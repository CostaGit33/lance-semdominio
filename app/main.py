import subprocess
import os
import time
import glob
import threading
import signal
import sys
import json
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ───────────────── CONFIG ─────────────────
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

CAMERA_NAME      = config.get("camera_name", "Integrated Camera")
SEGMENT_DURATION = int(config.get("segment_duration", 5))
BUFFER_SEGMENTS  = int(config.get("buffer_segments", 8))
CLIP_DURATION    = int(config.get("clip_duration", 30))
DELAY_SECONDS    = int(config.get("delay_seconds", 3))
SERVER_PORT      = int(config.get("server_port", 8080))

BASE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
OUTPUT_DIR = os.path.join(BASE_DIR, "clips")
BUFFER_DIR = os.path.join(BASE_DIR, "buffer")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(BUFFER_DIR, exist_ok=True)

capture_process = None
running = True

# ───────────────── CAMERA INPUT ─────────────────
def get_camera_input():
    if sys.platform == "darwin":
        return ["-f", "avfoundation", "-i", "0:none"]
    elif sys.platform.startswith("linux"):
        return ["-f", "v4l2", "-i", "/dev/video0"]
    else:
        return [
            "-rtbufsize", "200M",
            "-f", "dshow",
            "-i", f"video={CAMERA_NAME}"
        ]

# ───────────────── CAPTURA ─────────────────
def start_buffer_recording():
    global capture_process

    input_args = get_camera_input()

    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        *input_args,
        "-an",
        "-c:v", "mjpeg",  # 🔥 mais compatível que copy
        "-q:v", "5",
        "-f", "segment",
        "-segment_time", str(SEGMENT_DURATION),
        "-reset_timestamps", "1",
        "-strftime", "1",
        os.path.join(BUFFER_DIR, "seg_%Y%m%d_%H%M%S.mjpeg")
    ]

    print(f"[CAPTURA] Câmera: {CAMERA_NAME}")

    try:
        capture_process = subprocess.Popen(cmd)
    except Exception as e:
        print("[ERRO] Falha ao iniciar FFmpeg:", e)

    return capture_process

# ───────────────── CLIP ─────────────────
def save_clip():
    segments = sorted(
        glob.glob(os.path.join(BUFFER_DIR, "seg_*.mjpeg")),
        key=os.path.getmtime
    )

    if not segments:
        print("[ERRO] Sem segmentos.")
        return None

    total_needed = CLIP_DURATION + DELAY_SECONDS
    needed = max(1, total_needed // SEGMENT_DURATION)
    selected = segments[-needed:]

    clip_name = datetime.now().strftime("clip_%Y%m%d_%H%M%S")
    clip_path = os.path.join(OUTPUT_DIR, f"{clip_name}.mp4")

    list_file = os.path.join(BUFFER_DIR, "concat.txt")

    with open(list_file, "w", encoding="utf-8") as f:
        for seg in selected:
            safe = seg.replace("\\", "/")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-y",
        clip_path
    ]

    print(f"[CLIP] Gerando {clip_name}...")

    try:
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            print("[CLIP] ✅ Sucesso")
            return clip_path
        else:
            print("[ERRO] FFmpeg:", result.stderr.decode())
            return None
    except Exception as e:
        print("[ERRO] Execução FFmpeg:", e)
        return None

# ───────────────── LIMPEZA ─────────────────
def cleanup_old_segments():
    while running:
        try:
            segments = sorted(
                glob.glob(os.path.join(BUFFER_DIR, "seg_*.mjpeg")),
                key=os.path.getmtime
            )

            while len(segments) > BUFFER_SEGMENTS:
                old = segments.pop(0)
                try:
                    os.remove(old)
                except:
                    pass
        except:
            pass

        time.sleep(SEGMENT_DURATION)

# ───────────────── HTTP API ─────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        return

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path == "/api/status":
                segs = glob.glob(os.path.join(BUFFER_DIR, "seg_*.mjpeg"))
                self._json({
                    "recording": capture_process and capture_process.poll() is None,
                    "segments": len(segs),
                    "buffer_seconds": len(segs) * SEGMENT_DURATION
                })

            elif self.path == "/api/clips":
                files = sorted(
                    glob.glob(os.path.join(OUTPUT_DIR, "*.mp4")),
                    key=os.path.getmtime,
                    reverse=True
                )

                clips = [{
                    "name": os.path.basename(f),
                    "url": f"/clips/{os.path.basename(f)}"
                } for f in files]

                self._json(clips)

            elif self.path.startswith("/clips/"):
                filename = self.path.replace("/clips/", "")
                filepath = os.path.join(OUTPUT_DIR, filename)

                if os.path.exists(filepath):
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self._cors()
                    self.end_headers()

                    with open(filepath, "rb") as f:
                        while chunk := f.read(1024 * 1024):
                            self.wfile.write(chunk)
                else:
                    self.send_error(404)

            else:
                self.send_error(404)

        except Exception as e:
            print("[ERRO API]", e)
            self.send_error(500)

    def do_POST(self):
        try:
            if self.path == "/api/trigger":
                clip = save_clip()

                if clip:
                    self._json({"success": True})
                else:
                    self._json({"success": False}, 500)
            else:
                self.send_error(404)

        except Exception as e:
            print("[ERRO POST]", e)
            self.send_error(500)

# ───────────────── FINALIZAÇÃO ─────────────────
def shutdown(sig, frame):
    global running
    print("\nEncerrando...")
    running = False

    if capture_process:
        capture_process.terminate()

    sys.exit(0)

# ───────────────── MAIN ─────────────────
if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)

    print("=== LANCE SEM DOMINIO ===")
    print(f"Porta: {SERVER_PORT}")

    threading.Thread(target=cleanup_old_segments, daemon=True).start()

    start_buffer_recording()

    server = HTTPServer(("0.0.0.0", SERVER_PORT), Handler)

    print(f"Rodando em http://0.0.0.0:{SERVER_PORT}")
    server.serve_forever()