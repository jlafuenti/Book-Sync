FROM python:3.11-slim

# System dependencies for Whisper (ffmpeg) and PostgreSQL client
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --no-build-isolation -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /data/ebooks /data/audiobooks /data/app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
