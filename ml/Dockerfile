FROM python:3.12-slim
WORKDIR /app

# CPU-сборка torch: без CUDA образ меньше на несколько ГБ
RUN pip install --no-cache-dir torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY demo.py .

# Один образ на три роли: API (по умолчанию), Kafka-консьюмер настроения, ingest базы знаний
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
