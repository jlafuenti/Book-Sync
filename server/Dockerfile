FROM python:3.11-slim

# System dependencies for Whisper (ffmpeg), PostgreSQL client, Calibre,
# and curl (used to fetch the acsm-calibre-plugin release zip below).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq-dev \
    gcc \
    calibre \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (includes audible + audible-cli for the
# Audible import source).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip "setuptools<71" wheel poetry-core && \
    pip install --no-cache-dir --no-build-isolation -r requirements.txt

# Install the acsm-calibre-plugin (DeACSM) so Calibre can convert .acsm
# files (Google Play Books / Nook downloads) into DRM-free EPUBs headlessly.
# The plugin runs via `calibre-debug -r DeACSM -- file.acsm`, invoked from
# services/import_sources/acsm.py. Version pinned to the most recent
# release; the project releases infrequently because ADEPT itself is stable.
ARG ACSM_PLUGIN_VERSION=0.0.16
RUN curl -L --fail -o /tmp/acsm-plugin.zip \
      https://github.com/Leseratte10/acsm-calibre-plugin/releases/download/v${ACSM_PLUGIN_VERSION}/DeACSM_${ACSM_PLUGIN_VERSION}.zip && \
    calibre-customize -a /tmp/acsm-plugin.zip && \
    rm /tmp/acsm-plugin.zip

# Copy application code
COPY . .

# Create data directories. /data/imports is the staging area for the ACSM
# watched-folder workflow (inbox/, processed/, failed/ subdirs are created
# at runtime by services/import_sources/acsm.py).
RUN mkdir -p /data/ebooks /data/audiobooks /data/app /data/imports

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
