FROM python:3.14-slim

# Install FFmpeg for transcoding. The base image's ca-certificates 20250419 still
# trusts the compromised eMudhra intermediates; trixie-security distrusts them.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get install -y --only-upgrade ca-certificates \
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
