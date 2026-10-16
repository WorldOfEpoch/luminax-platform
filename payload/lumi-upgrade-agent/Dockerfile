FROM python:3.11-slim
WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py
RUN apt-get update && apt-get install -y git curl docker.io procps nodejs npm && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -SL https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
RUN npm install -g @openai/codex && npm cache clean --force
COPY lumi-upgrade-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lumi-upgrade-agent/main.py .
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8130"]
