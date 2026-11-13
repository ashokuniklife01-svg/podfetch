FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YT_SKIP_UPDATE=1 \
    YT_VERBOSE=1 \
    DENO_DIR=/tmp/deno-cache \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
    BGUTIL_POT_HOST=127.0.0.1 \
    BGUTIL_POT_PORT=4416 \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN chmod +x /usr/local/bin/deno && deno --version

# Prebuilt PO-token HTTP server (Deno flavor, version-matched to the pip plugin).
# We bind it to 127.0.0.1 only at runtime and never publish 4416 on $PORT.
COPY --from=brainicism/bgutil-ytdlp-pot-provider:2.0.2-deno /app /opt/bgutil

WORKDIR /app

COPY requirements.txt .
# Pinned deps, then the newest yt-dlp plus Chrome TLS impersonation and the
# bgutil PO-token plugin. Runtime self-upgrade is disabled (YT_SKIP_UPDATE=1).
RUN pip install --no-cache-dir -r requirements.txt gunicorn \
    && pip install --no-cache-dir -U "yt-dlp[default,curl-cffi]" \
    && pip install --no-cache-dir "bgutil-ytdlp-pot-provider==2.0.2"

COPY . .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /tmp/yt-downloader-jobs "$DENO_DIR" /tmp/bgutil \
    && chmod +x /app/entrypoint.sh \
    && chown -R appuser:appuser /app /tmp/yt-downloader-jobs "$DENO_DIR" /opt/bgutil /tmp/bgutil
USER appuser

# Rebuild the Deno cache with this image's Deno, in case the official
# provider image was built with a different version.
RUN deno cache /opt/bgutil/src/main.ts \
    || echo "[podfetch] deno cache of bgutil skipped (will retry at runtime)"

EXPOSE 10000

# One worker so the in-memory job store stays in a single process.
# entrypoint.sh starts the loopback POT server, then gunicorn.
CMD ["/app/entrypoint.sh"]
