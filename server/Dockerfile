# Matches CI (.github/workflows/tests.yml) and docs/testing.md — dev, CI and
# prod all run 3.12. This image was pinned to 3.11 while CI tested on 3.12
# (issue #52); ship what you test.
FROM python:3.12-slim

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
    gnupg \
    rsync \
    && rm -rf /var/lib/apt/lists/*

# PostgreSQL 16 client (pg_restore) for the UI-triggered restore (issue #60).
# The db service runs Postgres 16, so restoring its custom-format dumps needs a
# v16 client; Debian bookworm only ships v15, so pull v16 from the PGDG repo.
RUN install -d /usr/share/postgresql-common/pgdg && \
    curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list && \
    apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (includes audible + audible-cli for the
# Audible import source).
COPY requirements.txt requirements-local.txt ./
RUN pip install --no-cache-dir --upgrade pip "setuptools<71" wheel poetry-core && \
    pip install --no-cache-dir --no-build-isolation -r requirements.txt

# Local (server-side) Whisper — torch + openai-whisper — is opt-in because torch
# pulls multi-GB nvidia-cu* CUDA wheels that are pure build cost when
# transcription is offloaded to a remote/Jetson worker. Default build skips them.
# Enable with:  docker compose build --build-arg INSTALL_LOCAL_WHISPER=1
ARG INSTALL_LOCAL_WHISPER=false
RUN if [ "$INSTALL_LOCAL_WHISPER" = "true" ] || [ "$INSTALL_LOCAL_WHISPER" = "1" ]; then \
        pip install --no-cache-dir -r requirements-local.txt ; \
    else \
        echo "Skipping local Whisper (torch/openai-whisper) — remote-transcription-only image." ; \
    fi

# DRM plugins (DeACSM + DeDRM) are opt-in and NOT installed in the default
# image — see docs/import-sources.md. Enable only if this deployment uses the
# ACSM (Adobe ADEPT) or Audible import sources, with:
#   docker compose build --build-arg INSTALL_DRM_PLUGINS=1
ARG INSTALL_DRM_PLUGINS=false

# acsm-calibre-plugin (DeACSM) lets Calibre convert .acsm files (Google Play
# Books / Nook downloads) into EPUBs headlessly. The plugin runs through
# Calibre's standard `ebook-convert` (DeACSM is a file-type plugin that
# transforms .acsm at conversion time).
#
# v0.0.16 (2022) was released before Calibre moved to Python 3.11+, and
# libadobe.py:249 has a bytes/str concat bug that breaks anonymous and
# Adobe-ID authorization on every modern Calibre. We unpack the zip,
# patch that one line, repack, and then install. The patch is a no-op
# on the (str + str) path, so it stays correct if upstream ever fixes
# this and we bump the version pin.
ARG ACSM_PLUGIN_VERSION=0.0.16
RUN if [ "$INSTALL_DRM_PLUGINS" = "true" ] || [ "$INSTALL_DRM_PLUGINS" = "1" ]; then \
        curl -L --fail -o /tmp/acsm-plugin.zip \
          https://github.com/Leseratte10/acsm-calibre-plugin/releases/download/v${ACSM_PLUGIN_VERSION}/DeACSM_${ACSM_PLUGIN_VERSION}.zip && \
        mkdir -p /tmp/acsm-unpacked && \
        cd /tmp/acsm-unpacked && \
        unzip -q /tmp/acsm-plugin.zip && \
        sed -i 's|str_to_hash = serial + devkey_bytes\.decode|str_to_hash = (serial.decode("latin-1") if isinstance(serial, bytes) else serial) + devkey_bytes.decode|' libadobe.py && \
        { grep -q 'isinstance(serial, bytes)' libadobe.py || (echo "DeACSM patch did not apply" && exit 1) ; } && \
        zip -qr /tmp/acsm-plugin-patched.zip . && \
        calibre-customize -a /tmp/acsm-plugin-patched.zip && \
        cd / && \
        rm -rf /tmp/acsm-plugin.zip /tmp/acsm-plugin-patched.zip /tmp/acsm-unpacked ; \
    else \
        echo "Skipping DeACSM plugin — default image ships no DRM tooling." ; \
    fi

# DeDRM plugin (noDRM fork): DeACSM only *fulfills* — it downloads the still-
# encrypted book plus the license token; the AES/RSA decryption is a separate
# step that DeDRM performs (its ineptepub auto-discovers the DeACSM account
# key). Without this the pipeline stored encrypted EPUBs as if DRM-free.
ARG DEDRM_VERSION=10.0.3
RUN if [ "$INSTALL_DRM_PLUGINS" = "true" ] || [ "$INSTALL_DRM_PLUGINS" = "1" ]; then \
        curl -L --fail -o /tmp/dedrm_tools.zip \
          https://github.com/noDRM/DeDRM_tools/releases/download/v${DEDRM_VERSION}/DeDRM_tools_${DEDRM_VERSION}.zip && \
        mkdir -p /tmp/dedrm-unpacked && \
        unzip -q /tmp/dedrm_tools.zip -d /tmp/dedrm-unpacked && \
        DEDRM_PLUGIN="$(find /tmp/dedrm-unpacked -name 'DeDRM_plugin.zip' | head -1)" && \
        test -n "$DEDRM_PLUGIN" && \
        calibre-customize -a "$DEDRM_PLUGIN" && \
        rm -rf /tmp/dedrm_tools.zip /tmp/dedrm-unpacked ; \
    else \
        echo "Skipping DeDRM plugin — default image ships no DRM tooling." ; \
    fi

# Copy application code
COPY . .

# Create data directories. /data/imports is the staging area for the ACSM
# watched-folder workflow (inbox/, processed/, failed/ subdirs are created
# at runtime by services/import_sources/acsm.py).
RUN mkdir -p /data/ebooks /data/audiobooks /data/app /data/imports

EXPOSE 8000

# Run migrations (alembic upgrade head) then uvicorn. Invoked via `sh` so it
# doesn't depend on the file's executable bit surviving a Windows checkout.
CMD ["sh", "entrypoint.sh"]
