# Jetson Orin Nano — faster-whisper transcription server
# Uses dustynv's JetPack 6-compatible base image specifically compiled with CTranslate2 CUDA support for ARM64
FROM dustynv/faster-whisper:r36.2.0

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Download NLTK sentence tokenizer data at build time
RUN python3 -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Copy application code
COPY server.py .

EXPOSE 9000

# Run with uvicorn; single worker since we process one job at a time
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
