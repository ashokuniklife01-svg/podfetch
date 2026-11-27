from datetime import timedelta

import pytest

from db import utcnow
from routing import (ValidationError, detect_shape, plan_schedule, route_post, route_video,
                     validate_destination, with_shorts_tag)


@pytest.mark.parametrize("w,h,d,exp", [
    (1080, 1920, 45, "short"), (1080, 1920, 180, "short"), (1080, 1920, 180.5, "long"),
    (1920, 1080, 30, "long"), (1080, 1080, 60, "short"), (0, 0, 30, "long"), (None, None, None, "long"),
    ("x", 1, 1, "long"), (1080, 1920, 0, "long"),
])
def test_detect_shape(w, h, d, exp):
    assert detect_shape(w, h, d) == exp


def test_route_video_platforms():
    assert route_video("youtube", 1080, 1920, 50) == "short"
    assert route_video("youtube", 1920, 1080, 50) == "long"
    assert route_video("instagram", 1920, 1080, 600) == "reel"
    assert route_video("facebook", 1080, 1920, 60) == "reel"
    assert route_video("facebook", 1080, 1920, 90) == "reel"
    assert route_video("facebook", 1080, 1920, 120) == "video"   # FB Reels API max 90 s
    assert route_video("facebook", 1920, 1080, 60) == "video"


def test_route_override():
    assert route_video("youtube", 1080, 1920, 50, "long") == "long"
    assert route_video("youtube", 1920, 1080, 500, "short") == "short"
    assert route_video("facebook", 1920, 1080, 30, "short") == "reel"
    assert route_video("facebook", 1080, 1920, 30, "long") == "video"
    assert route_video("youtube", 1080, 1920, 50, "bogus") == "short"
    with pytest.raises(ValueError):
        route_video("tiktok", 1, 1, 1)


def test_route_post_types():
    img = [{"width": 1, "height": 1}]
    assert route_post("photo", "youtube", img) is None
    assert route_post("photo", "instagram", img) == "photo"
    assert route_post("photo", "instagram", img * 3) == "carousel"
    assert route_post("photo", "facebook", img * 3) == "photos"
    assert route_post("text", "facebook") == "text"
    assert route_post("text", "instagram") is None
    assert route_post("text", "youtube") is None
    assert route_post("video", "youtube", [{"width": 720, "height": 1280, "duration": 30}]) == "short"


def test_plan_schedule():
    now = utcnow()
    assert plan_schedule("youtube", "long", None, now) == ("now", "queued")
    assert plan_schedule("youtube", "long", now + timedelta(days=60), now) == ("native", "queued")
    assert plan_schedule("facebook", "video", now + timedelta(minutes=5), now) == ("app", "waiting")
    assert plan_schedule("facebook", "video", now + timedelta(hours=3), now) == ("native", "queued")
    assert plan_schedule("facebook", "video", now + timedelta(days=40), now) == ("app", "waiting")
    assert plan_schedule("instagram", "reel", now + timedelta(hours=3), now) == ("app", "waiting")
    assert plan_schedule("instagram", "reel", None, now) == ("now", "queued")


def test_validate_youtube():
    ok = validate_destination("video", "youtube", "long", {"title": " Hi ", "body": "d"})
    assert ok["title"] == "Hi" and ok["schedule_at"] is None
    for bad in [{"title": ""}, {"title": "x" * 101}, {"title": "a<b"}, {"title": "t", "body": "x" * 5001},
                {"title": "t", "privacy": "secret"}]:
        with pytest.raises(ValidationError):
            validate_destination("video", "youtube", "long", bad)


def test_validate_schedule():
    now = utcnow()
    with pytest.raises(ValidationError):
        validate_destination("video", "youtube", "long", {"title": "t", "schedule_at": (now + timedelta(seconds=30)).isoformat()}, now)
    with pytest.raises(ValidationError):
        validate_destination("video", "youtube", "long", {"title": "t", "schedule_at": "nope"}, now)
    with pytest.raises(ValidationError):
        validate_destination("video", "youtube", "long", {"title": "t", "schedule_at": (now + timedelta(days=200)).isoformat()}, now)
    v = validate_destination("video", "youtube", "long", {"title": "t", "schedule_at": "2099-01-01T10:00:00+05:30"},
                             now.replace(year=2098, month=12, day=1))
    assert v["schedule_at"].utcoffset() == timedelta(0) and v["schedule_at"].hour == 4 and v["schedule_at"].minute == 30


def test_validate_ig_fb():
    with pytest.raises(ValidationError):
        validate_destination("video", "instagram", "reel", {"body": "x" * 2201})
    with pytest.raises(ValidationError):
        validate_destination("video", "instagram", "reel", {"body": " ".join(f"#t{i}" for i in range(31))})
    with pytest.raises(ValidationError):
        validate_destination("text", "facebook", "text", {"body": ""})
    with pytest.raises(ValidationError):
        validate_destination("text", "facebook", "text", {"link": "ftp://x"})
    assert validate_destination("text", "facebook", "text", {"link": "https://a.b"})["link"] == "https://a.b"
    with pytest.raises(ValidationError):
        validate_destination("photo", "youtube", None, {"title": "t"})


def test_shorts_tag():
    assert with_shorts_tag("Hello", "") == ("Hello #Shorts", "")
    assert with_shorts_tag("Hi #shorts", "x") == ("Hi #shorts", "x")
    t, d = with_shorts_tag("x" * 95, "desc")
    assert t == "x" * 95 and d.endswith("#Shorts")
