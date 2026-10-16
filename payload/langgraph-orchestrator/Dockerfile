FROM python:3.11-slim

WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py

COPY langgraph-orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY langgraph-orchestrator/main.py .

EXPOSE 8504

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8504"]
