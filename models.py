"""Model catalogue for UNIK AI Studio (fal.ai endpoints).

Prices are APPROXIMATE, copied from each model's fal.ai page (Nov 2026).
fal bills your account; always check https://fal.ai/pricing for current rates.
"""

ASPECTS = ("16:9", "9:16")

# ---- price helpers -------------------------------------------------------

def _per_sec(audio_rate, silent_rate=None):
    """audio_rate applies when sound is on (or the model has a single rate)."""
    def f(o):
        rate = audio_rate if (silent_rate is None or o.get("audio")) else silent_rate
        return rate * int(o["duration"])
    return f


def _per_sec_by_res(table):
    def f(o):
        return table[o["resolution"]] * int(o["duration"])
    return f


def _flat(x):
    return lambda o: x


# ---- payload builders ----------------------------------------------------

def _veo(o):
    p = {
        "prompt": o["prompt"],
        "aspect_ratio": o["aspect"],
        "duration": f'{o["duration"]}s',
        "resolution": "720p",
        "generate_audio": bool(o["audio"]),
        "auto_fix": True,
    }
    if o.get("negative"):
        p["negative_prompt"] = o["negative"]
    if o.get("image_url"):
        p["image_url"] = o["image_url"]
    return p


def _kling(field=None, aspect=True, audio=True):
    def b(o):
        p = {"prompt": o["prompt"], "duration": str(o["duration"])}
        if aspect:
            p["aspect_ratio"] = o["aspect"]
        if audio:
            p["generate_audio"] = bool(o["audio"])
        if o.get("negative"):
            p["negative_prompt"] = o["negative"]
        if field:
            p[field] = o["image_url"]
        return p
    return b


def _wan(i2v=False):
    def b(o):
        p = {"prompt": o["prompt"], "duration": str(o["duration"]), "resolution": o["resolution"]}
        if not i2v:
            p["aspect_ratio"] = o["aspect"]
        else:
            p["image_url"] = o["image_url"]
        if o.get("negative"):
            p["negative_prompt"] = o["negative"]
        return p
    return b


def _img_aspect(extra=None):
    def b(o):
        p = {"prompt": o["prompt"], "aspect_ratio": o["aspect"], "num_images": 1}
        p.update(extra or {})
        return p
    return b


def _img_size(sizes, extra=None):
    def b(o):
        p = {"prompt": o["prompt"], "image_size": sizes[o["aspect"]]}
        p.update(extra or {})
        return p
    return b


FAL_SIZES = {"16:9": "landscape_16_9", "9:16": "portrait_16_9"}

# ---- catalogue -----------------------------------------------------------
# mode: t2v (prompt->video), t2i (prompt->image), i2v (image->video)

