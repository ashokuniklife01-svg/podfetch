"""Runtime configuration, all from environment variables."""
import os


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self, env=None):
        e = os.environ if env is None else env
        g = e.get
        self.SECRET_KEY = g("SECRET_KEY") or ""
        self.APP_PASSWORD = g("APP_PASSWORD") or ""
        base = (g("BASE_URL") or g("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
        self.BASE_URL = base
        self.GOOGLE_CLIENT_ID = g("GOOGLE_CLIENT_ID") or ""
        self.GOOGLE_CLIENT_SECRET = g("GOOGLE_CLIENT_SECRET") or ""
        self.META_APP_ID = g("META_APP_ID") or ""
        self.META_APP_SECRET = g("META_APP_SECRET") or ""
        self.INSTAGRAM_APP_ID = g("INSTAGRAM_APP_ID") or ""          # optional: Instagram Login
        self.INSTAGRAM_APP_SECRET = g("INSTAGRAM_APP_SECRET") or ""
        self.META_CONFIG_ID = g("META_CONFIG_ID") or ""  # optional: Facebook Login for Business configuration
        self.META_GRAPH_VERSION = g("META_GRAPH_VERSION") or "v25.0"
        self.DATABASE_URL = g("DATABASE_URL") or ""
        self.DATA_DIR = g("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        self.CRON_TOKEN = g("CRON_TOKEN") or ""
        self.MAX_UPLOAD_MB = int(g("MAX_UPLOAD_MB") or 2048)
        self.MEDIA_RETENTION_HOURS = float(g("MEDIA_RETENTION_HOURS") or 24)
        self.WORKER_ENABLED = (g("WORKER_ENABLED") or "true").lower() in ("1", "true", "yes")
        self.WORKER_INTERVAL = float(g("WORKER_INTERVAL") or 15)

    @property
    def google_ready(self):
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET and self.BASE_URL)

    @property
    def meta_ready(self):
        return bool(self.META_APP_ID and self.META_APP_SECRET and self.BASE_URL)

    @property
    def ig_login_ready(self):
        return bool(self.INSTAGRAM_APP_ID and self.INSTAGRAM_APP_SECRET and self.BASE_URL)

    @property
    def persistent_db(self):
        return bool(self.DATABASE_URL)
