FROM python:3.11-slim

WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py

COPY luminax-brain/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY luminax-brain/main.py .

RUN mkdir -p /data/memory/luminax_brain

EXPOSE 8501

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8501"]