MODELS = [
    # Prompt -> Video
    dict(id="veo31-fast", mode="t2v", name="Google Veo 3.1 Fast", tag="Best value · sound",
         endpoint="fal-ai/veo3.1/fast", durations=[4, 6, 8], default_duration=8, audio=True,
         price=_per_sec(0.15, 0.10), price_note="$0.15/s with sound, $0.10/s silent (720p)", build=_veo),
    dict(id="veo31", mode="t2v", name="Google Veo 3.1", tag="Top quality · sound",
         endpoint="fal-ai/veo3.1", durations=[4, 6, 8], default_duration=8, audio=True,
         price=_per_sec(0.40, 0.20), price_note="$0.40/s with sound, $0.20/s silent (720p)", build=_veo),
    dict(id="kling3", mode="t2v", name="Kling 3.0 Pro", tag="Up to 15s · sound",
         endpoint="fal-ai/kling-video/v3/pro/text-to-video", durations=[5, 10, 15], default_duration=5, audio=True,
         price=_per_sec(0.168, 0.112), price_note="$0.168/s with sound, $0.112/s silent", build=_kling()),
    dict(id="kling26", mode="t2v", name="Kling 2.6 Pro", tag="Cinematic · sound",
         endpoint="fal-ai/kling-video/v2.6/pro/text-to-video", durations=[5, 10], default_duration=5, audio=True,
         price=_per_sec(0.14, 0.07), price_note="$0.14/s with sound, $0.07/s silent", build=_kling()),
    dict(id="kling25t", mode="t2v", name="Kling 2.5 Turbo Pro", tag="Fast · no sound",
         endpoint="fal-ai/kling-video/v2.5-turbo/pro/text-to-video", durations=[5, 10], default_duration=5, audio=False,
         price=_per_sec(0.07), price_note="$0.35 per 5s ($0.07/s)", build=_kling(audio=False)),
    dict(id="wan25", mode="t2v", name="Wan 2.5", tag="Cheapest",
         endpoint="fal-ai/wan-25-preview/text-to-video", durations=[5, 10], default_duration=5, audio=False,
         resolutions=["480p", "720p", "1080p"], default_resolution="480p",
         price=_per_sec_by_res({"480p": 0.05, "720p": 0.10, "1080p": 0.15}),
         price_note="$0.05/s 480p, $0.10/s 720p, $0.15/s 1080p", build=_wan()),

    # Image -> Video
    dict(id="veo31-fast-i2v", mode="i2v", name="Google Veo 3.1 Fast", tag="Best value · sound",
         endpoint="fal-ai/veo3.1/fast/image-to-video", durations=[4, 6, 8], default_duration=8, audio=True,
         price=_per_sec(0.15, 0.10), price_note="$0.15/s with sound, $0.10/s silent (720p)", build=_veo),
    dict(id="veo31-i2v", mode="i2v", name="Google Veo 3.1", tag="Top quality · sound",
         endpoint="fal-ai/veo3.1/image-to-video", durations=[4, 6, 8], default_duration=8, audio=True,
         price=_per_sec(0.40, 0.20), price_note="$0.40/s with sound, $0.20/s silent (720p)", build=_veo),
    dict(id="kling3-i2v", mode="i2v", name="Kling 3.0 Pro", tag="Up to 15s · sound",
         endpoint="fal-ai/kling-video/v3/pro/image-to-video", durations=[5, 10, 15], default_duration=5, audio=True,
         price=_per_sec(0.168, 0.112), price_note="$0.168/s with sound, $0.112/s silent",
         build=_kling(field="start_image_url", aspect=False)),
    dict(id="kling26-i2v", mode="i2v", name="Kling 2.6 Pro", tag="Cinematic · sound",
         endpoint="fal-ai/kling-video/v2.6/pro/image-to-video", durations=[5, 10], default_duration=5, audio=True,
         price=_per_sec(0.14, 0.07), price_note="$0.14/s with sound, $0.07/s silent",
         build=_kling(field="start_image_url", aspect=False)),
    dict(id="kling25t-i2v", mode="i2v", name="Kling 2.5 Turbo Pro", tag="Fast · no sound",
         endpoint="fal-ai/kling-video/v2.5-turbo/pro/image-to-video", durations=[5, 10], default_duration=5, audio=False,
         price=_per_sec(0.07), price_note="$0.35 per 5s ($0.07/s)",
         build=_kling(field="image_url", aspect=False, audio=False)),
    dict(id="wan25-i2v", mode="i2v", name="Wan 2.5", tag="Cheapest",
         endpoint="fal-ai/wan-25-preview/image-to-video", durations=[5, 10], default_duration=5, audio=False,
         resolutions=["480p", "720p", "1080p"], default_resolution="480p",
         price=_per_sec_by_res({"480p": 0.05, "720p": 0.10, "1080p": 0.15}),
         price_note="$0.05/s 480p, $0.10/s 720p, $0.15/s 1080p", build=_wan(i2v=True)),

    # Prompt -> Image (FREE, no key: Pollinations classic API, handled in app.py)
    dict(id="free-pollinations", mode="t2i", provider="pollinations", name="Free AI image (Pollinations)",
         tag="Free · no credit needed", endpoint="image.pollinations.ai", price=_flat(0.0),
         price_note="Free · 1024×576 (16:9) or 576×1024 (9:16)", build=None),
    # Prompt -> Image (fal.ai, paid)
    dict(id="nano-banana", mode="t2i", name="Google Nano Banana", tag="Great all-rounder",
         endpoint="fal-ai/nano-banana", price=_flat(0.039), price_note="$0.039 per image",
         build=_img_aspect({"output_format": "png"})),
    dict(id="nano-banana-pro", mode="t2i", name="Google Nano Banana Pro", tag="Best text in images",
         endpoint="fal-ai/nano-banana-pro", price=_flat(0.15), price_note="$0.15 per image (1K)",
         build=_img_aspect({"output_format": "png", "resolution": "1K"})),
    dict(id="seedream45", mode="t2i", name="Seedream 4.5", tag="Sharp 2K images",
         endpoint="fal-ai/bytedance/seedream/v4.5/text-to-image", price=_flat(0.04), price_note="$0.04 per image",
         build=_img_size({"16:9": {"width": 2560, "height": 1440}, "9:16": {"width": 1440, "height": 2560}},
                         {"num_images": 1})),
    dict(id="flux2-pro", mode="t2i", name="FLUX.2 Pro", tag="Photoreal",
         endpoint="fal-ai/flux-2-pro", price=_flat(0.03), price_note="about $0.03 per image",
         build=_img_size(FAL_SIZES, {"output_format": "jpeg"})),
    dict(id="flux-dev", mode="t2i", name="FLUX.1 dev", tag="Budget",
         endpoint="fal-ai/flux/dev", price=_flat(0.025), price_note="$0.025 per megapixel",
         build=_img_size(FAL_SIZES, {"num_images": 1})),
    dict(id="flux-schnell", mode="t2i", name="FLUX.1 schnell", tag="Cheapest · fastest",
         endpoint="fal-ai/flux/schnell", price=_flat(0.003), price_note="$0.003 per megapixel",
         build=_img_size(FAL_SIZES, {"num_images": 1})),
]

BY_ID = {m["id"]: m for m in MODELS}


def options_grid(m):
    """All valid option combos for a model, for client-side price lookup."""
    durs = m.get("durations") or [0]
    auds = [True, False] if m.get("audio") else [False]
    ress = m.get("resolutions") or [""]
    out = {}
    for d in durs:
        for a in auds:
            for r in ress:
                o = {"duration": d, "audio": a, "resolution": r}
                out[f"{d}|{int(a)}|{r}"] = round(m["price"](o), 3)
    return out


def public_catalogue(fal_enabled=True):
    res = []
    for m in MODELS:
        is_fal = m.get("provider", "fal") == "fal"
        # paid fal image models only appear when fal is switched on; video models are always
        # listed (shown disabled in the UI) so people can see what's coming.
        if is_fal and m["mode"] == "t2i" and not fal_enabled:
            continue
        res.append({
            "provider": m.get("provider", "fal"), "free": not is_fal,
            "id": m["id"], "mode": m["mode"], "name": m["name"], "tag": m.get("tag", ""),
            "endpoint": m["endpoint"],
            "durations": m.get("durations", []), "default_duration": m.get("default_duration"),
            "audio": bool(m.get("audio")),
            "resolutions": m.get("resolutions", []), "default_resolution": m.get("default_resolution", ""),
            "price_note": m["price_note"], "prices": options_grid(m),
        })
    return res
