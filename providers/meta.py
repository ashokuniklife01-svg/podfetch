"""Meta Graph API client: Facebook Login, Pages, Instagram publishing, FB posting."""
import json
import time
from urllib.parse import urlencode

import requests

from providers import ProviderError

SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
          "instagram_basic", "instagram_content_publish", "business_management"]
TIMEOUT = 60

REAUTH_CODES = {190, 102, 463, 467}
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 80001, 80002}


def _error(resp, default="Meta error"):
    try:
        err = resp.json().get("error", {}) or {}
    except ValueError:
        err = {}
    code = err.get("code")
    msg = err.get("error_user_msg") or err.get("message") or str(resp.status_code)
    if code in REAUTH_CODES or code == 10 and "permission" in msg.lower():
        return ProviderError("Facebook access expired or lacks permission. Reconnect.", reauth=True)
    if code == 200 or (isinstance(code, int) and 200 <= code < 300):
        return ProviderError(f"Missing permission: {msg}", reauth=True)
    if code in TRANSIENT_CODES or resp.status_code >= 500:
        return ProviderError(f"Meta is busy ({msg}). Will retry.", transient=True)
    return ProviderError(f"{default}: {msg}")


class MetaClient:
    def __init__(self, app_id, app_secret, redirect_uri, version="v25.0", config_id="",
                 session=None, sleep=time.sleep, graph_host="https://graph.facebook.com"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.version = version
        self.config_id = config_id
        self.http = session or requests.Session()
        self.sleep = sleep
        self.graph = f"{graph_host}/{version}"
        self.graph_video = f"https://graph-video.facebook.com/{version}"
        self.rupload = f"https://rupload.facebook.com/video-upload/{version}"

    # ---- low level ---------------------------------------------------
    def _req(self, method, url, default="Meta error", **kw):
        kw.setdefault("timeout", TIMEOUT)
        try:
            r = self.http.request(method, url, **kw)
        except requests.RequestException:
            raise ProviderError("Couldn't reach Meta. Will retry.", transient=True)
        if r.status_code != 200:
            raise _error(r, default)
        try:
            return r.json()
        except ValueError:
            raise ProviderError(f"{default}: bad response", transient=True)

    def get(self, path, token, params=None, default="Meta error"):
        p = dict(params or {})
        p["access_token"] = token
        return self._req("GET", f"{self.graph}/{path}", default, params=p)

    def post(self, path, token, data=None, default="Meta error", base=None):
        d = dict(data or {})
        d["access_token"] = token
        return self._req("POST", f"{base or self.graph}/{path}", default, data=d)

    # ---- OAuth -------------------------------------------------------
    def auth_url(self, state):
        q = {"client_id": self.app_id, "redirect_uri": self.redirect_uri, "state": state,
             "response_type": "code"}
        if self.config_id:
            q["config_id"] = self.config_id
        else:
            q["scope"] = ",".join(SCOPES)
        return f"https://www.facebook.com/{self.version}/dialog/oauth?{urlencode(q)}"

    def exchange_code(self, code):
        short = self._req("GET", f"{self.graph}/oauth/access_token", "Facebook sign-in failed", params={
            "client_id": self.app_id, "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri, "code": code})
        long = self._req("GET", f"{self.graph}/oauth/access_token", "Facebook sign-in failed", params={
            "grant_type": "fb_exchange_token", "client_id": self.app_id,
            "client_secret": self.app_secret, "fb_exchange_token": short["access_token"]})
        return long["access_token"]

    def list_accounts(self, user_token):
        """Pages the user manages + linked IG professional accounts.

        Page tokens fetched with a long-lived user token don't expire.
        """
        fields = ("id,name,username,access_token,picture{url},"
                  "instagram_business_account{id,username,name,profile_picture_url}")
        out, url, params = [], "me/accounts", {"fields": fields, "limit": 100}
        pages = self.get(url, user_token, params, "Couldn't list your Pages")
        while True:
            for p in pages.get("data", []):
                ig = p.get("instagram_business_account")
                out.append({
                    "id": p["id"], "name": p.get("name", ""), "username": p.get("username"),
                    "token": p.get("access_token"),
                    "avatar": ((p.get("picture") or {}).get("data") or {}).get("url"),
                    "ig": ({"id": ig["id"], "username": ig.get("username"), "name": ig.get("name"),
                            "avatar": ig.get("profile_picture_url")} if ig else None),
                })
            nxt = (pages.get("paging") or {}).get("next")
            if not nxt:
                break
            pages = self._req("GET", nxt, "Couldn't list your Pages")
        return out

    # ---- Instagram ---------------------------------------------------
    def ig_create_container(self, ig_id, token, kind, media_url=None, caption="", children=None,
                            share_to_feed=True, is_carousel_item=False):
        data = {}
        if kind == "reel":
            data.update(media_type="REELS", video_url=media_url, share_to_feed="true" if share_to_feed else "false")
        elif kind == "photo":
            data["image_url"] = media_url
        elif kind == "carousel":
            data.update(media_type="CAROUSEL", children=",".join(children or []))
        else:
            raise ValueError(kind)
        if is_carousel_item:
            data["is_carousel_item"] = "true"
        elif caption:
            data["caption"] = caption
        return self.post(f"{ig_id}/media", token, data, "Instagram rejected the media")["id"]

    def ig_container_status(self, container_id, token):
        r = self.get(container_id, token, {"fields": "status_code,status"}, "Instagram status check failed")
        return r.get("status_code", "IN_PROGRESS"), r.get("status", "")

    def ig_wait(self, container_id, token, timeout=60, interval=3):
        t0 = time.monotonic()
        while True:
            code, status = self.ig_container_status(container_id, token)
            if code in ("FINISHED", "PUBLISHED"):
                return code
            if code in ("ERROR", "EXPIRED"):
                raise ProviderError(f"Instagram couldn't process the media: {status or code}")
            if time.monotonic() - t0 > timeout:
                return code
            self.sleep(interval)

    def ig_publish(self, ig_id, token, container_id):
        media_id = self.post(f"{ig_id}/media_publish", token, {"creation_id": container_id},
                             "Instagram publish failed")["id"]
        link = None
        try:
            link = self.get(media_id, token, {"fields": "permalink"}).get("permalink")
        except ProviderError:
            pass
        return media_id, link

    # ---- Facebook ----------------------------------------------------
    @staticmethod
    def _sched(data, publish_ts):
        if publish_ts:
            data["published"] = "false"
            data["scheduled_publish_time"] = str(int(publish_ts))
        return data

    def fb_video(self, page_id, token, file_url, title="", description="", publish_ts=None):
        data = self._sched({"file_url": file_url, "title": title, "description": description}, publish_ts)
        r = self.post(f"{page_id}/videos", token, data, "Facebook video upload failed", base=self.graph_video)
        vid = r["id"]
        return vid, f"https://www.facebook.com/{page_id}/videos/{vid}"

    def fb_reel(self, page_id, token, file_url, description="", publish_ts=None):
        start = self.post(f"{page_id}/video_reels", token, {"upload_phase": "start"}, "Facebook Reel start failed")
        vid = start["video_id"]
        try:
            r = self.http.post(f"{self.rupload}/{vid}", headers={"Authorization": f"OAuth {token}",
                                                                 "file_url": file_url}, timeout=300)
        except requests.RequestException:
            raise ProviderError("Couldn't reach Meta. Will retry.", transient=True)
        if r.status_code != 200:
            raise _error(r, "Facebook Reel upload failed")
        data = {"upload_phase": "finish", "video_id": vid, "description": description,
                "video_state": "SCHEDULED" if publish_ts else "PUBLISHED"}
        if publish_ts:
            data["scheduled_publish_time"] = str(int(publish_ts))
        self.post(f"{page_id}/video_reels", token, data, "Facebook Reel publish failed")
        return vid, f"https://www.facebook.com/reel/{vid}"

    def fb_photo(self, page_id, token, url, message="", publish_ts=None):
        data = self._sched({"url": url, "caption": message}, publish_ts)
        r = self.post(f"{page_id}/photos", token, data, "Facebook photo failed")
        pid = r.get("post_id") or r["id"]
        return pid, f"https://www.facebook.com/{pid}"

    def fb_photos(self, page_id, token, urls, message="", publish_ts=None):
        ids = []
        for u in urls:
            d = {"url": u, "published": "false"}
            if publish_ts:
                d["temporary"] = "true"
            ids.append(self.post(f"{page_id}/photos", token, d, "Facebook photo failed")["id"])
        data = {"message": message}
        for i, fid in enumerate(ids):
            data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
        self._sched(data, publish_ts)
        r = self.post(f"{page_id}/feed", token, data, "Facebook post failed")
        return r["id"], f"https://www.facebook.com/{r['id']}"

    def fb_feed(self, page_id, token, message="", link="", publish_ts=None):
        data = {"message": message}
        if link:
            data["link"] = link
        self._sched(data, publish_ts)
        r = self.post(f"{page_id}/feed", token, data, "Facebook post failed")
        return r["id"], f"https://www.facebook.com/{r['id']}"


IG_SCOPES = ["instagram_business_basic", "instagram_business_content_publish"]


class InstagramLoginClient(MetaClient):
    """'Instagram API with Instagram Login': connects one IG professional account
    directly (no Facebook Page needed). Publishing uses the same container flow
    on graph.instagram.com. Tokens last 60 days and are refreshed by the app."""

    def __init__(self, app_id, app_secret, redirect_uri, version="v25.0", session=None, sleep=time.sleep):
        super().__init__(app_id, app_secret, redirect_uri, version, session=session, sleep=sleep,
                         graph_host="https://graph.instagram.com")

    def auth_url(self, state):
        q = {"client_id": self.app_id, "redirect_uri": self.redirect_uri, "response_type": "code",
             "scope": ",".join(IG_SCOPES), "state": state, "enable_fb_login": "0", "force_authentication": "1"}
        return f"https://www.instagram.com/oauth/authorize?{urlencode(q)}"

    def exchange_code(self, code):
        code = (code or "").split("#")[0]
        short = self._req("POST", "https://api.instagram.com/oauth/access_token", "Instagram sign-in failed", data={
            "client_id": self.app_id, "client_secret": self.app_secret, "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri, "code": code})
        if isinstance(short.get("data"), list) and short["data"]:
            short = short["data"][0]
        long = self._req("GET", "https://graph.instagram.com/access_token", "Instagram sign-in failed", params={
            "grant_type": "ig_exchange_token", "client_secret": self.app_secret,
            "access_token": short["access_token"]})
        return {"access_token": long["access_token"], "expires_in": int(long.get("expires_in", 5184000))}

    def refresh(self, token):
        r = self._req("GET", "https://graph.instagram.com/refresh_access_token", "Instagram token refresh failed",
                      params={"grant_type": "ig_refresh_token", "access_token": token})
        return {"access_token": r["access_token"], "expires_in": int(r.get("expires_in", 5184000))}

    def me(self, token):
        r = self.get("me", token, {"fields": "user_id,username,name,profile_picture_url"}, "Couldn't read the Instagram account")
        return {"id": str(r.get("user_id") or r["id"]), "username": r.get("username"),
                "name": r.get("name"), "avatar": r.get("profile_picture_url")}
