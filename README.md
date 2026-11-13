# PodFetch — personal YouTube, Instagram, and Facebook downloader

A small web app for downloading public media you own or have the right to save. The home page has three large cards (YouTube, Instagram, Facebook) plus a smaller WhatsApp Status how-to. Click a platform, paste a link, preview the media, then download.

- **YouTube:** 1080p, 720p, 360p MP4, or MP3 audio
- **Instagram:** Reels, posts (including photo and carousel posts), and stories (optional login cookies)
- **Facebook:** Public reels, videos, and posts; stories with optional login cookies
- **WhatsApp Status:** on-phone instructions only — a website cannot read statuses from your phone

If you paste an Instagram or Facebook link on the YouTube screen (or the other way around), the app detects the site and opens the right screen automatically.

For YouTube 1080p / 720p / 360p, the app takes the best video stream at that height (or the next best if that exact quality is missing) and merges it with the best audio into an MP4. It prefers H.264 video and AAC audio so the file plays in ordinary players and browsers. If those codecs are already in the streams, the file is remuxed (no slow re-encode). Instagram videos are saved as the best available MP4, plus an optional MP3. Facebook videos offer HD and SD plus MP3. Photos are saved as JPG. Temporary files are deleted after the download finishes.

This is a personal tool. Use it only for media you have the right to download. Private, age-restricted, or members-only videos cannot be fetched. The app will not bypass a login.

## Double-click start

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH**.
2. Double-click the starter for your computer:
   - **Windows:** `start.bat`
   - **Mac:** `start.command` (first time: right-click → Open, if macOS warns about an unidentified developer)
   - **Linux:** `start.sh` (or run `./start.sh` in a terminal)
