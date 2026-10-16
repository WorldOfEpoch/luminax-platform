FROM python:3.11-slim
WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py
RUN apt-get update && apt-get install -y docker.io curl procps systemd rsync && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -SL https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
COPY admin-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY admin-api/main.py .
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8040"]
