# Jetson Orin Nano — faster-whisper transcription server
# Uses dustynv's JetPack 6-compatible base image with PyTorch and CUDA
FROM dustynv/whisper:r36.2.0

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
# We pull faster-whisper (and its ctranslate2 dependency) specifically from 
# the dusty-nv repo which has it pre-compiled with GPU/CUDA support for ARM64 JetPack 6.
RUN pip3 install --no-cache-dir --verbose --extra-index-url https://dusty-nv.github.io/jetson-containers faster-whisper && \
    pip3 install --no-cache-dir -r requirements.txt

# Download NLTK sentence tokenizer data at build time
RUN python3 -c "import nltk; nltk.download('punkt_tab', quiet=True)"

# Copy application code
COPY server.py .

EXPOSE 9000

# Run with uvicorn; single worker since we process one job at a time
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
