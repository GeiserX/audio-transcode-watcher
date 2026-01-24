FROM python:3.13-slim

# FFmpeg for transcoding
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# App
COPY music_sync.py /app/music_sync.py
COPY verify_sync.py /app/verify_sync.py
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "music_sync.py"]