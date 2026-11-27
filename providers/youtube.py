"""YouTube Data API v3 client: OAuth, channel lookup, resumable upload."""
import json
import os
import time
from urllib.parse import urlencode

import requests

from providers import ProviderError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
CHUNK = 8 * 1024 * 1024      # multiple of 256 KiB
TIMEOUT = 60

FRIENDLY = {
    "quotaExceeded": "YouTube daily limit reached for this app. It resets daily around 1:30 PM IST (12:30 PM IST in US summer).",
    "uploadLimitExceeded": "This channel hit YouTube's upload limit for today. Try again tomorrow.",
    "youtubeSignupRequired": "This Google account has no YouTube channel yet.",
    "forbidden": "YouTube refused the upload for this channel.",
    "invalidTitle": "YouTube rejected the title.",
    "invalidDescription": "YouTube rejected the description.",
    "invalidPublishAt": "YouTube rejected the schedule time.",
}


def _error(resp, default="YouTube error"):
    reason, msg = "", ""
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", "")
            errs = err.get("errors") or []
            if errs:
                reason = errs[0].get("reason", "")
        else:
            reason = str(err)
            msg = resp.json().get("error_description", "")
    except ValueError:
        pass
    status = resp.status_code
    if reason == "invalid_grant" or status == 401:
        return ProviderError("YouTube access expired. Reconnect this channel.", reauth=True)
    if reason in FRIENDLY:
        return ProviderError(FRIENDLY[reason], transient=False)
    if status in (429, 500, 502, 503, 504):
        return ProviderError(f"YouTube is busy ({status}). Will retry.", transient=True)
    return ProviderError(f"{default}: {msg or reason or status}")


class YouTubeClient:
    def __init__(self, client_id, client_secret, redirect_uri, session=None, sleep=time.sleep):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http = session or requests.Session()
        self.sleep = sleep

    # ---- OAuth -------------------------------------------------------
    def auth_url(self, state):
        q = {
            "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": " ".join(SCOPES),
            "access_type": "offline", "prompt": "consent select_account",
            "include_granted_scopes": "true", "state": state,
        }
        return f"{AUTH_URL}?{urlencode(q)}"

    def exchange_code(self, code):
        r = self.http.post(TOKEN_URL, data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"}, timeout=TIMEOUT)
        if r.status_code != 200:
            raise _error(r, "Google sign-in failed")
        return r.json()   # access_token, expires_in, refresh_token, scope

    def refresh(self, refresh_token):
        try:
            r = self.http.post(TOKEN_URL, data={
                "refresh_token": refresh_token, "client_id": self.client_id,
                "client_secret": self.client_secret, "grant_type": "refresh_token"}, timeout=TIMEOUT)
        except requests.RequestException:
            raise ProviderError("Couldn't reach Google. Will retry.", transient=True)
        if r.status_code != 200:
            raise _error(r, "Google token refresh failed")
        return r.json()

    def revoke(self, token):
        try:
            self.http.post(REVOKE_URL, data={"token": token}, timeout=15)
        except requests.RequestException:
            pass

    # ---- data --------------------------------------------------------
    def my_channel(self, access_token):
        r = self.http.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT)
        if r.status_code != 200:
            raise _error(r, "Couldn't read your channel")
        items = r.json().get("items") or []
        if not items:
            raise ProviderError("This Google account has no YouTube channel. Pick the channel's account when signing in.")
        it = items[0]
        sn = it.get("snippet", {})
        thumb = ((sn.get("thumbnails") or {}).get("default") or {}).get("url")
        return {"id": it["id"], "title": sn.get("title", ""), "handle": sn.get("customUrl"), "avatar": thumb}

    # ---- upload ------------------------------------------------------
    @staticmethod
    def build_metadata(title, description, privacy, publish_at=None, made_for_kids=False):
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(made_for_kids)}
        if publish_at:
            status["privacyStatus"] = "private"          # required by the API for publishAt
            status["publishAt"] = publish_at
        return {"snippet": {"title": title, "description": description or "", "categoryId": "22"},
                "status": status}

    def start_upload(self, access_token, metadata, size, mime="video/*"):
        r = self.http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                           headers={"Authorization": f"Bearer {access_token}",
                                    "Content-Type": "application/json; charset=UTF-8",
                                    "X-Upload-Content-Length": str(size),
                                    "X-Upload-Content-Type": mime or "video/*"},
                           data=json.dumps(metadata), timeout=TIMEOUT)
        if r.status_code != 200 or not r.headers.get("Location"):
            raise _error(r, "YouTube upload couldn't start")
        return r.headers["Location"]

    def _query_offset(self, session_url, token, size):
        r = self.http.put(session_url, headers={"Authorization": f"Bearer {token}",
                                                "Content-Range": f"bytes */{size}"}, timeout=TIMEOUT)
        if r.status_code in (200, 201):
            return size, r
        if r.status_code == 308:
            rng = r.headers.get("Range")
            return (int(rng.split("-")[1]) + 1 if rng else 0), None
        raise _error(r, "YouTube upload lost")

    def upload_file(self, session_url, path, get_token, progress=None, max_retries=6):
        """Upload a file to a resumable session in chunks.

        get_token(force_refresh: bool) -> access token (refreshes on 401).
        progress(sent, total) is called after each chunk. Returns the video resource.
        """
        size = os.path.getsize(path)
        token = get_token(False)
        offset, retries = 0, 0
        with open(path, "rb") as f:
            while True:
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                headers = {"Authorization": f"Bearer {token}", "Content-Length": str(len(chunk)),
                           "Content-Range": f"bytes {offset}-{end}/{size}"}
                try:
                    r = self.http.put(session_url, headers=headers, data=chunk, timeout=300)
                except requests.RequestException:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    if progress:
                        progress(size, size)
                    return r.json()
                if r is not None and r.status_code == 308:
                    rng = r.headers.get("Range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    retries = 0
                    if progress:
                        progress(offset, size)
                    continue
                if r is not None and r.status_code == 401:
                    token = get_token(True)
                elif r is not None and r.status_code not in (408, 429, 500, 502, 503, 504):
                    raise _error(r, "YouTube upload failed")
                retries += 1
                if retries > max_retries:
                    raise ProviderError("YouTube upload kept failing. Will retry.", transient=True)
                self.sleep(min(2 ** retries, 60))
                try:
                    offset, done = self._query_offset(session_url, token, size)
                    if done is not None:
                        return done.json()
                except ProviderError as e:
                    if e.reauth:
                        token = get_token(True)
                    elif not e.transient:
                        raise
                except requests.RequestException:
                    pass
