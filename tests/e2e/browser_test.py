"""Headless Chrome E2E: no-key mode, then full flow against mock_fal.py with a password."""
import json, os, subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
SHOTS = os.path.join(HERE, "shots"); os.makedirs(SHOTS, exist_ok=True)
results = []

def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (" :: " + str(extra) if extra and not cond else ""))

def wait_up(url):
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1); return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("server didn't start: " + url)

def start(port, env):
    e = dict(os.environ); e.update(env)
    p = subprocess.Popen([PY, "-m", "gunicorn", "app:app", "--bind", f"127.0.0.1:{port}", "--workers", "1", "--threads", "8"],
                         cwd=ROOT, env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_up(f"http://127.0.0.1:{port}/health"); return p

def watch(page, errs):
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))

procs = []
try:
    mock = subprocess.Popen([PY, os.path.join(HERE, "mock_fal.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); procs.append(mock)
    wait_up("http://127.0.0.1:5056/_log")
    env_nokey = {"FAL_KEY": "fake-key:fake-secret", "VIDEO_ENABLED": "", "APP_PASSWORD": "", "POLLINATIONS_BASE": "http://127.0.0.1:5056"}
    procs.append(start(5055, env_nokey))
    env_mock = {"FAL_KEY": "fake-key:fake-secret", "APP_PASSWORD": "pass123", "FAL_QUEUE_BASE": "http://127.0.0.1:5056", "FAL_REST_BASE": "http://127.0.0.1:5056", "VIDEO_ENABLED": "true"}
    procs.append(start(5057, env_mock))

    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])

        # ---------------- A: default deploy (key present but VIDEO_ENABLED unset) -> free images only
        ctx = br.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True); page = ctx.new_page(); errs = []; watch(page, errs)
        page.goto("http://127.0.0.1:5055/"); page.wait_for_selector("#model option", state="attached")
        check("A opens on Prompt to Image", page.get_attribute(".tab[data-mode=t2i]", "aria-selected") == "true")
        check("A no warning banner", not page.is_visible("#banner"))
        check("A lock hidden when no password", not page.is_visible("#lock"))
        check("A Free badge + 2 Soon badges", page.is_visible(".badge.free") and page.locator(".badge.soon:visible").count() == 2)
        check("A only free image model listed", page.locator("#model option").count() == 1 and page.input_value("#model") == "free-pollinations")
        check("A button says Free", page.inner_text("#cost") == "Free", page.inner_text("#cost"))
        check("A form enabled for images", not page.is_disabled("#go") and not page.is_visible("#soon"))
        for mode in ("t2v", "i2v"):
            page.click(f".tab[data-mode={mode}]")
            check(f"A {mode} shows coming-soon note", page.is_visible("#soon") and "fal.ai credit" in page.inner_text("#soon"))
            check(f"A {mode} form disabled", page.is_disabled("#go") and page.is_disabled("#prompt"))
            check(f"A {mode} models still visible", page.locator("#model option").count() == 6)
        r = page.evaluate("""async()=>{const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'t2v',model:'veo31-fast',prompt:'abc test',aspect:'16:9',duration:8,audio:true})});return [r.status, await r.json()]}""")
        check("A server refuses video (video_disabled)", r[0] == 503 and r[1]["error"]["code"] == "video_disabled", r)
        page.screenshot(path=os.path.join(SHOTS, "video_soon.png"), full_page=False)
        page.click("#tryFree")
        check("A 'Make a free image' switches to images", page.get_attribute(".tab[data-mode=t2i]", "aria-selected") == "true" and not page.is_visible("#soon"))
        # free image 16:9 then 9:16
        page.click(".segbtn[data-aspect='16:9']"); page.fill("#prompt", "podcast studio wide neon"); page.click("#go")
        page.wait_for_selector(".card img", timeout=30000)
        nat = page.evaluate("new Promise(r=>{const i=document.querySelector('.card img'); const f=()=>r([i.naturalWidth,i.naturalHeight]); i.complete&&i.naturalWidth?f():i.onload=f})")
        check("A free 16:9 image in gallery", nat[0] > nat[1], nat)
        page.click(".segbtn[data-aspect='9:16']"); page.fill("#prompt", "founder portrait with mic"); page.click("#go")
        page.wait_for_function("document.querySelectorAll('.card img').length==2", timeout=30000)
        check("A free 9:16 card styled", "v916" in page.locator(".card").first.get_attribute("class"))
        check("A gallery shows Free", "Free" in page.inner_text(".card .meta small"))
        page.fill("#prompt", "BLOCKME something"); page.click("#go")
        page.wait_for_selector(".job.err", timeout=20000)
        check("A free content-policy message", "content rules" in page.inner_text(".job.err"), page.inner_text(".job.err"))
        with page.expect_download() as dl:
            page.locator(".card .btn.primary").first.click()
        check("A free image download", dl.value.suggested_filename.startswith("unik-ai-"), dl.value.suggested_filename)
        log = json.loads(urllib.request.urlopen("http://127.0.0.1:5056/_log").read())
        pl = [l for l in log if l["endpoint"] == "pollinations"]
        check("A pollinations sizes 1024x576 / 576x1024", [(l["args"]["width"], l["args"]["height"]) for l in pl[:2]] == [("1024", "576"), ("576", "1024")], pl)
        check("A each free image fetched once (server only)", len([l for l in pl if "BLOCKME" not in l["prompt"]]) == 2, pl)
        check("A no fal calls made", not [l for l in log if l["endpoint"] != "pollinations"], log)
        page.reload(); page.wait_for_selector(".card img")
        check("A gallery persists after reload", page.locator(".card").count() == 2)
        page.screenshot(path=os.path.join(SHOTS, "free_desktop.png"), full_page=True)
        check("A no console errors", not [e for e in errs if not any(s in e for s in ("503", "422"))], errs)
        ctx.close()
        urllib.request.urlopen("http://127.0.0.1:5056/_reset")

        # ---------------- B: mock fal + password
        ctx = br.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True); page = ctx.new_page(); errs = []; watch(page, errs)
        page.goto("http://127.0.0.1:5057/"); page.wait_for_selector("#lock:not([hidden])")
        check("B lock screen shown", page.is_visible("#lock"))
        page.fill("#pw", "wrong"); page.click("#lockForm button"); page.wait_for_selector("#lockMsg:not([hidden])")
        check("B wrong password message", "Wrong" in page.inner_text("#lockMsg"))
        page.fill("#pw", "pass123"); page.click("#lockForm button"); page.wait_for_selector("#lock", state="hidden")
        check("B unlocked", not page.is_visible("#lock"))
        check("B no banner with key", not page.is_visible("#banner"))
        check("B no Soon badges when video on", page.locator(".badge.soon:visible").count() == 0)
        page.click(".tab[data-mode=t2i]")
        check("B paid image models listed too", page.locator("#model option").count() == 7)

        # t2v 16:9 Veo 3.1 Fast, 4s silent
        page.click(".tab[data-mode=t2v]"); page.click(".segbtn[data-aspect='16:9']")
        page.select_option("#model", "veo31-fast"); page.click("#durChips .chip[data-v='4']"); page.click(".switch")
        check("B cost veo fast 4s silent = $0.40", page.inner_text("#cost") == "≈ $0.40", page.inner_text("#cost"))
        page.fill("#prompt", "podcast studio wide shot"); page.click("#go")
        page.wait_for_selector(".job"); check("B job card appears", page.is_visible(".job"))
        page.wait_for_selector(".card video", timeout=30000)
        check("B t2v result in gallery", page.locator(".card video").count() == 1)

        # t2i 9:16 nano banana
        page.click(".tab[data-mode=t2i]"); page.click(".segbtn[data-aspect='9:16']"); page.select_option("#model", "nano-banana")
        check("B t2i hides duration/sound", not page.is_visible("#durBox") and not page.is_visible("#audioBox"))
        page.fill("#prompt", "thumbnail portrait founder"); page.click("#go")
        page.wait_for_selector(".card img", timeout=20000)
        check("B t2i result in gallery", page.locator(".card img").count() == 1)
        check("B 9:16 card styled", "v916" in page.locator(".card").first.get_attribute("class"))

        # i2v 9:16 Kling 2.6 with upload
        page.click(".tab[data-mode=i2v]")
        page.set_input_files("#file", os.path.join(HERE, "photo.jpg"))
        page.wait_for_selector("#preview:not([hidden])")
        dims = page.evaluate("[document.querySelector('#preview').width, document.querySelector('#preview').height]")
        check("B image cropped to 9:16", abs(dims[0] / dims[1] - 9 / 16) < 0.01, dims)
        page.click(".segbtn[data-aspect='16:9']")
        dims2 = page.evaluate("[document.querySelector('#preview').width, document.querySelector('#preview').height]")
        check("B re-cropped to 16:9", abs(dims2[0] / dims2[1] - 16 / 9) < 0.01, dims2)
        page.click(".segbtn[data-aspect='9:16']")
        page.select_option("#model", "kling26-i2v")
        page.fill("#prompt", "portrait person smiles and waves"); page.click("#go")
        page.wait_for_function("document.querySelectorAll('.card video').length==2", timeout=30000)
        check("B i2v result in gallery", page.locator(".card video").count() == 2)

        # wan resolution chips
        page.click(".tab[data-mode=t2v]"); page.select_option("#model", "wan25")
        check("B wan shows quality chips", page.is_visible("#resBox") and page.locator("#resChips .chip").count() == 3)
        check("B wan 5s 480p = $0.25", page.inner_text("#cost") == "≈ $0.25", page.inner_text("#cost"))

        # error paths
        page.fill("#prompt", "BLOCKME please"); page.click("#go"); page.wait_for_selector("#formMsg:not([hidden])")
        check("B content policy message", "content rules" in page.inner_text("#formMsg"), page.inner_text("#formMsg"))
        page.fill("#prompt", "NOMONEY please"); page.click("#go"); page.wait_for_function("document.querySelector('#formMsg').textContent.includes('credit')")
        check("B out of credit message", "out of credit" in page.inner_text("#formMsg"))

        # download
        with page.expect_download() as dl:
            page.locator(".card .btn.primary").first.click()
        check("B download works", dl.value.suggested_filename.startswith("unik-ai-"), dl.value.suggested_filename)

        log = json.loads(urllib.request.urlopen("http://127.0.0.1:5056/_log").read())
        eps = [l["endpoint"] for l in log]
        check("B endpoints hit", eps[:3] == ["fal-ai/veo3.1/fast", "fal-ai/nano-banana", "fal-ai/kling-video/v2.6/pro/image-to-video"], eps)
        b0, b1, b2 = log[0]["body"], log[1]["body"], log[2]["body"]
        check("B veo payload", b0["aspect_ratio"] == "16:9" and b0["duration"] == "4s" and b0["generate_audio"] is False, b0)
        check("B nano payload", b1["aspect_ratio"] == "9:16", b1)
        check("B kling i2v payload", b2["start_image_url"].startswith("http://127.0.0.1:5056/files/") and b2["duration"] == "5", b2)

        # reload: gallery persists, prefs persist
        page.reload(); page.wait_for_selector(".card")
        check("B gallery persists after reload", page.locator(".card").count() == 3)
        page.screenshot(path=os.path.join(SHOTS, "mock_desktop.png"), full_page=True)
        bad = [e for e in errs if not any(s in e for s in ("401", "422", "402", "403"))]
        check("B no unexpected console errors", not bad, errs)
        ctx.close()

        # ---------------- C: mobile
        ctx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = ctx.new_page(); errs = []; watch(page, errs)
        page.goto("http://127.0.0.1:5055/"); page.wait_for_selector("#model option", state="attached")
        sw = page.evaluate("document.documentElement.scrollWidth")
        check("C mobile no horizontal scroll", sw <= 390, sw)
        page.screenshot(path=os.path.join(SHOTS, "mobile.png"), full_page=True)
        check("C no console errors", not errs, errs)
        ctx.close(); br.close()
finally:
    for p in procs:
        p.terminate()

fails = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
sys.exit(1 if fails else 0)
