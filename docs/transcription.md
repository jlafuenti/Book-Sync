# Transcription

A pair can only sync position between formats once its audiobook has been transcribed and the
transcript aligned to the ebook text. This page covers how that work is dispatched and what
governs how long it takes.

Deploying the remote worker itself is a separate walkthrough:
[jetson/README.md](../jetson/README.md).

## What happens to a job

1. A pair is queued (manually, or automatically when auto-transcribe is on).
2. [`services/queue_manager.py`](../server/services/queue_manager.py) claims **one item at a
   time** and hands the audio to the configured provider.
3. The provider returns text; NLTK splits it into sentences and the result is cached as an
   `AudioTranscript` so re-alignment never re-transcribes.
4. [`services/alignment.py`](../server/services/alignment.py) fuzzy-matches transcript sentences
   against EPUB sentences and writes the `SyncMap` / `SyncPoint` rows the clients read.

The transcript is editable afterwards (Transcription → editor) if the alignment came out poor.

Both the server and Jetson images bake NLTK's `punkt_tab` sentence tokenizer in at build time
(into `/usr/local/share/nltk_data`, named by `NLTK_DATA`), so a running container needs no
outbound network for step 3 or for the EPUB side of step 4 — only the build host does.

## Provider modes

`TRANSCRIPTION_PROVIDER` — env var for the first boot, then **System → Transcription Settings**
in the web UI, which is what the queue actually reads.

| Mode | Behavior |
|---|---|
| `remote` | Send every job to the worker at `TRANSCRIPTION_REMOTE_URL`. Fails if it's unreachable. |
| `local` | Run Whisper in the server container. Requires an image built with the local stack. |
| `remote_with_fallback` *(default)* | Try the remote worker, fall back to local Whisper. |

**The default server image has no local Whisper.** `torch` + `openai-whisper` CUDA wheels are
multi-GB, so they're opt-in at build time:

```bash
docker compose build --build-arg INSTALL_LOCAL_WHISPER=1
```

