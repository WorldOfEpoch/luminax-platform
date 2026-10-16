FROM mcr.microsoft.com/playwright/python:v1.52.0-noble
WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py
COPY browser-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY browser-agent/main.py .
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8010"]
