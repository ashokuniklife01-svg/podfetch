"""Pure logic: video routing, schedule planning and destination validation."""
from datetime import timedelta

from db import parse_iso, utcnow

SHORT_MAX_SECONDS = 180          # YouTube Shorts limit (3 min)
FB_REEL_MAX_SECONDS = 90         # Facebook Reels via API: 3-90 s
IG_REEL_MAX_SECONDS = 15 * 60    # Instagram Reels via API
FB_NATIVE_MIN = timedelta(minutes=10)
FB_NATIVE_MAX = timedelta(days=29)
IG_CONTAINER_LEAD = timedelta(hours=20)   # IG containers expire after 24 h

LIMITS = {
    "yt_title": 100, "yt_desc": 5000, "ig_caption": 2200, "ig_hashtags": 30,
    "fb_message": 63000, "fb_title": 255, "carousel_max": 10,
}


def detect_shape(width, height, duration):
    """Returns 'short' or 'long' for a video's natural format."""
    try:
        w, h, d = float(width or 0), float(height or 0), float(duration or 0)
    except (TypeError, ValueError):
        return "long"
    if w <= 0 or h <= 0 or d <= 0:
        return "long"
    vertical = h >= w          # square counts as Shorts-eligible too
    return "short" if (vertical and d <= SHORT_MAX_SECONDS) else "long"


def route_video(platform, width, height, duration, override=None):
    """Decide the destination kind for a video on a platform.

    override: None | 'short' | 'long' (the user's manual toggle).
    Returns kind: youtube -> 'short'|'long'; instagram -> 'reel';
    facebook -> 'reel'|'video'.
    """
    shape = override if override in ("short", "long") else detect_shape(width, height, duration)
    if platform == "youtube":
        return shape
    if platform == "instagram":
        return "reel"           # the IG API publishes every video as a Reel
    if platform == "facebook":
        try:
            d = float(duration or 0)
        except (TypeError, ValueError):
            d = 0
        if shape == "short" and 0 < d <= FB_REEL_MAX_SECONDS:
            return "reel"
        return "video"
    raise ValueError(f"unknown platform {platform}")


def route_post(post_type, platform, media=None, override=None):
    """Kind for any post type, or None when the platform can't take it."""
    media = media or []
    if post_type == "video":
        m = media[0] if media else {}
        return route_video(platform, m.get("width"), m.get("height"), m.get("duration"), override)
    if post_type == "photo":
        if platform == "youtube":
            return None          # Community posts are not available in the API
        if platform == "instagram":
            return "carousel" if len(media) > 1 else "photo"
        return "photos" if len(media) > 1 else "photo"
    if post_type == "text":
        return "text" if platform == "facebook" else None
    raise ValueError("bad post type")


def plan_schedule(platform, kind, schedule_at, now=None):
    """Decide who holds a scheduled post.

    Returns (mode, initial_status):
      ('now', 'queued')          post immediately
      ('native', 'queued')       upload now; the platform publishes at the time
      ('app', 'waiting')         app publishes at the time (cron/worker)
    """
    now = now or utcnow()
    if schedule_at is None:
        return "now", "queued"
    delta = schedule_at - now
    if platform == "youtube":
        return "native", "queued"              # publishAt
    if platform == "facebook":
        if FB_NATIVE_MIN <= delta <= FB_NATIVE_MAX:
            return "native", "queued"          # scheduled_publish_time
        return "app", "waiting"
    return "app", "waiting"                    # Instagram has no API scheduling


def ig_prepare_at(schedule_at):
    """When to create the IG container for an app-scheduled post."""
    return schedule_at - IG_CONTAINER_LEAD


class ValidationError(Exception):
    pass


def with_shorts_tag(title, description):
    """Add #Shorts once (to the description, or the title if it fits)."""
    title = title or ""
    description = description or ""
    if "#shorts" in title.lower() or "#shorts" in description.lower():
        return title, description
    if len(title) + 8 <= LIMITS["yt_title"]:
        return f"{title} #Shorts".strip(), description
    return title, (description + "\n\n#Shorts").strip()


def validate_destination(post_type, platform, kind, d, now=None):
    """Validate one destination payload. Returns cleaned dict or raises."""
    now = now or utcnow()
    if kind is None:
        names = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook"}
        raise ValidationError(f"{names.get(platform, platform)} can't take this post type")
    title = (d.get("title") or "").strip()
    body = (d.get("body") or "").strip()
    link = (d.get("link") or "").strip()
    privacy = (d.get("privacy") or "public").strip()
    sched_raw = d.get("schedule_at")
    schedule_at = None
    if sched_raw:
        try:
            schedule_at = parse_iso(sched_raw)
        except ValueError:
            raise ValidationError("Invalid schedule time")
        if schedule_at < now + timedelta(minutes=2):
            raise ValidationError("Schedule time must be at least 2 minutes from now")
        if schedule_at > now + timedelta(days=180):
            raise ValidationError("Schedule time must be within 6 months")

    if platform == "youtube":
        if not title:
            raise ValidationError("Title is required")
        if len(title) > LIMITS["yt_title"]:
            raise ValidationError(f"Title is over {LIMITS['yt_title']} characters")
        if "<" in title or ">" in title:
            raise ValidationError("Title can't contain < or >")
        if len(body) > LIMITS["yt_desc"]:
            raise ValidationError(f"Description is over {LIMITS['yt_desc']} characters")
        if privacy not in ("public", "unlisted", "private"):
            raise ValidationError("Bad visibility")
    elif platform == "instagram":
        if len(body) > LIMITS["ig_caption"]:
            raise ValidationError(f"Caption is over {LIMITS['ig_caption']} characters")
        if body.count("#") > LIMITS["ig_hashtags"]:
            raise ValidationError("Instagram allows at most 30 hashtags")
    elif platform == "facebook":
        if len(body) > LIMITS["fb_message"]:
            raise ValidationError("Text is too long")
        if len(title) > LIMITS["fb_title"]:
            raise ValidationError("Title is too long")
        if kind == "text":
            if not body and not link:
                raise ValidationError("Write something or add a link")
            if link and not link.lower().startswith(("http://", "https://")):
                raise ValidationError("Link must start with http:// or https://")
    return {"title": title, "body": body, "link": link, "privacy": privacy,
            "schedule_at": schedule_at, "share_to_feed": bool(d.get("share_to_feed", True))}
