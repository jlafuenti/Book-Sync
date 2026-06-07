FROM python:3.11-slim

# System dependencies for Whisper (ffmpeg), PostgreSQL client, Calibre,
# curl (fetching the acsm-calibre-plugin release zip), and zip/unzip
# (patching the plugin in place before installing it).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq-dev \
    gcc \
    calibre \
    curl \
    ca-certificates \
    zip \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (includes audible + audible-cli for the
# Audible import source).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip "setuptools<71" wheel poetry-core && \
    pip install --no-cache-dir --no-build-isolation -r requirements.txt

# Install the acsm-calibre-plugin (DeACSM) so Calibre can convert .acsm
# files (Google Play Books / Nook downloads) into DRM-free EPUBs headlessly.
# The plugin runs through Calibre's standard `ebook-convert` (DeACSM is a
# file-type plugin that transforms .acsm at conversion time).
#
# v0.0.16 (2022) was released before Calibre moved to Python 3.11+, and
# libadobe.py:249 has a bytes/str concat bug that breaks anonymous and
# Adobe-ID authorization on every modern Calibre. We unpack the zip,
# patch that one line, repack, and then install. The patch is a no-op
# on the (str + str) path, so it stays correct if upstream ever fixes
# this and we bump the version pin.
ARG ACSM_PLUGIN_VERSION=0.0.16
RUN curl -L --fail -o /tmp/acsm-plugin.zip \
      https://github.com/Leseratte10/acsm-calibre-plugin/releases/download/v${ACSM_PLUGIN_VERSION}/DeACSM_${ACSM_PLUGIN_VERSION}.zip && \
    mkdir -p /tmp/acsm-unpacked && \
    cd /tmp/acsm-unpacked && \
    unzip -q /tmp/acsm-plugin.zip && \
    sed -i 's|str_to_hash = serial + devkey_bytes\.decode|str_to_hash = (serial.decode("latin-1") if isinstance(serial, bytes) else serial) + devkey_bytes.decode|' libadobe.py && \
    grep -q 'isinstance(serial, bytes)' libadobe.py || (echo "DeACSM patch did not apply" && exit 1) && \
    zip -qr /tmp/acsm-plugin-patched.zip . && \
    calibre-customize -a /tmp/acsm-plugin-patched.zip && \
    cd / && \
    rm -rf /tmp/acsm-plugin.zip /tmp/acsm-plugin-patched.zip /tmp/acsm-unpacked

# Install the DeDRM plugin (noDRM fork) so ACSM-fulfilled Adobe ADEPT EPUBs can
# actually be decrypted. DeACSM only *fulfills* — it downloads the still-
# encrypted book plus the license token; the AES/RSA decryption is a separate
# step that DeDRM performs (its ineptepub auto-discovers the DeACSM account
# key). Without this the pipeline stored encrypted EPUBs as if DRM-free.
ARG DEDRM_VERSION=10.0.3
RUN curl -L --fail -o /tmp/dedrm_tools.zip \
      https://github.com/noDRM/DeDRM_tools/releases/download/v${DEDRM_VERSION}/DeDRM_tools_${DEDRM_VERSION}.zip && \
    mkdir -p /tmp/dedrm-unpacked && \
    unzip -q /tmp/dedrm_tools.zip -d /tmp/dedrm-unpacked && \
    DEDRM_PLUGIN="$(find /tmp/dedrm-unpacked -name 'DeDRM_plugin.zip' | head -1)" && \
    test -n "$DEDRM_PLUGIN" && \
    calibre-customize -a "$DEDRM_PLUGIN" && \
    rm -rf /tmp/dedrm_tools.zip /tmp/dedrm-unpacked

# Copy application code
COPY . .

# Create data directories. /data/imports is the staging area for the ACSM
# watched-folder workflow (inbox/, processed/, failed/ subdirs are created
# at runtime by services/import_sources/acsm.py).
RUN mkdir -p /data/ebooks /data/audiobooks /data/app /data/imports

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
