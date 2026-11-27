# UNIK Post

Upload a video (or photos) once, tick your YouTube channels, Instagram accounts and Facebook Pages,
write a separate title/caption for each, post now or schedule each one, and press one button.

* **Auto-routing**: vertical video ≤ 3 min → YouTube Short / Instagram Reel / Facebook Reel (FB Reels API max 90 s);
  longer or horizontal → normal YouTube video / Facebook video (Instagram: always a Reel). Manual Short/Long override.
* **Photo posts**: Instagram photo + carousel (up to 10), Facebook photo / album. **Text/link posts**: Facebook.
  YouTube Community posts have no API, so YouTube is shown as unavailable for photo/text posts.
* **Original quality**: the file is stored byte-for-byte and sent as is (no re-encode). Instagram photos get a JPEG copy only
  when the original isn't JPEG (Instagram only accepts JPEG).
* **Queue** with per-account status, links, failure reasons, retry and cancel.
* **Channels**: add / rename / remove / connect / reconnect / disconnect; seeded with Venu Kalyan's accounts.

## Stack
Flask + gunicorn (1 worker, 8 threads), vanilla JS, SQLite (dev) or Postgres via `DATABASE_URL`.
Background scheduler thread + `/cron/run` endpoint. Official APIs only: YouTube Data API v3, Meta Graph API
(Facebook Login for Business) and, optionally, Instagram API with Instagram Login.

## Environment
| Var | Needed | What |
| --- | --- | --- |
| `APP_PASSWORD` | yes | site password |
| `SECRET_KEY` | yes | session signing + token encryption key (Render generates it). Changing it means reconnecting accounts |
| `DATABASE_URL` | strongly recommended | Postgres URL (e.g. Neon free). Without it data lives in SQLite on Render's temporary disk and is lost on every restart/sleep |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | for YouTube | Google OAuth web client |
| `META_APP_ID`, `META_APP_SECRET` | for Facebook + Instagram | Meta app |
| `META_CONFIG_ID` | optional | Facebook Login for Business configuration id (otherwise scopes are requested directly) |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | optional | Instagram Login: connects IG accounts that aren't linked to a Facebook Page |
| `CRON_TOKEN` | recommended | protects `/cron/run` (Render generates it) |
| `BASE_URL` | optional | public URL; defaults to Render's `RENDER_EXTERNAL_URL` |
| `MAX_UPLOAD_MB` | optional | default 2048 |
| `MEDIA_RETENTION_HOURS` | optional | how long finished media stays for retries (default 24) |
| `META_GRAPH_VERSION` | optional | default `v25.0` |

## How scheduling works (and why)
Render's free plan sleeps after 15 minutes without traffic and wipes its disk on restart, so the app relies on
platform-native scheduling wherever it exists:

| Platform | Who publishes at the set time |
| --- | --- |
| YouTube | **YouTube** (`publishAt`). The video uploads right away as private and YouTube makes it public at the time. |
| Facebook (10 min – 29 days ahead) | **Facebook** (`scheduled_publish_time`). Uploaded right away. |
| Facebook (< 10 min or > 29 days) | the app |
| Instagram | the app (Instagram's API has no scheduling). It prepares the post up to 20 h early, then publishes on time. |

App-held posts need the app awake at that time: point a free external cron (e.g. cron-job.org) at
`https://<site>/cron/run?token=<CRON_TOKEN>` every 10 minutes. That also keeps media files alive for the
Instagram posts. Cost: an always-awake service uses ~720 of Render's 750 free instance hours a month, which are
shared by every free service in the workspace, so limit the cron to daytime hours (e.g. 06:00–23:59 IST ≈ 540 h)
if other free services must keep running. If the app was asleep, due posts go out as soon as it wakes.

## Run locally
```
pip install -r requirements.txt
SECRET_KEY=dev APP_PASSWORD=dev flask --app app run   # or gunicorn app:app
python -m pytest                                      # set TEST_DATABASE_URL=postgresql://... to run against Postgres
```

See `SETUP.md` for creating the Google and Meta apps.
