(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const LS_PW = "unik_ai_pw", LS_GAL = "unik_ai_gallery", LS_JOBS = "unik_ai_jobs", LS_PREF = "unik_ai_prefs";
  const MODE_TEXT = {
    t2v: { lbl: "Describe your video", ph: "A confident Indian entrepreneur walks into a modern glass office at sunrise, cinematic, slow camera push-in", btn: "✨ Generate video" },
    t2i: { lbl: "Describe your image", ph: "YouTube thumbnail: excited young founder holding a glowing rupee coin, bold purple background, studio lighting", btn: "✨ Generate image" },
    i2v: { lbl: "How should the image move?", ph: "The person smiles and turns to the camera, gentle camera push-in, natural light", btn: "✨ Animate image" },
  };

  const st = {
    cfg: null, mode: "t2v", aspect: "16:9", model: {}, duration: {}, resolution: {}, audio: true,
    img: null, imgBlob: null, imgUrl: null, submitting: false,
  };

  // ---------- storage helpers
  const load = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (_) { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* storage full or blocked */ } };

  // ---------- API
  class ApiErr extends Error { constructor(code, msg, status) { super(msg); this.code = code; this.status = status; } }
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    const pw = localStorage.getItem(LS_PW);
    if (pw) headers["X-App-Password"] = pw;
    let r;
    try { r = await fetch(path, Object.assign({}, opts, { headers })); }
    catch (_) { throw new ApiErr("network", "No connection to the studio. Check your internet and try again.", 0); }
    let data = null;
    try { data = await r.json(); } catch (_) { /* non-JSON */ }
    if (!r.ok || !data || data.ok === false) {
      const e = (data && data.error) || {};
      let msg = e.message;
      if (!msg) msg = r.status === 502 || r.status === 503 ? "The studio is waking up or busy. Please try again in a few seconds." : "Something went wrong (" + r.status + "). Please try again.";
      const err = new ApiErr(e.code || "http_" + r.status, msg, r.status);
      if (err.code === "password") showLock(localStorage.getItem(LS_PW) ? "Wrong or changed password. Please enter it again." : "");
      throw err;
    }
    return data;
  }
  const postJSON = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ---------- UI helpers
  function el(tag, attrs = {}, kids = []) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
    }
    for (const c of [].concat(kids)) if (c) e.append(c);
    return e;
  }
  const money = (x) => x == null ? "" : x === 0 ? "Free" : "≈ $" + (x < 0.1 ? x.toFixed(3).replace(/0$/, "") : x.toFixed(2));
  function formError(msg) { const m = $("#formMsg"); m.textContent = msg || ""; m.hidden = !msg; }
  const models = () => st.cfg ? st.cfg.models.filter((m) => m.mode === st.mode) : [];
  const curModel = () => st.cfg && st.cfg.models.find((m) => m.id === st.model[st.mode]);

  // ---------- lock screen
  function showLock(msg) {
    $("#lock").hidden = false;
    const m = $("#lockMsg"); m.textContent = msg || ""; m.hidden = !msg;
    setTimeout(() => $("#pw").focus(), 30);
  }
  $("#lockForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const pw = $("#pw").value;
    if (!pw) return;
    try {
      const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) { const m = $("#lockMsg"); m.textContent = (d.error && d.error.message) || "Wrong password."; m.hidden = false; return; }
      localStorage.setItem(LS_PW, pw);
      $("#lock").hidden = true; $("#pw").value = "";
      resumeJobs();
    } catch (_) { const m = $("#lockMsg"); m.textContent = "No connection. Try again."; m.hidden = false; }
  });

  // ---------- form rendering
  function renderModels() {
    const sel = $("#model"); sel.innerHTML = "";
    for (const m of models()) {
      const opt = el("option", { value: m.id, text: m.name + " · " + m.tag });
      sel.append(opt);
    }
    if (!st.model[st.mode] || !models().some((m) => m.id === st.model[st.mode])) st.model[st.mode] = models()[0] && models()[0].id;
    sel.value = st.model[st.mode];
    renderOptions();
  }
  function chipRow(box, values, cur, fmt, onPick) {
    box.innerHTML = "";
    for (const v of values) {
      box.append(el("button", { type: "button", class: "chip" + (String(v) === String(cur) ? " active" : ""), "data-v": v, text: fmt(v), onclick: () => onPick(v) }));
    }
  }
  function renderOptions() {
    const m = curModel(); if (!m) return;
    $("#modelNote").textContent = "Approx. price: " + m.price_note;
    // durations
    $("#durBox").hidden = !m.durations.length;
    if (m.durations.length) {
      if (!m.durations.includes(st.duration[m.id])) st.duration[m.id] = m.default_duration;
      chipRow($("#durChips"), m.durations, st.duration[m.id], (v) => v + " sec", (v) => { st.duration[m.id] = v; renderOptions(); });
    }
    $("#resBox").hidden = !m.resolutions.length;
    if (m.resolutions.length) {
      if (!m.resolutions.includes(st.resolution[m.id])) st.resolution[m.id] = m.default_resolution;
      chipRow($("#resChips"), m.resolutions, st.resolution[m.id], (v) => v, (v) => { st.resolution[m.id] = v; renderOptions(); });
    }
    $("#audioBox").hidden = !m.audio;
    $("#audio").checked = st.audio; $("#audioTxt").textContent = st.audio ? "On" : "Off";
    updateCost();
    savePrefs();
  }
  function priceFor(m) {
    const d = m.durations.length ? st.duration[m.id] : 0;
    const a = m.audio && st.audio ? 1 : 0;
    const r = m.resolutions.length ? st.resolution[m.id] : "";
    return m.prices[d + "|" + a + "|" + r];
  }
  function updateCost() {
    const m = curModel(); $("#cost").textContent = m ? money(priceFor(m)) : "";
    $("#priceHint").textContent = m && m.free ? "Free images by Pollinations.ai · no account or credit needed. One image at a time." : "Prices are approximate fal.ai rates and are charged to your fal.ai account.";
  }

  const videoOn = () => !!(st.cfg && st.cfg.fal_enabled);
  const locked = (mode) => mode !== "t2i" && !videoOn();
  function setMode(mode) {
    st.mode = mode;
    const off = locked(mode);
    $("#soon").hidden = !off;
    $("#fields").disabled = off;
    document.querySelectorAll(".tab").forEach((t) => { const on = t.dataset.mode === mode; t.classList.toggle("active", on); t.setAttribute("aria-selected", on); });
    const t = MODE_TEXT[mode];
    $("#promptLbl").textContent = t.lbl; $("#prompt").placeholder = t.ph; $("#go").firstElementChild.textContent = t.btn;
    $("#imgBox").hidden = mode !== "i2v";
    $("#negative").closest(".more").hidden = mode === "t2i";
    formError("");
    renderModels();
  }
  function applyAvailability() {
    const on = videoOn();
    document.querySelectorAll(".badge.soon").forEach((b) => { b.hidden = on; });
    document.querySelectorAll(".badge.free").forEach((b) => { b.hidden = !st.cfg.models.some((m) => m.free); });
  }
  function setAspect(a) {
    st.aspect = a;
    document.querySelectorAll(".segbtn").forEach((b) => b.classList.toggle("active", b.dataset.aspect === a));
    if (st.img) cropImage();
    savePrefs();
  }
  function savePrefs() { save(LS_PREF, { mode: st.mode, aspect: st.aspect, model: st.model, duration: st.duration, resolution: st.resolution, audio: st.audio }); }

  // ---------- image handling (crop to chosen shape, max 1920px)
  async function pickFile(f) {
    formError("");
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) { formError("Please choose a JPG, PNG or WEBP image."); return; }
    if (f.size > 25 * 1024 * 1024) { formError("That image is very large. Please use one under 25 MB."); return; }
    const url = URL.createObjectURL(f);
    try {
      const img = new Image();
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
      if (img.naturalWidth < 300 || img.naturalHeight < 300) { formError("That image is too small. Please use one at least 300×300 pixels."); return; }
      st.img = img; cropImage();
    } catch (_) { formError("Couldn't read that image. Try another file."); }
  }
  function cropImage() {
    const img = st.img, [aw, ah] = st.aspect === "16:9" ? [16, 9] : [9, 16];
    const W = img.naturalWidth, H = img.naturalHeight;
    let sw = W, sh = Math.round(W * ah / aw);
    if (sh > H) { sh = H; sw = Math.round(H * aw / ah); }
    const sx = Math.round((W - sw) / 2), sy = Math.round((H - sh) / 2);
    const scale = Math.min(1, 1920 / Math.max(sw, sh));
    const c = $("#preview"); c.width = Math.round(sw * scale); c.height = Math.round(sh * scale);
    c.getContext("2d").drawImage(img, sx, sy, sw, sh, 0, 0, c.width, c.height);
    c.hidden = false; $("#dropInner").hidden = true; $("#clearImg").hidden = false;
    st.imgUrl = null; st.imgBlob = null;
    c.toBlob((b) => { st.imgBlob = b; }, "image/jpeg", 0.92);
  }
  function clearImage() {
    st.img = st.imgBlob = st.imgUrl = null;
    $("#preview").hidden = true; $("#dropInner").hidden = false; $("#clearImg").hidden = true; $("#file").value = "";
  }
  async function ensureUpload() {
    if (st.imgUrl) return st.imgUrl;
    if (!st.imgBlob) await new Promise((ok) => $("#preview").toBlob((b) => { st.imgBlob = b; ok(); }, "image/jpeg", 0.92));
    const fd = new FormData(); fd.append("image", st.imgBlob, "start.jpg");
    const d = await api("/api/upload", { method: "POST", body: fd });
    st.imgUrl = d.url; return d.url;
  }

  // ---------- jobs
  const jobs = load(LS_JOBS, []).filter((j) => Date.now() - j.started < 6 * 3600e3);
  const saveJobs = () => save(LS_JOBS, jobs.filter((j) => j.state !== "done" && j.state !== "error").map(({ node, timer, ui, fails, ...rest }) => rest));

  async function submit(ev) {
    ev.preventDefault();
    if (st.submitting) return;
    formError("");
    const m = curModel();
    const prompt = $("#prompt").value.trim();
    if (!st.cfg) { formError("The studio is still loading. Please wait a second."); return; }
    if (locked(st.mode) || (!m.free && !videoOn())) { formError("Video needs fal.ai credit · coming soon. Prompt to Image is free right now."); return; }
    if (st.mode === "i2v" && !st.img) { formError("Please choose a starting image first."); return; }
    if (prompt.length < 3) { formError(prompt ? "Please describe it in a few more words." : "Please type a prompt describing what you want."); $("#prompt").focus(); return; }
    st.submitting = true; $("#go").disabled = true;
    const label = $("#go").firstElementChild.textContent;
    $("#go").firstElementChild.textContent = st.mode === "i2v" && !st.imgUrl ? "Uploading image…" : "Sending…";
    if (m.free) {
      try { await freeImage(m, prompt); }
      finally { st.submitting = false; $("#go").disabled = false; $("#go").firstElementChild.textContent = label; }
      return;
    }
    try {
      const body = { mode: st.mode, model: m.id, prompt, aspect: st.aspect, negative: $("#negative").value.trim() };
      if (m.durations.length) body.duration = st.duration[m.id];
      if (m.resolutions.length) body.resolution = st.resolution[m.id];
      if (m.audio) body.audio = st.audio;
      if (st.mode === "i2v") body.image_url = await ensureUpload();
      const d = await postJSON("/api/generate", body);
      const job = { id: d.request_id, token: d.job, mode: st.mode, model: m.id, modelName: m.name, prompt, aspect: st.aspect, est: d.estimate, started: Date.now(), state: "queued" };
      jobs.unshift(job); saveJobs(); startJob(job);
    } catch (e) { formError(e.message); }
    finally { st.submitting = false; $("#go").disabled = false; $("#go").firstElementChild.textContent = label; }
  }

  // ---------- free images (Pollinations via our server, with a direct-from-browser fallback)
  function pollUrl(prompt, aspect) {
    const [w, h] = aspect === "16:9" ? [1024, 576] : [576, 1024];
    const seed = Math.floor(Math.random() * 16777215);
    return "https://image.pollinations.ai/prompt/" + encodeURIComponent(prompt.replace(/\s+/g, " ")) + "?width=" + w + "&height=" + h + "&seed=" + seed + "&nologo=true";
  }
  async function directImage(url) {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 110000);
    try {
      const r = await fetch(url, { signal: ctl.signal });
      const ct = r.headers.get("content-type") || "";
      if (!r.ok || !ct.startsWith("image/")) throw new Error("http " + r.status);
      await r.blob(); return url;
    } finally { clearTimeout(t); }
  }
  async function freeImage(m, prompt) {
    const job = { id: "free-" + Date.now(), mode: "t2i", model: m.id, modelName: "Free AI image", prompt, aspect: st.aspect, est: 0, started: Date.now(), state: "running", free: true };
    startJob(job, true);
    job.ui.status.textContent = "Painting your free image… (usually 5–20 seconds)";
    const tick = setInterval(() => setJobProgress(job), 500);
    try {
      let url;
      try {
        const d = await postJSON("/api/free-image", { prompt, aspect: st.aspect });
        return finishJob(job, [{ type: "image", url: d.outputs[0].url, public_url: d.outputs[0].public_url }]);
      } catch (e) {
        if (!["free_limit", "free_busy", "free_failed", "free_timeout", "network", "http_502", "http_503", "http_504"].includes(e.code)) throw e;
        job.ui.status.textContent = "Trying another route…";
        try { url = await directImage(pollUrl(prompt, st.aspect)); }
        catch (_) { throw e; }
      }
      finishJob(job, [{ type: "image", url }]);
    } catch (e) { failJob(job, e.message); }
    finally { clearInterval(tick); }
  }

  function expectedSecs(job) { return job.free ? 10 : job.mode === "t2i" ? 15 : 150; }
  function startJob(job, noPoll) {
    const bar = el("i"), status = el("div", { class: "js", text: "Waiting in line…" });
    const btns = el("div", { class: "jbtns" });
    job.node = el("div", { class: "job" }, [
      el("div", { class: "jt" }, [el("b", { text: (job.mode === "t2i" ? "🖼️ " : "🎬 ") + job.modelName + " · " + job.aspect }), el("span", { class: "jtime", text: "0s" })]),
      el("div", { class: "jp", text: job.prompt }),
      el("div", { class: "bar" }, bar), status, btns,
    ]);
    job.ui = { bar, status, btns, time: job.node.querySelector(".jtime") };
    $("#jobs").prepend(job.node);
    job.fails = 0;
    if (!noPoll) poll(job);
  }
  function setJobProgress(job) {
    const secs = Math.round((Date.now() - job.started) / 1000);
    job.ui.time.textContent = secs >= 60 ? Math.floor(secs / 60) + "m " + (secs % 60) + "s" : secs + "s";
    const p = Math.min(95, 100 * (1 - Math.exp(-secs / expectedSecs(job))));
    job.ui.bar.style.width = (job.state === "queued" ? Math.min(p, 10) : Math.max(p, 12)) + "%";
  }
  async function poll(job) {
    const limit = job.mode === "t2i" ? 5 * 60e3 : 20 * 60e3;
    setJobProgress(job);
    if (Date.now() - job.started > limit && !job.extended) {
      return failJob(job, "This is taking much longer than normal.", true);
    }
    try {
      const d = await api("/api/status/" + job.token);
      job.fails = 0;
      if (d.state === "queued") { job.state = "queued"; job.ui.status.textContent = d.position != null ? "Waiting in line (position " + (d.position + 1) + ")…" : "Waiting in line…"; }
      else if (d.state === "running") { job.state = "running"; job.ui.status.textContent = job.mode === "t2i" ? "Painting your image…" : "Creating your video… (usually 1–4 minutes)"; }
      else if (d.state === "done") return finishJob(job, d.outputs);
      else if (d.state === "error") return failJob(job, d.error.message);
    } catch (e) {
      if (e.code === "password") { job.ui.status.textContent = "Paused: enter the password to continue."; return; }
      if (["bad_job", "bad_key", "no_key", "no_credits", "forbidden"].includes(e.code)) return failJob(job, e.message);
      job.fails++;
      if (job.fails > 8) return failJob(job, e.message, true);
      job.ui.status.textContent = "Connection hiccup, retrying…";
    }
    saveJobs();
    job.timer = setTimeout(() => poll(job), job.mode === "t2i" ? 2000 : 4000);
  }
  function finishJob(job, outputs) {
    job.state = "done"; saveJobs();
    const gal = load(LS_GAL, []);
    for (const o of outputs) gal.unshift({ id: job.id + ":" + o.url.slice(-12), type: o.type, url: o.url, public_url: o.public_url, prompt: job.prompt, model: job.modelName, mode: job.mode, aspect: job.aspect, est: job.est, at: Date.now() });
    saveGallery(gal.slice(0, 100));
    job.node.remove();
    renderGallery(true);
  }
  function failJob(job, msg, canRetry) {
    job.state = canRetry ? "stalled" : "error";
    if (!canRetry) saveJobs();
    job.node.classList.add("err");
    job.ui.bar.style.width = "100%";
    job.ui.status.textContent = "⚠️ " + msg;
    job.ui.btns.innerHTML = "";
    if (canRetry) job.ui.btns.append(el("button", { class: "btn small ghost", type: "button", text: "Keep checking", onclick: () => { job.extended = true; job.fails = 0; job.node.classList.remove("err"); job.ui.btns.innerHTML = ""; poll(job); } }));
    job.ui.btns.append(el("button", { class: "btn small ghost", type: "button", text: "Dismiss", onclick: () => { job.state = "error"; saveJobs(); job.node.remove(); } }));
  }
  function resumeJobs() { for (const j of jobs) if (!j.node && j.state !== "done" && j.state !== "error") startJob(j); }

  // ---------- gallery
  // Free images are kept as small data URLs so they never need re-fetching. If browser storage
  // fills up, the oldest ones fall back to their public Pollinations link.
  function saveGallery(gal) {
    for (;;) {
      try { localStorage.setItem(LS_GAL, JSON.stringify(gal)); return; }
      catch (_) {
        let i = gal.length - 1;
        while (i >= 0 && !(gal[i].url || "").startsWith("data:")) i--;
        if (i >= 0) { if (gal[i].public_url) gal[i].url = gal[i].public_url; else gal.splice(i, 1); }
        else if (gal.length) gal.pop();
        else return;
      }
    }
  }
  async function download(item, btn) {
    const ext = item.type === "video" ? "mp4" : item.url.startsWith("data:image/jpeg") || item.public_url ? "jpg" : (item.url.match(/\.(png|jpe?g|webp)(\?|$)/i) || [, "png"])[1];
    const name = "unik-ai-" + new Date(item.at).toISOString().slice(0, 19).replace(/[:T]/g, "-") + "." + ext;
    const old = btn.textContent; btn.textContent = "Downloading…"; btn.disabled = true;
    try {
      const r = await fetch(item.url, { mode: "cors" });
      if (!r.ok) throw new Error("http " + r.status);
      const blob = await r.blob();
      const a = el("a", { href: URL.createObjectURL(blob), download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    } catch (_) {
      const src = item.url.startsWith("data:") ? item.public_url : item.url;
      if (src) window.location.href = "/api/download?url=" + encodeURIComponent(src);
    } finally { btn.textContent = old; btn.disabled = false; }
  }
  function renderGallery(flash) {
    const gal = load(LS_GAL, []), box = $("#gallery");
    box.innerHTML = "";
    $("#emptyGallery").hidden = gal.length > 0;
    $("#clearGallery").hidden = gal.length === 0;
    gal.forEach((it, i) => {
      const media = it.type === "video"
        ? el("video", { src: it.url, controls: "", playsinline: "", preload: "metadata", loop: "" })
        : el("img", { src: it.url, alt: it.prompt.slice(0, 120), loading: "lazy" });
      media.addEventListener("error", () => {
        if (it.public_url && media.src !== it.public_url && it.type === "image") { media.src = it.public_url; return; }
        media.replaceWith(el("div", { class: "empty", text: "Preview expired" })); });
      const dl = el("button", { class: "btn small primary", type: "button", text: "⬇ Download" });
      dl.addEventListener("click", () => download(it, dl));
      const card = el("div", { class: "card" + (it.aspect === "9:16" ? " v916" : "") }, [
        el("div", { class: "media" }, media),
        el("div", { class: "meta" }, [
          el("p", { text: it.prompt }),
          el("small", { text: it.model + " · " + it.aspect + (it.est != null ? " · " + money(it.est) : "") + " · " + new Date(it.at).toLocaleString() }),
          el("div", { class: "cbtns" }, [
            dl,
            el("button", { class: "btn small ghost", type: "button", text: "Reuse prompt", onclick: () => { setMode(it.mode); setAspect(it.aspect); $("#prompt").value = it.prompt; updateCount(); window.scrollTo({ top: 0, behavior: "smooth" }); } }),
            el("button", { class: "btn small ghost", type: "button", title: "Remove from list", "aria-label": "Remove", text: "✕", onclick: () => { const g = load(LS_GAL, []); g.splice(i, 1); saveGallery(g); renderGallery(); } }),
          ]),
        ]),
      ]);
      box.append(card);
    });
    if (flash && box.firstChild) box.firstChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const updateCount = () => { $("#pcount").textContent = $("#prompt").value.length; };

  // ---------- wiring
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { setMode(t.dataset.mode); savePrefs(); }));
  document.querySelectorAll(".segbtn").forEach((b) => b.addEventListener("click", () => setAspect(b.dataset.aspect)));
  $("#model").addEventListener("change", (e) => { st.model[st.mode] = e.target.value; renderOptions(); });
  $("#audio").addEventListener("change", (e) => { st.audio = e.target.checked; renderOptions(); });
  $("#prompt").addEventListener("input", updateCount);
  $("#form").addEventListener("submit", submit);
  $("#file").addEventListener("change", (e) => pickFile(e.target.files[0]));
  $("#clearImg").addEventListener("click", clearImage);
  $("#tryFree").addEventListener("click", () => { setMode("t2i"); savePrefs(); $("#prompt").focus(); });
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));
  $("#clearGallery").addEventListener("click", () => { if (confirm("Clear your creations list on this device? (Download anything you want to keep first.)")) { save(LS_GAL, []); renderGallery(); } });

  async function init() {
    const p = load(LS_PREF, {});
    Object.assign(st, { aspect: p.aspect || "16:9", model: p.model || {}, duration: p.duration || {}, resolution: p.resolution || {}, audio: p.audio !== false });
    renderGallery();
    let tries = 0;
    while (!st.cfg) {
      try { st.cfg = await api("/api/config"); }
      catch (e) {
        if (++tries > 6) { const b = $("#banner"); b.textContent = "Can't reach the studio right now. Please refresh the page in a minute."; b.hidden = false; return; }
        await new Promise((r) => setTimeout(r, 2500));
      }
    }
    applyAvailability();
    setAspect(st.aspect);
    let mode = ["t2v", "t2i", "i2v"].includes(p.mode) ? p.mode : (videoOn() ? "t2v" : "t2i");
    if (locked(mode)) mode = "t2i";
    setMode(mode);
    if (st.cfg.password_required && !localStorage.getItem(LS_PW)) showLock();
    else resumeJobs();
  }
  init();
})();
