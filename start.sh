#!/usr/bin/env bash
# Double-click or run: ./start.sh
set -e
cd "$(cd "$(dirname "$0")" && pwd)"

echo "========================================"
echo "  PodFetch — YouTube downloader"
echo "========================================"
echo

pick_python() {
  local cand
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        echo "$cand"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo "Python 3.10 or newer is required, but it was not found on this computer."
  echo
  if [ "$(uname)" = "Darwin" ]; then
    echo "Install it from https://www.python.org/downloads/  or run:  brew install python"
  else
    echo "Install it with your package manager, for example:"
    echo "  sudo apt install python3 python3-venv python3-pip"
    echo "  sudo dnf install python3"
    echo "Or download it from https://www.python.org/downloads/"
  fi
  echo
  read -r -p "Press Enter to close…" || true
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg is required (it merges video + audio and creates MP3s), but it was not found."
  echo
  if [ "$(uname)" = "Darwin" ]; then
    echo "Install it with Homebrew:"
    echo "  brew install ffmpeg"
    if command -v brew >/dev/null 2>&1; then
      echo
      read -r -p "Install ffmpeg with Homebrew now? [y/N] " ans || true
      case "$ans" in
        y|Y)
          brew install ffmpeg
          ;;
        *)
          read -r -p "Press Enter to close…" || true
          exit 1
          ;;
      esac
    else
      echo "Homebrew is not installed. See https://ffmpeg.org/download.html"
      read -r -p "Press Enter to close…" || true
      exit 1
    fi
  else
    echo "Install it with your package manager, for example:"
    echo "  sudo apt install ffmpeg"
    echo "  sudo dnf install ffmpeg"
    echo "Or see https://ffmpeg.org/download.html"
    echo
    read -r -p "Press Enter to close…" || true
    exit 1
  fi
fi

if [ ! -d .venv ]; then
  echo "Creating a Python environment…"
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "Installing / updating packages…"
python -m pip install -U pip >/dev/null
python -m pip install -U -r requirements.txt

open_browser() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v sensible-browser >/dev/null 2>&1; then
    sensible-browser "$url" >/dev/null 2>&1 || true
  fi
}

PORT="${PORT:-5000}"
URL="http://127.0.0.1:${PORT}"

already_up() {
  python - << PY
import sys, urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:${PORT}/health", timeout=1)
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

if already_up; then
  echo "The app is already running. Opening ${URL}"
  open_browser "$URL"
  read -r -p "Press Enter to close this window (the app will keep running)…" || true
  exit 0
fi

echo
echo "Starting the app at ${URL}"
echo "Leave this window open while you use it. Close the window to stop."
echo

# Open the browser once the server is up, without blocking the server itself.
(
  for _ in $(seq 1 50); do
    if already_up; then
      open_browser "$URL"
      exit 0
    fi
    sleep 0.2
  done
) &

export YT_SKIP_UPDATE=1
exec python app.py
