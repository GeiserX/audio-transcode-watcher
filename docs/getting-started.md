# Getting started

## Docker Compose (Recommended)

**1.** Create a `config.yaml` file:

```yaml
source:
  path: /music/flac

outputs:
  - name: alac
    codec: alac
    path: /music/alac

  - name: mp3-256
    codec: mp3
    bitrate: 256k
    path: /music/mp3

  - name: aac-256
    codec: aac
    bitrate: 256k
    path: /music/aac
```

**2.** Create a `docker-compose.yml`:

```yaml
services:
  audio-transcoder:
    image: drumsergio/audio-transcoder:0.6.2
    container_name: audio_transcoder
    environment:
      - TZ=Europe/Madrid
      - CONFIG_FILE=/app/config.yaml
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - /path/to/flac:/music/flac:ro
      - /path/to/alac:/music/alac
      - /path/to/mp3:/music/mp3
      - /path/to/aac:/music/aac
    restart: unless-stopped
```

**3.** Start the service:

```bash
docker compose up -d
```

## Docker CLI

```bash
docker run -d \
  --name audio_transcoder \
  -e TZ=Europe/Madrid \
  -e CONFIG_FILE=/app/config.yaml \
  -v ./config.yaml:/app/config.yaml:ro \
  -v /path/to/flac:/music/flac:ro \
  -v /path/to/mp3:/music/mp3 \
  --restart unless-stopped \
  drumsergio/audio-transcoder:0.6.2
```

