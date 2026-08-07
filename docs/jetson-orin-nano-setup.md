# Book Sync: Jetson Orin Nano Transcription Server Setup

This guide walks through configuring a Jetson Orin Nano Developer Kit to act as a dedicated, hardware-accelerated transcription server for Book Sync using `faster-whisper`.

## 1. Prerequisites
- **JetPack 6.x** flashed onto the Jetson Orin Nano.
- **Max Power Mode** enabled for best GPU performance.
  ```bash
  sudo nvpmodel -m 0
  sudo jetson_clocks
  ```
- **8GB Swap Space** (highly recommended for the `medium` Whisper model to prevent OOM errors):
  ```bash
  # Create swap file
  sudo fallocate -l 8G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  
  # Make it permanent
  echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
  ```

## 2. Docker & NVIDIA Container Toolkit
Ensure Docker and the NVIDIA Container Toolkit are installed so containers can access the GPU.

1. Install Docker:
   ```bash
   sudo apt-get update
   sudo apt-get install curl docker.io
   ```
2. Install NVIDIA Container Toolkit:
   *(Usually pre-installed on recent JetPack versions, but if needed, install via apt)*
3. Configure `daemon.json` to ensure the default runtime is `nvidia`:
   ```json
   // /etc/docker/daemon.json
   {
     "default-runtime": "nvidia",
     "runtimes": {
       "nvidia": {
         "path": "nvidia-container-runtime",
         "runtimeArgs": []
       }
     }
   }
   ```
4. Restart Docker:
   ```bash
   sudo systemctl restart docker
   ```

## 3. Server Setup (`docker-compose.yml`)
Create a new directory on the Jetson (e.g., `~/booksync-transcriber`) and add the following files to run the `faster-whisper` API server.

**Note:** The API server source code (Task 2) will need to be present in the `./jetson` directory relative to this docker-compose file. We use `dustynv/whisper:r36.2.0` as the base image, as it comes pre-compiled with PyTorch and CUDA bindings specifically optimized for JetPack 6.x.

```yaml
# docker-compose.yml
services:
  transcriber:
    build: 
      context: ./jetson
      dockerfile: Dockerfile
    container_name: booksync_transcriber
    runtime: nvidia # critical for GPU access
    restart: unless-stopped
    ports:
      - "9000:9000"
    volumes:
      - ./models:/root/.cache/huggingface/hub # Cache models across restarts
    environment:
      - WHISPER_MODEL=medium
      - WHISPER_COMPUTE_TYPE=float16
      - WHISPER_DEVICE=cuda
      - VAD_FILTER=true # Reduces hallucinations on silence
      - MODEL_IDLE_UNLOAD_MIN=30 # Release the model after 30 idle minutes (0 = never)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

## 4. Run the Server
1. Bring the container up. The first run will download the model files to the `./models` directory.
   ```bash
   docker-compose up -d
   ```
2. Monitor the logs:
   ```bash
   docker-compose logs -f
   ```
   Startup no longer loads the model — you should see
   `Model will be loaded on demand and released after 30 idle minutes`. The
   `float16`/`cuda` load line appears when the first transcription arrives.

## 5. Network Configuration
Ensure the Jetson Orin Nano has a static IP address on your local network (e.g., via your router's DHCP reservation).
Configure the Book Sync server to point to this Jetson API.
In your Book Sync `.env`:
```env
TRANSCRIPTION_PROVIDER=remote_with_fallback
TRANSCRIPTION_REMOTE_URL=http://<JETSON-IP-ADDRESS>:9000
```

## 6. Verification
You can test the API manually from any machine on your network (like the server running Book Sync):
```bash
# 1. Health check
curl http://<JETSON-IP-ADDRESS>:9000/v1/health

# 2. Transcription test (replace test.mp3 with a valid file)
curl -F "audio_file=@test.mp3" http://<JETSON-IP-ADDRESS>:9000/v1/transcribe
```

## 7. Sharing the GPU with another service

If this Orin also runs something latency-sensitive (a voice assistant's STT, say),
transcription can be confined to a nightly window — see the "Sharing the GPU" section
of `jetson/README.md` and BookSync → Settings → Transcription → **Only transcribe during
off-hours**. Two mechanics matter operationally:

- The worker holds **no** GPU memory while idle (`MODEL_IDLE_UNLOAD_MIN`), so
  `/v1/health` reporting `model_state: "unloaded"` is the healthy resting state.
- A job still running when the window closes pauses at its next chunk boundary and
  resumes from that checkpoint next window. The paused job's source audio is parked in
  `/tmp/booksync_checkpoints` until it resumes, completes, is cancelled, or ages out
  after 48 hours — budget roughly one audiobook of disk there.

## 8. Monitoring
To keep an eye on hardware usage (especially GPU RAM and temperature) during long audiobook transcriptions, use `jtop`.
```bash
sudo pip3 install -U jetson-stats
sudo systemctl restart jetson_stats.service
jtop
```
