FROM python:3.11-slim
WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py
RUN apt-get update && apt-get install -y iproute2 iputils-ping net-tools arp-scan nmap && rm -rf /var/lib/apt/lists/*
COPY network-manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY network-manager/main.py .
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8090"]
