#!/bin/sh
# Start the bgutil PO-token server on loopback, then gunicorn.
# The POT server is optional: if it fails to start, the app still comes up.
set -eu

POT_HOST="${BGUTIL_POT_HOST:-127.0.0.1}"
POT_PORT="${BGUTIL_POT_PORT:-4416}"
POT_LOG="${BGUTIL_POT_LOG:-/tmp/bgutil/server.log}"
DENO_DIR="${DENO_DIR:-/tmp/deno-cache}"

# /tmp is often a fresh tmpfs at container start, so directories created
# during the image build are gone. Recreate them before writing anything.
mkdir -p /tmp/bgutil "$DENO_DIR"

# Optional YouTube account cookies (Netscape cookies.txt), passed as a
# single-line base64 env var so the secret is not baked into the image.
# yt-dlp rewrites the cookie file, so it must be created at runtime and
# left writable by this user. Never log the value.
install_yt_cookies() {
    if [ -z "${YT_COOKIES_B64:-}" ]; then
        echo "[podfetch] YouTube cookies: not loaded (YT_COOKIES_B64 is unset)"
        return 0
    fi
    mkdir -p /tmp/cookies
    chmod 700 /tmp/cookies
    cookie_path="/tmp/cookies/youtube.txt"
    tmp_path="${cookie_path}.tmp"
    # Strip whitespace so a wrapped value still decodes. Do not echo the secret.
    if printf '%s' "$YT_COOKIES_B64" | tr -d ' \n\r\t' | base64 -d >"$tmp_path" 2>/dev/null \
        && [ -s "$tmp_path" ]; then
        mv "$tmp_path" "$cookie_path"
        chmod 600 "$cookie_path"
        echo "[podfetch] YouTube cookies: loaded"
    else
        echo "[podfetch] YouTube cookies: not loaded (YT_COOKIES_B64 could not be decoded)"
        rm -f "$tmp_path" "$cookie_path"
    fi
}

install_yt_cookies || echo "[podfetch] YouTube cookies: not loaded (install failed)"

start_pot() {
    if [ ! -f /opt/bgutil/src/main.ts ]; then
        echo "[podfetch] bgutil POT sources not found at /opt/bgutil; continuing without a POT server"
        return 0
    fi
    echo "[podfetch] Starting bgutil POT server on ${POT_HOST}:${POT_PORT}"
    # Outbound network is required so the server can talk to YouTube BotGuard.
    # The listen address is forced to loopback via --host (not published on $PORT).
    nohup deno run \
        --allow-env \
        --allow-net \
        --allow-ffi=/opt/bgutil/node_modules \
        --allow-read=/opt/bgutil \
        --allow-write="${DENO_DIR:-/tmp/deno-cache}" \
        /opt/bgutil/src/main.ts --host "${POT_HOST}" --port "${POT_PORT}" \
        >"$POT_LOG" 2>&1 &
    echo $! > /tmp/bgutil/server.pid

    python3 -c "
import os, socket, sys, time
host = os.environ.get('BGUTIL_POT_HOST', '127.0.0.1')
port = int(os.environ.get('BGUTIL_POT_PORT', '4416'))
log = os.environ.get('BGUTIL_POT_LOG', '/tmp/bgutil/server.log')
for _ in range(40):
    try:
        s = socket.create_connection((host, port), 0.5)
        s.close()
        print(f'[podfetch] bgutil POT server is up on {host}:{port}', flush=True)
        sys.exit(0)
    except OSError:
        time.sleep(0.25)
print(f'[podfetch] bgutil POT server did not become ready on {host}:{port}; continuing anyway', flush=True)
try:
    text = open(log, 'r', errors='replace').read()[-2000:]
    if text:
        print('[podfetch] bgutil log tail:', text, flush=True)
except OSError:
    pass
"
}

start_pot || echo "[podfetch] POT server start failed; gunicorn will still run"

# One worker so the in-memory job store stays in a single process.
# Threads handle overlapping lookups, progress polls, and downloads.
# $PORT is set by Render (defaults to 10000 in this image).
exec gunicorn \
    --bind "0.0.0.0:${PORT:-10000}" \
    --workers 1 \
    --threads 8 \
    --timeout 600 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    app:app
