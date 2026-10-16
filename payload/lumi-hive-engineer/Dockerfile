FROM python:3.11-slim
WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py
COPY lumi-hive-engineer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lumi-hive-engineer/main.py .
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8102"]
