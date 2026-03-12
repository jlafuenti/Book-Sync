# Jetson Orin Nano — faster-whisper transcription server
# Uses dustynv's pre-built faster-whisper image which includes
# CTranslate2 compiled from source with CUDA support for ARM64.
FROM dustynv/faster-whisper:r36.4.0-cu128-24.04

WORKDIR /app

# Install ffmpeg/ffprobe (needed for audio chunking to avoid OOM on large files)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Install remaining Python dependencies (faster-whisper is already in the base image)
COPY requirements.txt .
# Force use of standard PyPI. The base image configures pip to use a
# jetson-ai-lab proxy that is sometimes offline / fails DNS.
RUN pip3 install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt

# Download NLTK sentence tokenizer data at build time
RUN python3 -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Copy application code
COPY server.py .

EXPOSE 9000

# Run with uvicorn; single worker since we process one job at a time
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
