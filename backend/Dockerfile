FROM python:3.11-slim

WORKDIR /app

# Python 출력 버퍼링 비활성화
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway uses PORT env var)
EXPOSE 8000

# Run FastAPI with uvicorn (Railway injects PORT)
CMD ["sh", "-c", "echo '[Docker] Starting uvicorn...' && exec uvicorn api:api --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
