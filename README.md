# UNIK AI Studio

Google-Flow-style AI generator: **Prompt → Video**, **Prompt → Image**, **Image → Video**, each in **16:9** (YouTube) or **9:16** (Reels/Shorts). Runs on fal.ai models with one key.

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
- Optional: `RATE_GENERATE` (generations per IP per 10 min, default 12)

The fal key never reaches the browser. Without `FAL_KEY` the site loads and shows a "setup needed" banner.

## Tests
```
pip install pytest jsonschema playwright
python -m pytest -q tests            # mocked fal API, payloads validated against fal OpenAPI schemas
python tests/e2e/browser_test.py     # headless Chrome against a mock fal server
```
