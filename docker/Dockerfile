# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS node
FROM python:3.14-slim-trixie

LABEL org.opencontainers.image.title="RiiTube at Home" \
      org.opencontainers.image.description="YouTube search and seekable LAN video preparation for WiiMC-SS" \
      org.opencontainers.image.authors="cmyksoda" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RIIRIITUBE_HOST=0.0.0.0 \
    RIIRIITUBE_PORT=301 \
    RIIRIITUBE_DATA=/data \
    XDG_CACHE_HOME=/data/.cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg fonts-noto-core libatomic1 libstdc++6 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 riitube \
    && useradd --uid 10001 --gid riitube --create-home riitube \
    && install -d -o riitube -g riitube /data

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/LICENSE /usr/local/share/licenses/node/LICENSE

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py LICENSE ./
COPY templates/ ./templates/
COPY static/ ./static/
COPY assets/ ./assets/
COPY docker/entrypoint.sh /usr/local/bin/riitube-entrypoint
COPY docker/README.md ./DOCKER-README.md
RUN chmod 755 /usr/local/bin/riitube-entrypoint

USER 10001:10001
EXPOSE 301
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('RIIRIITUBE_PORT', '301') + '/', timeout=3).close()"
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/riitube-entrypoint"]
CMD ["python", "app.py"]
