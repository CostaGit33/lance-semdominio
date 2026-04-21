FROM python:3.12-slim

# Instalar ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Criar diretório da app
WORKDIR /app

# Copiar arquivos
COPY . .

# Instalar dependências (se houver futuramente)
# RUN pip install -r requirements.txt

# Expor porta
EXPOSE 8080

# Rodar aplicação
CMD ["python", "app/main.py"]