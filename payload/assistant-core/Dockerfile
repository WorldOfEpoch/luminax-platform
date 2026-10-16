FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y docker.io ffmpeg git curl nodejs npm && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -SL https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
RUN npm install -g @openai/codex && npm cache clean --force
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