3. Your browser should open [http://localhost:5000](http://localhost:5000) automatically. Leave the terminal window open while you use the app.

The starter checks for Python and ffmpeg, creates a virtual environment, installs updates, and launches the app.

- **Windows:** if ffmpeg is missing, the script tries `winget install Gyan.FFmpeg`. If winget cannot install it, install ffmpeg yourself from [ffmpeg.org](https://ffmpeg.org/download.html) (or `choco install ffmpeg`) and double-click `start.bat` again.
- **Mac:** if ffmpeg is missing and Homebrew is installed, the script can run `brew install ffmpeg` for you.
- **Linux:** install ffmpeg first (`sudo apt install ffmpeg` or `sudo dnf install ffmpeg`).

## What you need

- Python 3.10 or newer
- ffmpeg and ffprobe (required for merging video + audio, converting to MP4/JPG, and extracting MP3)
- A network connection
- Optional: [aria2](https://aria2.github.io/) (`aria2c` on your PATH) for faster YouTube fragment downloads
- Optional: a Netscape cookies file for Instagram or Facebook **stories** (see below)

## Manual setup (Windows)

If you prefer the terminal instead of `start.bat`:

1. Install Python from [python.org](https://www.python.org/downloads/). During setup, tick **Add python.exe to PATH**.
2. Install ffmpeg (`winget install Gyan.FFmpeg`, `choco install ffmpeg`, or a build from [ffmpeg.org](https://ffmpeg.org/download.html) added to PATH). Check with `ffmpeg -version` and `python --version`.
3. In this folder:

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```

4. Open [http://localhost:5000](http://localhost:5000).

## Manual setup (macOS)

1. Install Python 3 if needed (from [python.org](https://www.python.org/downloads/) or `brew install python`).
2. Install ffmpeg: `brew install ffmpeg`. Check with `ffmpeg -version`.
3. In this folder:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python app.py
   ```

4. Open [http://localhost:5000](http://localhost:5000).

## Manual setup (Linux)

Install ffmpeg from your package manager (`sudo apt install ffmpeg` or `sudo dnf install ffmpeg`), then follow the same `venv` / `pip install -r requirements.txt` / `python app.py` steps as on macOS. Or run `./start.sh`.

## How to use it

1. Open the home page and choose **YouTube**, **Instagram**, or **Facebook**.
2. Paste a public link and click **Get video** or **Get media**.
3. Pick a format. For Instagram and Facebook carousel posts, each photo or video has its own buttons.
4. Wait while the file is prepared (a progress message is shown), then save it when your browser starts the download.

YouTube links that work include `youtube.com/watch?v=…`, `youtu.be/…`, Shorts, `/embed/`, `/live/`, `m.youtube.com`, `music.youtube.com`, and links with `&list=`, `&t=`, `&si=`, or extra spaces. Only that one video is downloaded — never the whole playlist.

Facebook links that work include `facebook.com/reel/`, `/videos/`, `/watch?v=`, `/share/r/`, `/share/v/`, `fb.watch`, and `m.facebook.com`.

If the exact quality you asked for is not available, the app falls back to the best quality below that and tells you.

### WhatsApp Status

A website cannot access WhatsApp statuses on a phone. The WhatsApp card only explains how to save one:

- **Android:** Files → WhatsApp → Media → `.Statuses`, or use the status’s Share / Forward option.
- **iPhone:** Forward the status to yourself, or use a screenshot / screen recording.

### Instagram and Facebook stories (optional cookies)

Stories are not public. To enable them for an account you are logged into and allowed to use:

1. Export a **Netscape cookies.txt** file from your browser while logged in.
2. Save it as `cookies/instagram.txt` or `cookies/facebook.txt`.
3. Restart the app.

If the file is missing, the Story screen shows a friendly message and does not try to download. This app will not bypass a login. Never share cookie files — they are as sensitive as a logged-in session. They are excluded from git and from the project zip. See `cookies/README.md`.

## If a download fails

- **Wrong site:** paste a public YouTube, Instagram, or Facebook URL. The app will route a link that landed on the wrong screen.
- **Private / age-restricted / members-only / removed:** the app will say so. It cannot bypass those restrictions.
- **Login required / stories:** add a cookies file as described above, only for content you have the right to view.
- **Rate limited:** Instagram and Facebook sometimes throttle shared or datacenter IPs. Wait a few minutes, or run the app on your own computer (a home connection usually works better).
- **Bot check / “sign in to confirm you are not a bot”:** YouTube sometimes blocks datacenter or shared IP addresses. Running the app on your own computer usually works. The app also updates yt-dlp on startup when it can reach the internet.
- **ffmpeg not found:** install ffmpeg and make sure it is on your PATH, then start the app again.


## Deploy on Render (Docker)

This project includes a `Dockerfile` and `render.yaml` for [Render](https://render.com/)’s free web plan (Docker runtime).

1. Push this repo to GitHub (for example `github.com/ashokuniklife01-svg/podfetch`).
2. In the Render dashboard, create a Blueprint from the repo, or a new Web Service that builds from the Dockerfile. The health check path is `/health`.
3. Render sets the `PORT` environment variable. The container listens on `0.0.0.0:$PORT` with **gunicorn**, **1 worker**, and several threads. That keeps the in-memory job store in a single process. yt-dlp is installed (and refreshed to the latest release) when the image is built; the app does not self-upgrade it at startup.

Free web services sleep after inactivity, so the first request after a pause can take longer than usual. Temporary download files live in the container’s ephemeral disk and are cleaned up after each download.

To run the same production command locally:

```bash
YT_SKIP_UPDATE=1 gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 600 app:app
```

## Project layout

```
yt-downloader/
  start.bat           # Windows double-click starter
  start.command       # macOS double-click starter
  start.sh            # Linux / Mac starter
  app.py              # Flask backend (yt-dlp + ffmpeg)
  extractors.py       # Instagram / Facebook detection and helpers
  templates/index.html
  cookies/README.md   # how to add optional story cookies
  Dockerfile          # Render / Docker image (ffmpeg + gunicorn)
  render.yaml         # Render Blueprint (free web service)
  requirements.txt
  README.md
```

The server listens on `0.0.0.0:5000` by default. Override with the `HOST` and `PORT` environment variables if you need to. Set `YT_SKIP_UPDATE=1` to skip the startup yt-dlp upgrade check.
