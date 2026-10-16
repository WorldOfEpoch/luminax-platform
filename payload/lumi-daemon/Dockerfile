FROM python:3.11-slim
WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py
RUN apt-get update && apt-get install -y docker.io curl procps && rm -rf /var/lib/apt/lists/*
COPY lumi-daemon/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lumi-daemon/daemon.py .
CMD ["python", "daemon.py"]
