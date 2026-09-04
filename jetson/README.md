# Deploying the Jetson transcription worker

`jetson/` runs a standalone faster-whisper transcription server on a Jetson Orin Nano (or similar).
It's a separate host from the main Tandem stack, so it gets its own clone — nothing else in this
repo needs to be present on the Jetson. The compose file lives in this directory, so deploy
commands run from inside `jetson/` with no `-f` flag needed.

## Prerequisites

- JetPack with the NVIDIA Container Runtime installed (this is what provides the `nvidia` Docker
  runtime the compose file requests — it ships with JetPack, no extra setup needed on a stock
  Jetson image).
- Docker + Docker Compose.
- Network reachability between the Jetson and the machine running the main Tandem server (same
  LAN/VPN is typical — port 9000 does **not** need to be reachable from the public internet).
  **It must not be.** This worker speaks plaintext HTTP and authenticates with a shared bearer
  token, so every request puts that token on the wire in the clear. The compose template's
  `ports: - "9000:9000"` publishes on every interface; bind the LAN address explicitly
  (`- "<lan-ip>:9000:9000"`) or front it with TLS (Caddy, Tailscale) if it has to cross networks.

### Host prep (JetPack)

Optional on a healthy stock image, but both of these materially affect long transcriptions on an
Orin Nano:

- **Max power mode**, for the GPU clocks:

  ```bash
  sudo nvpmodel -m 0
  sudo jetson_clocks
  ```

- **An 8 GB swap file.** The `medium` model plus the Orin Nano's unified memory is tight; without
  swap, long jobs can OOM mid-transcription:

  ```bash
  sudo fallocate -l 8G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab   # make it permanent
  ```

If `docker compose up` fails with an unknown runtime `nvidia`, the NVIDIA container runtime is not
wired in as expected on your image. Add it to `/etc/docker/daemon.json` and
`sudo systemctl restart docker`:

```json
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": { "path": "nvidia-container-runtime", "runtimeArgs": [] }
  }
}
```

### Monitoring the hardware

`jtop` is the practical way to watch GPU memory and temperature during a multi-hour audiobook:

```bash
sudo pip3 install -U jetson-stats
sudo systemctl restart jetson_stats.service
jtop
```

## 1. Sparse clone

The Jetson only needs this directory — not `server/`, `web/`, or `android/`:

```bash
git clone --filter=blob:none --sparse https://github.com/jlafuenti/Book-Sync.git tandem-jetson
cd tandem-jetson
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

**Preferred: generate it from the Tandem UI.** On the main server, go to
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

and Tandem → Settings → Transcription → Remote Server API Key (if you used the CLI fallback,
paste it into that field and click Save).

## 4. Deploy

From inside `jetson/`:

```bash
docker compose up -d --build
```

First boot downloads the whisper model (persisted in the `whisper_models` volume across
restarts), so it can take a few minutes.

## 5. Point the main server at it

Tandem → Settings → Transcription:
- **Remote URL**: `http://<orin-lan-ip>:9000`
- **Remote Server API Key**: the same value from step 3 (already filled in if you generated it
  from the UI).
- Click **Test Connection** — it should report the GPU and model status. A `401`/authentication
  error here means the two keys don't match. **"Model: Not Loaded" is normal** — see below.

## Worker environment variables

All set in `jetson/docker-compose.yml` (copied from the template in step 2). Only
`TRANSCRIPTION_API_KEY` is mandatory.

| Variable | Default | Meaning |
|---|---|---|
| `TRANSCRIPTION_API_KEY` | *(none)* | Shared bearer secret required on every `/v1/*` request. The container refuses to start without it. |
| `WHISPER_MODEL` | `medium` | faster-whisper model size (`tiny` → `large-v3`). |
| `WHISPER_COMPUTE_TYPE` | `float16` | CTranslate2 compute type. |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu`. |
| `WHISPER_LANGUAGE` | *(unset = auto)* | ISO 639-1 code forced on every chunk. Unset means detect once per file and pin that for the rest of it — never per-chunk re-detection. A `language` form field on `POST /v1/transcribe` overrides this per job, which is what Tandem's **Transcription Language** setting sends. |
| `VAD_FILTER` | `true` | Voice-activity filtering before transcription. |
| `MODEL_IDLE_UNLOAD_MIN` | `30` | Idle minutes before the weights are released; `0` keeps them resident. |
| `MAX_UPLOAD_BYTES` | `4294967296` (4 GiB) | Largest accepted upload. Over it, `POST /v1/transcribe` answers 413 before reading the body; `0` disables the cap. |
| `TMPDIR` | `/tmp/booksync_checkpoints/tmp` | Where the spooled upload and its copy land. Keep it on the sized checkpoint volume — the default container `/tmp` is not. |
| `SERVER_PORT` | `9000` | Listen port inside the container. |

An upload costs roughly **twice** the file size in temp space (Starlette spools the multipart
body to disk, then the endpoint copies it), so size the `booksync_checkpoints` volume for two
copies of your largest audiobook plus the one a paused job parks there. When free space is
short the worker answers `507` instead of filling the disk, and Tandem surfaces that as
"remote worker is out of disk" and retries rather than failing the book outright. A request
whose `Content-Length` is over `MAX_UPLOAD_BYTES` gets a `413` before the body is read, and
Tandem treats that as permanent rather than re-uploading the same file five times.

Uploads clean themselves up, but a hard stop mid-upload (an OoM-kill, a power cut) can leave
one behind; anything older than 48h in `TMPDIR` is swept at startup, and only when `TMPDIR` is
inside the checkpoint volume — the worker never sweeps a system temp directory it might share.

## Sharing the GPU (issue #106)

This worker is built to coexist with another GPU tenant (e.g. Home Assistant's voice
pipeline) on the Orin's 8GB of unified memory:

- **The model loads on the first job, not at startup**, and is released again after
  `MODEL_IDLE_UNLOAD_MIN` idle minutes (default 30; `0` keeps it resident forever, the
  pre-#106 behaviour). An idle worker therefore reports `model_state: "unloaded"` on
  `/v1/health` and holds no GPU memory. That is healthy, not broken — the main server's
  availability check looks at `status`, not `model_loaded`.
- **A running job can be paused at a chunk boundary.** Tandem posts `/v1/pause` when its
  off-hours window closes; the worker finishes the chunk it's on (≤ ~15 minutes of audio),
  writes a checkpoint, parks the source audio next to it, unloads the model, and answers
  the transcription request with `{"status": "paused", ...}` instead of a transcript.
- **Resuming costs no upload.** `POST /v1/transcribe/resume` continues from the retained
  audio; Tandem falls back to a normal upload if the file has been swept. Either way the
  checkpoint means no audio is transcribed twice.
- Nothing partial is ever served from `/v1/result/{filename}` — a truncated transcript is
  indistinguishable from a complete one to the client, so paused jobs stay out of that cache.

Turn the schedule itself on in Tandem → Settings → Transcription → **Only transcribe
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
  doesn't match what's saved in Tandem → Settings → Transcription. Regenerate from the UI and
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