Without that, `remote_with_fallback` has nothing to fall back *to*, so it behaves as `remote`:
a remote outage is **retried** on the ladder below and, only if the ladder runs out, fails the
item with an error naming the remote worker. It does not enter the local leg — doing so raised
"Local Whisper isn't installed in this image", which failed the book on the first blip and named
the wrong component (issue #191). The server logs one warning at startup of each job when the
fallback leg is missing. Setting `TRANSCRIPTION_PROVIDER=remote` on a remote-only image is still
clearer, but it is no longer the difference between a retry and a dead book.

One remote failure is deliberately **not** retried: audio the worker cannot decode. That is a
corrupt or truncated source file, so the item fails immediately with "re-import required" rather
than re-uploading the same bytes five more times.

The remote worker requires a shared API key on every request. Generate it from
**System → Transcription Settings → Remote Server API Key** (the value is shown once) and paste
the same value into the worker's `TRANSCRIPTION_API_KEY`.

## Language

**System → Transcription Settings → Transcription Language**, stored as
`transcription_language` in the database. It applies to both providers.

Left on **Auto-detect** (the default, `""`), the transcriber detects the language from the
first chunk of each book and *pins that answer for the whole file*. It does not re-detect per
chunk, which is what it used to do and what made this a setting: a chunk that opens on music,
silence, or a foreign-language epigraph gets detected as another language and comes back as
transliterated garbage for that entire span — 15 minutes on the worker, an hour on the server.
Alignment then finds no matches there and interpolates across the gap, so the symptom is a
stretch of a book where the position sync is quietly wrong rather than an error anyone sees.

Pin an explicit language if your library is single-language; it skips detection altogether.
The accepted codes are the ISO 639-1 list in
[`services/transcription_providers/__init__.py`](../server/services/transcription_providers/__init__.py)
(`SUPPORTED_LANGUAGES`) — anything else is rejected with a 422 rather than saved and failed
hours later. The setting reaches the remote worker as a `language` form field on
`POST /v1/transcribe` (and in the resume body), so it needs no worker restart; the worker's own
`WHISPER_LANGUAGE` env var is only the default for jobs that arrive without one.

A paused job stores its pinned language in the checkpoint, so resuming continues in the same
language rather than re-detecting from wherever the resume happens to start.

## Queue behavior

- **Strictly serial.** One job runs at a time; the rest sit `pending`. Because of that, every
  wait the server does on the worker is bounded (#195): when a `409` says the worker is already
  running our file the server re-attaches and polls, and when it says a *different* file is
  running the server waits for it to go idle — both give up on the remote timeout, or after
  6 (re-attach) / 10 (wait-for-idle) consecutive unreachable polls, and hand the item to the
  retry ladder. Those poll failures are logged at WARNING with a counter. Before that, a worker
  that died inside either loop left the item `in_progress` forever and nothing else dispatched.
- **Cancellable at any point.** Pending, in-progress and paused items can all be cancelled. An
  in-progress item can't be *deleted* — cancel it first. Cancelling an in-progress job asks the
  worker to stop at its next chunk boundary and then discards the checkpoint and the audio it
  retained, so the GPU is handed back within one chunk instead of at the end of the book (#196).
  A transcription that finished anyway is **kept**: cancelling the sync does not throw away the
  transcript, and a later re-queue picks it up from the cache instead of re-transcribing.
  The stop is best effort — an unreachable worker still leaves the item cancelled, and the
  worker's own 48h sweep collects the leftovers.
  A cancel returns the pair to what it was before the job — `synced` for a cancelled
  re-transcription, otherwise `auto_matched` / `manual_matched` as before — using the status the
  pipeline recorded on the queue row when it claimed the pair (`pair_status_before`, #381). `error`
  is reserved for a job that actually failed.
- **Retries with a ceiling, on a backoff ladder.** A provider-unavailable failure re-pends the item
  and burns a retry; after the ceiling is reached the item is marked permanently failed with the
  error attached. The wait doubles each time — 30s, 60s, 120s, 240s, 480s — capped at 15 minutes,
  so the five default retries span roughly 15 minutes rather than 2.5. That is deliberately longer
  than a worker reboot plus a `medium` model load, which the old flat 30s ladder outran (#242).
  Both knobs are settings, for a worker that is flakier than that:

  | Setting | Default | Meaning |
  |---|---|---|
  | `transcription_retry_max` | `5` | Retries before the item fails permanently. |
  | `transcription_retry_base_seconds` | `30` | First delay; doubles per retry, capped at 900s. |

  Only *provider unavailable* (connection refused, read timeout, worker OOM) retries. A genuine
  transcription error is not retried — a corrupt file does not get better on the seventh try.
  The wait happens inline in the queue loop, so nothing else dispatches during it; that is fine,
  because the provider everything would dispatch to is the thing that is down.
- **Checkpointed.** The remote worker checkpoints between chunks, so a paused or resumed job picks
  up where it left off rather than restarting.

## Off-hours window

If the transcription GPU is shared with something latency-sensitive, restrict *dispatch* (not
queueing) to a time window: **System → Transcription Settings → Only transcribe during off-hours**.
Disabled by default; the defaults when enabled are 01:00–07:00 UTC.

Two things cooperate to make this safe mid-job: the queue loop refuses to claim new work outside
the window, and a watcher ticks independently so it notices the window closing *underneath* a
running job. When it does, the worker is asked to pause at its next checkpoint; the item returns
to `pending` with progress intact and **without burning a retry**, then resumes when the window
reopens. A "Run now" override on an individual item bypasses the window entirely.

Give the worker's checkpoint volume room for one audiobook — a paused job parks its source audio
there until it resumes, completes, is cancelled, or ages out after 48h.

## How long it takes

There are no published numbers here, because the honest answer is "measure it on your hardware".
The variables that actually move it:

- **Device.** GPU vs CPU is the dominant factor by a wide margin. `WHISPER_DEVICE=auto` picks a
  GPU when it sees one.
- **Model size.** `WHISPER_MODEL` (`tiny` → `large`) trades accuracy for speed at every step.
  `medium` is the default on both server and worker.
- **Audio length.** Cost is essentially linear in runtime — a 20-hour audiobook is 20× a one-hour
  one.
- **Contention.** With the off-hours window on, wall-clock time includes the hours the queue
  spends waiting for the window to open.

Time your first real book and extrapolate. The knob is **System → Transcription Settings → Remote
Timeout (s)** — a database setting, not an environment variable — and it defaults to 86400s (24 h).

Raise it if a book could take longer than that; lowering it does **not** make a dead worker fail
faster. `POST /v1/transcribe` is one blocking request that returns only when the whole book is
transcribed, so the timeout has to outlast the entire job: set it below your longest book and that
book fails partway through with nothing to show for it. The minimum accepted value is 60s.
