FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# ETL Pipeline: ingest → process → embed
CMD python storybook_ingest.py --mall-id ${MALL_ID} && python embedding_sync.py
