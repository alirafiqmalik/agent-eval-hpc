# Trial sandbox for every starter task. Modal builds it once and caches it.
# Modal adds the NVIDIA driver and nvidia-smi to GPU sandboxes at start.
FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /tests /logs
WORKDIR /app
