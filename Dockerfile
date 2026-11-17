# Use official slim Python 3.11 runtime
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user and group
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code, data, scripts, and local model assets
COPY app/ ./app/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY models/ ./models/

# Ingest dummy documents into ChromaDB so vector store is ready
RUN python -m app.rag.ingest

# Transfer file ownership to unprivileged user
RUN chown -R appuser:appgroup /app

# Run container as unprivileged non-root user
USER appuser

# Expose HTTP port
EXPOSE 8000

# Run FastAPI via production Uvicorn server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
