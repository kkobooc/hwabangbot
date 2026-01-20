FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# ETL Pipeline: refresh keys → ingest → embed (fallback, Railway uses startCommand)
CMD ["sh", "-c", "echo '[ETL] Starting pipeline...' && python -u storybook_refresh.py --mall-id ${MALL_ID} && python -u storybook_ingest.py --mall-id ${MALL_ID} && python -u embedding_sync.py && echo '[ETL] Completed'"]
