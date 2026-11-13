FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YT_SKIP_UPDATE=1 \
    YT_VERBOSE=1 \
    DENO_DIR=/tmp/deno-cache \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN chmod +x /usr/local/bin/deno && deno --version

WORKDIR /app

COPY requirements.txt .
# Install pinned deps, then refresh yt-dlp to whatever is newest at image build time.
# Runtime self-upgrade is disabled (YT_SKIP_UPDATE=1).
RUN pip install --no-cache-dir -r requirements.txt gunicorn \
    && pip install --no-cache-dir -U "yt-dlp[default]"

COPY . .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /tmp/yt-downloader-jobs "$DENO_DIR" \
    && chown -R appuser:appuser /app /tmp/yt-downloader-jobs "$DENO_DIR"
USER appuser

EXPOSE 10000

# One worker so the in-memory job store stays in a single process.
# Threads handle overlapping lookups, progress polls, and downloads.
# $PORT is set by Render (defaults to 10000 in this image).
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 600 --graceful-timeout 30 --access-logfile - --error-logfile - app:app"]
