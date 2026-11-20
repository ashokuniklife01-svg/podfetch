# UNIK AI Studio

Google-Flow-style AI generator: **Prompt → Video**, **Prompt → Image**, **Image → Video**, each in **16:9** (YouTube) or **9:16** (Reels/Shorts).

- **Prompt → Image is FREE** out of the box via the Pollinations classic API (no key; server-side proxy, rate-limited per IP).
  Free tier = one model, 1024×576 (16:9) / 576×1024 (9:16), small Pollinations logo in the corner.
- **Video modes + paid image models (fal.ai)** are shown as "coming soon" until BOTH `FAL_KEY` is set AND `VIDEO_ENABLED=true`.

## Run locally
```
pip install -r requirements.txt
FAL_KEY=xxxx APP_PASSWORD=choose-one python app.py   # http://localhost:5000
```

## Deploy on Render (free web service)
- Branch: `aistudio` · Runtime: Python 3 (or Docker, Dockerfile included)
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120`
- Health check path: `/health`
- Env vars: `FAL_KEY` (secret, from fal.ai/dashboard/keys), `APP_PASSWORD` (optional but recommended),
  `SECRET_KEY` (any long random string), `PYTHON_VERSION=3.12.7`
- `VIDEO_ENABLED` = `true` to switch on video + paid fal image models (default off; needs FAL_KEY with credit)
- Optional: `RATE_GENERATE` (fal generations per IP per 10 min, default 12), `RATE_FREE_IMAGE` (free images per IP per 10 min, default 15)

The fal key never reaches the browser. Without `FAL_KEY`/`VIDEO_ENABLED=true` the site opens on free Prompt → Image and video tabs show "Video needs fal.ai credit · coming soon".

## Tests
```
pip install pytest jsonschema playwright
python -m pytest -q tests            # mocked fal API, payloads validated against fal OpenAPI schemas
python tests/e2e/browser_test.py     # headless Chrome against a mock fal server
```
