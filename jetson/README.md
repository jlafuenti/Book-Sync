# Deploying the Jetson transcription worker

`jetson/` runs a standalone faster-whisper transcription server on a Jetson Orin Nano (or similar).
It's a separate host from the main BookSync stack, so it gets its own clone — nothing else in this
repo needs to be present on the Jetson. The compose file lives in this directory, so deploy
commands run from inside `jetson/` with no `-f` flag needed.

## Prerequisites

- JetPack with the NVIDIA Container Runtime installed (this is what provides the `nvidia` Docker
  runtime the compose file requests — it ships with JetPack, no extra setup needed on a stock
  Jetson image).
- Docker + Docker Compose.
- Network reachability between the Jetson and the machine running the main BookSync server (same
  LAN/VPN is typical — port 9000 does **not** need to be reachable from the public internet).

## 1. Sparse clone

The Jetson only needs this directory — not `server/`, `web/`, or `android/`:

```bash
git clone --filter=blob:none --sparse https://github.com/jlafuenti/Book-Sync.git booksync-jetson
cd booksync-jetson
git sparse-checkout set jetson
cd jetson
```

## 2. Copy the compose template

`docker-compose.yml` (in this directory) is gitignored — it will carry a real secret in the next
step, so copying it once means future `git pull`s never conflict with your local edits:

```bash
cp docker-compose.example.yml docker-compose.yml
```

## 3. Generate the shared API key

The transcription server refuses to start without `TRANSCRIPTION_API_KEY` set — every request to
it (including the health check) must present this key as a bearer token, since anyone who can
reach port 9000 would otherwise be able to submit jobs or read other users' cached transcripts.

**Preferred: generate it from the BookSync UI.** On the main server, go to
**Settings → Transcription → Remote Server API Key** and click **"Generate Key"**. This creates a
random key, saves it there immediately, and shows you the value once — copy it.

**Fallback (if the main server isn't reachable yet):**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste your generated value into both places — it's compared byte-for-byte, so they must match
exactly:

```yaml
# jetson/docker-compose.yml
environment:
  - TRANSCRIPTION_API_KEY=<paste-here>
```

and BookSync → Settings → Transcription → Remote Server API Key (if you used the CLI fallback,
paste it into that field and click Save).

## 4. Deploy

From inside `jetson/`:

```bash
docker compose up -d --build
```

First boot downloads the whisper model (persisted in the `whisper_models` volume across
restarts), so it can take a few minutes.

## 5. Point the main server at it

BookSync → Settings → Transcription:
- **Remote URL**: `http://<orin-lan-ip>:9000`
- **Remote Server API Key**: the same value from step 3 (already filled in if you generated it
  from the UI).
- Click **Test Connection** — it should report the GPU and model status. A `401`/authentication
  error here means the two keys don't match. **"Model: Not Loaded" is normal** — see below.

## Sharing the GPU (issue #106)

This worker is built to coexist with another GPU tenant (e.g. Home Assistant's voice
pipeline) on the Orin's 8GB of unified memory:

- **The model loads on the first job, not at startup**, and is released again after
  `MODEL_IDLE_UNLOAD_MIN` idle minutes (default 30; `0` keeps it resident forever, the
  pre-#106 behaviour). An idle worker therefore reports `model_state: "unloaded"` on
  `/v1/health` and holds no GPU memory. That is healthy, not broken — the main server's
  availability check looks at `status`, not `model_loaded`.
- **A running job can be paused at a chunk boundary.** BookSync posts `/v1/pause` when its
  off-hours window closes; the worker finishes the chunk it's on (≤ ~15 minutes of audio),
  writes a checkpoint, parks the source audio next to it, unloads the model, and answers
  the transcription request with `{"status": "paused", ...}` instead of a transcript.
- **Resuming costs no upload.** `POST /v1/transcribe/resume` continues from the retained
  audio; BookSync falls back to a normal upload if the file has been swept. Either way the
  checkpoint means no audio is transcribed twice.
- Nothing partial is ever served from `/v1/result/{filename}` — a truncated transcript is
  indistinguishable from a complete one to the client, so paused jobs stay out of that cache.

Turn the schedule itself on in BookSync → Settings → Transcription → **Only transcribe
during off-hours**.

To watch the memory actually come back:

```bash
watch -n 5 'free -h; curl -s -H "Authorization: Bearer $TRANSCRIPTION_API_KEY" localhost:9000/v1/health | jq .model_state'
```

## Troubleshooting

- **Container exits immediately on startup**: `TRANSCRIPTION_API_KEY` is unset or blank in
  `jetson/docker-compose.yml` — the server refuses to start rather than run unauthenticated. Check
  `docker compose logs transcriber` (from inside `jetson/`) for the exact message.
- **Test Connection / transcriptions fail with 401**: the key in `jetson/docker-compose.yml`
  doesn't match what's saved in BookSync → Settings → Transcription. Regenerate from the UI and
  copy it into the compose file again (then `docker compose up -d` to pick up the new env var).
- **`up` fails with "container name ... already in use"**: an older deployment (e.g. from before
  this file lived in `jetson/`, or from a differently-named checkout directory) is still running
  under the fixed `container_name: booksync_transcriber`. The template pins `name:
  booksync-transcriber` at the top so the project/volume names no longer depend on the checkout
  directory, but you still need to retire the old container by hand:
  ```bash
  docker rm -f booksync_transcriber   # stop/remove the stale container holding the name
  docker volume ls | grep whisper_models   # confirm which volume actually has your downloaded model
  docker compose up -d --build   # now reuses booksync-transcriber_whisper_models, no re-download
  ```
  If `docker compose up` had already run once before you fixed the name conflict, it may have
  created empty `<old-dirname>_whisper_models` / `<old-dirname>_booksync_checkpoints` volumes —
  safe to `docker volume rm` those once the real deployment is confirmed working.
