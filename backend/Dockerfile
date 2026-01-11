FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway uses PORT env var)
EXPOSE 8000

# Run FastAPI with uvicorn (Railway injects PORT)
CMD uvicorn api:api --host 0.0.0.0 --port ${PORT:-8000}
