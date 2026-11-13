"""Link detection and Instagram / Facebook metadata + download helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

APP_ROOT = Path(__file__).resolve().parent
COOKIES_DIR = APP_ROOT / "cookies"

IG_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}
FB_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
    "mbasic.facebook.com",
    "business.facebook.com",
    "fb.com",
    "www.fb.com",
    "m.fb.com",
    "fb.watch",
    "www.fb.watch",
}

URL_IN_TEXT_RE = re.compile(
    r"(https?://[^\s<>\"']+|"
    r"(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com|m\.youtube\.com|music\.youtube\.com|"
    r"instagram\.com|instagr\.am|facebook\.com|fb\.watch|fb\.com|m\.facebook\.com)/[^\s<>\"']+)",
    re.IGNORECASE,
)

SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_youtube_host(host: str) -> bool:
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in {
        "youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "youtu.be",
        "gaming.youtube.com",
        "youtubekids.com",
    } or host.endswith(".youtube.com") or host.endswith(".youtu.be") or host.endswith(".youtube-nocookie.com")


def cookies_path(platform: str) -> Path:
    return COOKIES_DIR / f"{platform}.txt"


def has_cookies(platform: str) -> bool:
    path = cookies_path(platform)
    try:
        return path.is_file() and path.stat().st_size > 32
    except OSError:
        return False


def _extract_raw_url(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw or "").strip()
    if not text:
        raise ValueError("Please paste a link.")
    text = text.replace("&amp;", "&")
    found = URL_IN_TEXT_RE.search(text)
    url = found.group(1) if found else text
    url = url.strip("<>\"'.,);[]")
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    url = re.sub(r"^http://", "https://", url, flags=re.IGNORECASE)
    return url


def detect_link(raw: str) -> dict:
    """Identify YouTube, Instagram, or Facebook and a rough content kind."""
    try:
        url = _extract_raw_url(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path or "")
    query = parse_qs(parsed.query)

    if _is_youtube_host(host):
        kind = "video"
        if "/shorts/" in path:
            kind = "shorts"
        elif "/live/" in path:
            kind = "live"
        return {"ok": True, "platform": "youtube", "kind": kind, "url": url}

    host_short = host[4:] if host.startswith("www.") else host
    if host_short in IG_HOSTS or host_short.endswith(".instagram.com"):
        kind = "post"
        if "/stories/" in path or "/story/" in path:
            kind = "story"
        elif "/reel/" in path or "/reels/" in path:
            kind = "reel"
        elif "/tv/" in path or "/p/" in path:
            kind = "post"
        return {"ok": True, "platform": "instagram", "kind": kind, "url": url}

    if host_short in FB_HOSTS or host_short.endswith(".facebook.com") or host_short.endswith(".fb.com") or host_short.endswith(".fb.watch"):
        kind = "video"
        if "/stories/" in path or "story.php" in path:
            kind = "story"
        elif "/reel/" in path or "/reels/" in path or "/share/r/" in path:
            kind = "reel"
        elif "/watch" in path or "fb.watch" in host_short or "/videos/" in path or "/share/v/" in path or query.get("v"):
            kind = "video"
        elif "/posts/" in path or "/permalink.php" in path or "/photo" in path or "/share/p/" in path:
            kind = "post"
        return {"ok": True, "platform": "facebook", "kind": kind, "url": url}

    return {
        "ok": False,
        "error": "That is not a YouTube, Instagram, or Facebook link. Paste a public URL from one of those sites.",
    }


def instagram_shortcode(url: str) -> str | None:
    path = unquote(urlparse(url).path or "")
    m = re.search(r"/(?:reel|reels|p|tv|stories)/([A-Za-z0-9_-]+)", path)
    if m:
        # stories are /stories/username/id
        if "/stories/" in path:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[0] == "stories":
                return parts[2] if SHORTCODE_RE.match(parts[2]) else parts[1]
        return m.group(1)
    return None


def _ydl_opts(cookies: Path | None, extra: dict | None = None) -> dict:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 8,
        "fragment_retries": 8,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "cachedir": False,
        "overwrites": True,
        "ignore_no_formats_error": True,
        "noplaylist": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if cookies and cookies.is_file():
        opts["cookiefile"] = str(cookies)
    if extra:
        opts.update(extra)
    return opts


def _login_required_message(platform: str, kind: str, exc: BaseException | None = None) -> str:
    text = (str(exc) or "").lower() if exc else ""
    if kind == "story":
        cookie = "cookies/instagram.txt" if platform == "instagram" else "cookies/facebook.txt"
        site = "Instagram" if platform == "instagram" else "Facebook"
        return (
            f"{site} stories need a logged-in account. "
            f"To enable them, export a Netscape cookies file from your browser while you are "
            f"logged in, and save it as {cookie}. This app will not bypass the login."
        )
    if "login" in text or "log in" in text or "sign in" in text or "not logged" in text:
        site = "Instagram" if platform == "instagram" else "Facebook"
        return (
            f"This {site} {kind} requires a logged-in account (it may be private, or the site "
            f"is asking for a login from this network). Add a Netscape cookies file at "
            f"cookies/{platform}.txt if you have the right to view it. This app will not bypass the login."
        )
    return ""


def social_friendly_error(platform: str, kind: str, exc: BaseException) -> str:
    login_msg = _login_required_message(platform, kind, exc)
    lowered = (str(exc) or "").lower()
    if login_msg and any(
        n in lowered
        for n in (
            "login",
            "log in",
            "sign in",
            "not logged",
            "restricted video",
            "only available to",
            "please log",
        )
    ):
        return login_msg
    if "private" in lowered:
        return f"This {platform} {kind} is private, so it cannot be downloaded."
    if ("rate" in lowered and "limit" in lowered) or "too many requests" in lowered or "please wait" in lowered:
        return "The site is rate-limiting requests right now. Please wait a few minutes and try again."
    if "empty media response" in lowered or "login required" in lowered:
        site = "Instagram" if platform == "instagram" else "Facebook"
        return (
            f"{site} did not return this {kind}. It may be private, or the site is asking for a login "
            f"from this network. If you have the right to view it, you can add a Netscape cookies file "
            f"at cookies/{platform}.txt. This app will not bypass the login."
        )
    if "not found" in lowered or "unavailable" in lowered or "no longer available" in lowered:
        return f"This {platform} {kind} is unavailable. It may have been removed or is hidden."
    if "no video formats" in lowered:
        return "Couldn't find a video in that link. If it's a photo, try the Post option."
    last = str(exc).strip().splitlines()[-1]
    last = re.sub(r"^ERROR:\s*", "", last).strip()
    last = re.sub(r"^\[[^\]]+\]\s*", "", last).strip()
    return last or f"Something went wrong while talking to {platform}."


def _best_image_url(info: dict) -> str | None:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        def score(t: dict) -> tuple:
            w = t.get("width") or 0
            h = t.get("height") or 0
            url = t.get("url") or ""
            # Prefer uncropped / full-size variants when dimensions are missing.
            bonus = 1 if "stp=dst-jpg_e" in url or "s1080x1080" in url else 0
            return (w * h, bonus, len(url))

        best = max(thumbs, key=score)
        if best.get("url"):
            return best["url"]
    return info.get("thumbnail") or info.get("url")


def _is_video_entry(info: dict) -> bool:
    if info.get("vcodec") not in (None, "none") and (info.get("ext") in {"mp4", "m4v", "webm", "mkv", "mov"} or info.get("formats")):
        formats = info.get("formats") or []
        if any((f.get("vcodec") not in (None, "none")) for f in formats) or info.get("vcodec") not in (None, "none"):
            # Photo posts often have no formats at all.
            if formats or info.get("url") and info.get("ext") in {"mp4", "m4v", "webm", "mkv", "mov"}:
                return True
    formats = info.get("formats") or []
    if any((f.get("vcodec") not in (None, "none") and f.get("url")) for f in formats):
        return True
    if info.get("duration") and (formats or info.get("url")):
        ext = (info.get("ext") or "").lower()
        if ext in {"mp4", "m4v", "webm", "mkv", "mov", ""}:
            return bool(formats or ext)
    return False


def _item_from_entry(entry: dict, index: int) -> dict:
    video = _is_video_entry(entry)
    image = _best_image_url(entry)
    width = entry.get("width")
    height = entry.get("height")
    if not width or not height:
        for t in reversed(entry.get("thumbnails") or []):
            if t.get("width") and t.get("height"):
                width = width or t["width"]
                height = height or t["height"]
                break
    item = {
        "id": str(entry.get("id") or index),
        "index": index,
        "type": "video" if video else "photo",
        "title": entry.get("title") or entry.get("description") or "",
        "thumbnail": image,
        "image_url": None if video else image,
        "width": width,
        "height": height,
        "duration": entry.get("duration"),
        "webpage_url": entry.get("webpage_url") or entry.get("original_url"),
    }
    if video:
        item["qualities"] = {"mp4": True, "mp3": True}
    else:
        item["qualities"] = {"jpg": True}
    return item


def _ydl_extract(url: str, cookies: Path | None) -> dict:
    opts = _ydl_opts(cookies, {"skip_download": True})
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _instaloader_items(shortcode: str) -> tuple[list[dict], dict] | None:
    try:
        import instaloader  # type: ignore
    except Exception:
        return None
    try:
        loader = instaloader.Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
    except Exception:
        return None
    meta = {
        "title": (post.caption or f"Post by {post.owner_username}")[:200],
        "uploader": post.owner_username,
        "id": shortcode,
    }
    items: list[dict] = []

    def add(is_video: bool, display_url: str | None, video_url: str | None, idx: int) -> None:
        items.append(
            {
                "id": f"{shortcode}-{idx}",
                "index": idx,
                "type": "video" if is_video else "photo",
                "title": meta["title"],
                "thumbnail": display_url,
                "image_url": None if is_video else display_url,
                "video_url": video_url if is_video else None,
                "width": None,
                "height": None,
                "duration": None,
                "webpage_url": f"https://www.instagram.com/p/{shortcode}/",
                "qualities": {"mp4": True, "mp3": True} if is_video else {"jpg": True},
            }
        )

    if post.typename == "GraphSidecar":
        for i, node in enumerate(post.get_sidecar_nodes()):
            add(node.is_video, getattr(node, "display_url", None), getattr(node, "video_url", None), i)
    else:
        add(post.is_video, post.url, post.video_url if post.is_video else None, 0)
    return items, meta


def _gallery_dl_items(url: str, cookies: Path | None) -> tuple[list[dict], dict] | None:
    exe = shutil.which("gallery-dl") or str(Path(sys.executable).parent / "gallery-dl")
    if not Path(exe).exists() and not shutil.which("gallery-dl"):
        return None
    cmd = [exe, "--no-download", "-j", "--range", "1-50", url]
    if cookies and cookies.is_file():
        cmd[1:1] = ["--cookies", str(cookies)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    items: list[dict] = []
    title = "Instagram post"
    uploader = None
    # gallery-dl JSON is a list of [type, object] messages mixed with file dicts.
    records: list[dict] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and (row.get("url") or row.get("filename")):
                records.append(row)
            elif isinstance(row, list) and len(row) >= 2 and isinstance(row[1], dict):
                payload = row[1]
                if payload.get("error"):
                    continue
                if payload.get("url") or payload.get("display_url"):
                    records.append(payload)
    elif isinstance(data, dict):
        records.append(data)
    for i, rec in enumerate(records):
        media_url = rec.get("url") or rec.get("display_url") or rec.get("content")
        if not media_url:
            continue
        extension = (rec.get("extension") or rec.get("ext") or Path(str(media_url).split("?")[0]).suffix.lstrip(".")).lower()
        is_video = extension in {"mp4", "m4v", "webm", "mov", "mkv"} or rec.get("video") or rec.get("is_video")
        title = rec.get("description") or rec.get("title") or title
        uploader = rec.get("username") or rec.get("author") or uploader
        items.append(
            {
                "id": str(rec.get("id") or rec.get("media_id") or i),
                "index": len(items),
                "type": "video" if is_video else "photo",
                "title": title,
                "thumbnail": rec.get("thumbnail") or (None if is_video else media_url),
                "image_url": None if is_video else media_url,
                "video_url": media_url if is_video else None,
                "width": rec.get("width"),
                "height": rec.get("height"),
                "duration": rec.get("duration"),
                "webpage_url": url,
                "qualities": {"mp4": True, "mp3": True} if is_video else {"jpg": True},
            }
        )
    if not items:
        return None
    return items, {"title": title, "uploader": uploader, "id": instagram_shortcode(url)}


def instagram_info(url: str, kind: str) -> dict:
    cookies = cookies_path("instagram") if has_cookies("instagram") else None
    if kind == "story" and not cookies:
        return {
            "ok": False,
            "platform": "instagram",
            "kind": "story",
            "needs_login": True,
            "error": _login_required_message("instagram", "story"),
        }

    last_exc: Exception | None = None
    info = None
    try:
        info = _ydl_extract(url, cookies)
    except Exception as exc:
        last_exc = exc
        login_msg = _login_required_message("instagram", kind, exc)
        # Still try instaloader / gallery-dl for public posts.
        if kind == "story":
            return {
                "ok": False,
                "platform": "instagram",
                "kind": kind,
                "needs_login": True,
                "error": login_msg or social_friendly_error("instagram", kind, exc),
            }

    items: list[dict] = []
    title = None
    uploader = None
    ident = instagram_shortcode(url)

    if info:
        if info.get("_type") == "playlist":
            title = info.get("title")
            uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id")
            ident = info.get("id") or ident
            for i, entry in enumerate(info.get("entries") or []):
                if not entry:
                    continue
                items.append(_item_from_entry(entry, i))
        else:
            title = info.get("title")
            uploader = info.get("uploader") or info.get("channel")
            ident = info.get("id") or ident
            items.append(_item_from_entry(info, 0))

    # If yt-dlp missed photos (common on carousels), fill from instaloader / gallery-dl.
    # Also refresh a generic "Video by …" title when instaloader can provide a caption.
    if ident and (
        not items
        or any(it["type"] == "photo" and not it.get("image_url") for it in items)
        or not title
        or title.lower().startswith(("video by", "photo by", "instagram"))
    ):
        extra = _instaloader_items(ident)
        if extra:
            ig_items, meta = extra
            if len(ig_items) >= len(items):
                items = ig_items
            generic = not title or title.lower().startswith(("video by", "photo by", "instagram"))
            title = (meta.get("title") if generic else title) or title or meta.get("title")
            uploader = uploader or meta.get("uploader")
        if not items or all(it["type"] == "photo" and not it.get("image_url") for it in items):
            gd = _gallery_dl_items(url, cookies)
            if gd:
                items, meta = gd
                title = title or meta.get("title")
                uploader = uploader or meta.get("uploader")

    if not items:
        if last_exc:
            return {
                "ok": False,
                "platform": "instagram",
                "kind": kind,
                "needs_login": "login" in str(last_exc).lower() or "log in" in str(last_exc).lower(),
                "error": social_friendly_error("instagram", kind, last_exc),
            }
        return {
            "ok": False,
            "platform": "instagram",
            "kind": kind,
            "error": "Couldn't find any photos or videos at that Instagram link.",
        }

    # If the user picked Reel but it's a photo post, still return the items (honest).
    inferred = kind
    if all(it["type"] == "photo" for it in items) and kind == "reel":
        inferred = "post"
    elif any(it["type"] == "video" for it in items) and kind == "post" and len(items) == 1:
        inferred = "reel" if "reel" in url else "post"

    return {
        "ok": True,
        "platform": "instagram",
        "kind": inferred,
        "id": ident,
        "title": (title or "Instagram post").strip() or "Instagram post",
        "uploader": uploader,
        "thumbnail": items[0].get("thumbnail"),
        "webpage_url": url,
        "items": items,
        "item_count": len(items),
        "needs_login": False,
    }


def _facebook_qualities(info: dict) -> dict:
    heights: list[int] = []
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none"):
            continue
        h = f.get("height")
        if isinstance(h, int) and h > 0:
            heights.append(h)
    max_h = max(heights) if heights else None
    return {
        "hd": {"available": bool(max_h and max_h >= 720) or bool(heights), "best": max_h},
        "sd": {"available": bool(heights) or True, "best": max((h for h in heights if h <= 480), default=min(heights) if heights else None)},
        "mp3": {"available": True},
        "max_height": max_h,
    }


def facebook_info(url: str, kind: str) -> dict:
    cookies = cookies_path("facebook") if has_cookies("facebook") else None
    if kind == "story" and not cookies:
        return {
            "ok": False,
            "platform": "facebook",
            "kind": "story",
            "needs_login": True,
            "error": _login_required_message("facebook", "story"),
        }
    try:
        info = _ydl_extract(url, cookies)
    except Exception as exc:
        return {
            "ok": False,
            "platform": "facebook",
            "kind": kind,
            "needs_login": "login" in str(exc).lower() or "log in" in str(exc).lower(),
            "error": social_friendly_error("facebook", kind, exc),
        }

    entries: list[dict] = []
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        title = info.get("title")
        uploader = info.get("uploader") or info.get("channel")
        ident = info.get("id")
    else:
        entries = [info]
        title = info.get("title")
        uploader = info.get("uploader") or info.get("channel")
        ident = info.get("id")

    items = [_item_from_entry(e, i) for i, e in enumerate(entries)]
    # Attach FB quality hints on video items.
    for item, entry in zip(items, entries):
        if item["type"] == "video":
            q = _facebook_qualities(entry)
            item["qualities"] = {"hd": True, "sd": True, "mp3": True, **{k: v for k, v in q.items()}}
            item["max_height"] = q.get("max_height")

    if not items:
        return {
            "ok": False,
            "platform": "facebook",
            "kind": kind,
            "error": "Couldn't find a video or photo at that Facebook link.",
        }

    return {
        "ok": True,
        "platform": "facebook",
        "kind": kind,
        "id": ident,
        "title": (title or "Facebook video").strip() or "Facebook video",
        "uploader": uploader,
        "thumbnail": items[0].get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "items": items,
        "item_count": len(items),
        "needs_login": False,
        "max_height": items[0].get("max_height") if items else None,
    }


def http_download(url: str, dest: Path, headers: dict | None = None) -> Path:
    req_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.instagram.com/",
    }
    if headers:
        req_headers.update({k: v for k, v in headers.items() if v})
    request = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
        ctype = (response.headers.get("Content-Type") or "").lower()
    if not data:
        raise RuntimeError("The image download was empty.")
    suffix = dest.suffix.lower()
    if data[:3] == b"\xff\xd8\xff":
        dest = dest.with_suffix(".jpg")
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        dest = dest.with_suffix(".png")
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        dest = dest.with_suffix(".webp")
    elif "jpeg" in ctype:
        dest = dest.with_suffix(".jpg")
    dest.write_bytes(data)
    return dest


def facebook_format_string(quality: str) -> str:
    if quality == "sd":
        return (
            "bv*[height<=480][vcodec^=avc1]+ba/"
            "bv*[height<=480][vcodec^=avc]+ba/"
            "bv*[height<=540]+ba/"
            "b[height<=540]/"
            "bv*+ba/b"
        )
    # hd (default)
    return (
        "bv*[height>=720][vcodec^=avc1]+ba[acodec^=mp4a]/"
        "bv*[height>=720][vcodec^=avc]+ba/"
        "bv*[height>=720]+ba/"
        "bv*[vcodec^=avc1]+ba/"
        "bv*+ba/b"
    )


def instagram_video_format() -> str:
    return "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*+ba/b"
