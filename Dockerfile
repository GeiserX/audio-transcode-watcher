FROM python:3.13-slim

# Install FFmpeg for transcoding
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy package files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY tools/ ./tools/

# Install the package
RUN pip install --no-cache-dir .

# Environment
ENV PYTHONUNBUFFERED=1

# Default command
ENTRYPOINT ["python", "-m", "audio_transcode_watcher.main"]
