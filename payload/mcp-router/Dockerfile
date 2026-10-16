FROM python:3.11-slim

WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py

COPY mcp-router/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-router/main.py .

EXPOSE 8503

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8503"]
