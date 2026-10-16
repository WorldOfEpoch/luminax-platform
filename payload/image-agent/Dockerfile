FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

WORKDIR /app
COPY service-runtime/runtime_guard.py /app/runtime_guard.py

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/data/hf
ENV TRANSFORMERS_CACHE=/data/hf
ENV HUGGINGFACE_HUB_CACHE=/data/hf

COPY image-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY image-agent/main.py .

EXPOSE 8055

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8055"]
