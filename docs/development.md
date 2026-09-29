# Development

<p>
  <a href="https://hub.docker.com/r/drumsergio/audio-transcoder"><img src="https://img.shields.io/docker/image-size/drumsergio/audio-transcoder/latest" alt="Docker Image Size" /></a>
  <a href="https://github.com/GeiserX/audio-transcode-watcher/releases"><img src="https://img.shields.io/github/v/release/GeiserX/audio-transcode-watcher" alt="GitHub Release" /></a>
  <a href="https://codecov.io/gh/GeiserX/audio-transcode-watcher"><img src="https://codecov.io/gh/GeiserX/audio-transcode-watcher/graph/badge.svg" alt="Codecov" /></a>
</p>

## Requirements

- Docker (recommended), or Python 3.14+ with FFmpeg installed
- [Hatch](https://hatch.pypa.io/) build system

## Running Tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests with coverage
pytest
```

## Building the Docker Image

```bash
docker build -t audio-transcoder:dev .
```

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes before submitting a pull request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Run tests (`pytest`)
4. Commit your changes (`git commit -m 'feat: add amazing feature'`)
5. Push to the branch (`git push origin feat/amazing-feature`)
