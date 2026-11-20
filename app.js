// UNIK Tools – everything runs locally in the browser.
const $ = (id) => document.getElementById(id);
const abs = (p) => new URL(p, document.baseURI).href;
const MB = 1024 * 1024;
const fmtSize = (b) => b >= 1024 * MB ? (b / 1024 / MB).toFixed(2) + ' GB' : b >= MB ? (b / MB).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
const baseName = (n) => (n.replace(/\.[^.]+$/, '') || 'file').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const urls = {};
function blobUrl(key, blob) { if (urls[key]) URL.revokeObjectURL(urls[key]); urls[key] = URL.createObjectURL(blob); return urls[key]; }

// ---------- tabs ----------
document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
  document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === 'panel-' + t.dataset.tab));
  history.replaceState(null, '', '#' + t.dataset.tab);
}));
const hashTab = document.querySelector(`.tab[data-tab="${location.hash.slice(1)}"]`);
if (hashTab) hashTab.click();

// ---------- UI helpers ----------
function ui(prefix) {
  const prog = $(prefix + '-prog'), bar = prog.querySelector('.bar'), fill = bar.querySelector('i'), text = prog.querySelector('.ptext');
  const msg = $(prefix + '-msg'), result = $(prefix + '-result');
  return {
    progress(pct, label) {
      prog.hidden = false;
      if (pct == null) { bar.classList.add('indet'); } else { bar.classList.remove('indet'); fill.style.width = Math.max(0, Math.min(100, pct)).toFixed(1) + '%'; }
      if (label != null) text.textContent = label;
    },
    hideProgress() { prog.hidden = true; bar.classList.remove('indet'); fill.style.width = '0'; },
    error(t) { msg.className = 'msg error'; msg.innerHTML = '⚠️ ' + t; msg.hidden = false; },
    info(t) { msg.className = 'msg info'; msg.innerHTML = t; msg.hidden = false; },
    clearMsg() { msg.hidden = true; },
    result(html) { result.innerHTML = html; result.hidden = !html; },
  };
}
function setupDrop(dropId, inputId, onFiles) {
  const drop = $(dropId), input = $(inputId);
  input.addEventListener('change', () => { if (input.files.length) onFiles([...input.files]); input.value = ''; });
  ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (ev) => { const f = [...(ev.dataTransfer?.files || [])]; if (f.length) onFiles(f); });
}
function sizeWarning(size) {
  if (size > 1.8 * 1024 * MB) return { block: true, text: 'This file is larger than 1.8 GB, which is too big to process inside a browser. Please trim it or use a smaller file.' };
  if (size > 700 * MB) return { text: 'Large file: this may be slow or run out of memory on phones. Desktop Chrome or Edge works best.' };
  if (size > 300 * MB) return { text: 'Big file: processing can take a while. Keep this tab open.' };
  return null;
}
const isVideoLike = (f) => /^(video|audio)\//.test(f.type) || /\.(mp4|m4v|mov|mkv|webm|avi|flv|wmv|3gp|mpeg|mpg|ts|m4a|ogg|wav|mp3|aac)$/i.test(f.name);

// ---------- ffmpeg (shared) ----------
let ff = null, ffLoading = null, ffBusy = false, ffLogs = [];
async function getFFmpeg(onStatus) {
  if (ff) return ff;
  if (!ffLoading) {
    ffLoading = (async () => {
      const { FFmpeg } = await import('./vendor/ffmpeg/index.js');
      const inst = new FFmpeg();
      inst.on('log', ({ message }) => { ffLogs.push(message); if (ffLogs.length > 400) ffLogs.splice(0, 200); ffLogListener && ffLogListener(message); });
      await inst.load({ coreURL: abs('vendor/ffmpeg-core/ffmpeg-core.js'), wasmURL: abs('vendor/ffmpeg-core/ffmpeg-core.wasm') });
      ff = inst; return inst;
    })().catch((e) => { ffLoading = null; throw e; });
  }
  onStatus && onStatus();
  return ffLoading;
}
let ffLogListener = null;
function killFFmpeg() { try { ff && ff.terminate(); } catch (e) { /* ignore */ } ff = null; ffLoading = null; }
const parseTime = (s) => { const m = /(\d+):(\d+):(\d+(?:\.\d+)?)/.exec(s); return m ? (+m[1]) * 3600 + (+m[2]) * 60 + parseFloat(m[3]) : null; };
function explainFFError(logs, fallback) {
  const t = logs.join('\n');
  if (/does not contain any stream|Output file .* does not contain|matches no streams|Stream map .* matches no/i.test(t)) return 'This video has no audio track, so there is nothing to convert to MP3.';
  if (/Invalid data found|could not find codec|Unknown format|moov atom not found|EBML header parsing failed/i.test(t)) return 'This file could not be read. It may be damaged or in an unsupported format. Try an MP4, MOV, MKV or WEBM file.';
  if (/memory|Aborted|OOM|RangeError/i.test(t + fallback)) return 'Your browser ran out of memory. Try a shorter/smaller video, close other tabs, or use desktop Chrome/Edge.';
  return fallback || 'Something went wrong while processing this file. Please try another file.';
}
async function runFF({ file, u, goBtn, cancelBtn, job }) {
  if (ffBusy) { u.error('Another video is being processed. Please wait for it to finish.'); return; }
  ffBusy = true; let cancelled = false; goBtn.disabled = true; cancelBtn.hidden = false;
  cancelBtn.onclick = () => { cancelled = true; killFFmpeg(); };
  u.clearMsg(); u.result('');
  const ext = ((/\.[a-z0-9]{1,5}$/i.exec(file.name) || ['.mp4'])[0].toLowerCase());
  const t0 = performance.now(); let inst = null, mounted = false, inPath = '';
  const check = () => { if (cancelled) throw new Error('cancelled'); };
  try {
    u.progress(null, ff ? 'Preparing…' : 'Loading the converter (about 30 MB, only the first time)…');
    inst = await getFFmpeg(); check();
    u.progress(null, 'Reading your file…');
    // Mount the file read-only (no copy into memory) so big videos work; fall back to copying.
    try {
      try { await inst.createDir('/in'); } catch (e) { /* exists */ }
      await inst.mount('WORKERFS', { files: [new File([file], 'input' + ext)] }, '/in');
      mounted = true; inPath = '/in/input' + ext;
    } catch (e) {
      inPath = 'input' + ext; await inst.writeFile(inPath, new Uint8Array(await file.arrayBuffer()));
    }
    check();
    const ctx = {
      inPath,
      // Run ffmpeg once; returns the output bytes (or null when outName is null).
      async exec(args, label, outName, { allowFail = false, pctFrom = 0, pctTo = 100 } = {}) {
        let duration = null; const ts = performance.now();
        ffLogs = [];
        ffLogListener = (m) => {
          if (duration == null && /Duration:/.test(m)) { const d = parseTime(m.split('Duration:')[1]); if (d) duration = d; }
          const tm = /time=\s*(-?[\d:.]+)/.exec(m);
          if (tm && duration && label) {
            const cur = parseTime(tm[1]); if (cur == null) return;
            const pct = Math.min(99, (cur / duration) * 100);
            const el = (performance.now() - ts) / 1000;
            const eta = pct > 3 ? Math.round(el * (100 - pct) / pct) : null;
            u.progress(pctFrom + (pctTo - pctFrom) * pct / 100, `${label} ${pct.toFixed(0)}%` + (eta != null ? ` · about ${eta >= 60 ? Math.floor(eta / 60) + ' min ' : ''}${eta % 60} sec left` : ''));
          }
        };
        if (label) u.progress(pctFrom, label + ' 0%');
        const code = await inst.exec(outName ? [...args, outName] : args); check();
        ffLogListener = null;
        if (!outName) return null;
        if (code !== 0 && !allowFail) throw new Error(explainFFError(ffLogs, ''));
        const data = await inst.readFile(outName);
        try { await inst.deleteFile(outName); } catch (e) { /* ignore */ }
        if (!data || !data.length) throw new Error(explainFFError(ffLogs, 'The output file came out empty.'));
        return data;
      },
      // Read duration / size / audio presence from ffmpeg's banner.
      async probe() {
        ffLogs = []; await inst.exec(['-hide_banner', '-i', inPath]); check();
        const t = ffLogs.join('\n');
        const dm = /Duration:\s*([\d:.]+)/.exec(t);
        const vm = /Stream #\S+.*Video:.*?(\d{2,5})x(\d{2,5})/.exec(t);
        if (!vm) throw new Error(explainFFError(ffLogs, 'No video picture was found in this file.'));
        return { duration: dm ? parseTime(dm[1]) : null, width: +vm[1], height: +vm[2], hasAudio: /Stream #\S+.*Audio:/.test(t), short: Math.min(+vm[1], +vm[2]) };
      },
    };
    await job(ctx);
    u.progress(100, 'Done in ' + Math.round((performance.now() - t0) / 1000) + ' sec');
  } catch (e) {
    if (cancelled || e.message === 'cancelled') { u.hideProgress(); u.info('Cancelled.'); }
    else { killFFmpeg(); u.hideProgress(); u.error(esc(explainFFError(ffLogs, e && e.message ? e.message : String(e)))); console.debug('ffmpeg error', e); }
  } finally {
    ffLogListener = null; ffBusy = false; goBtn.disabled = false; cancelBtn.hidden = true;
    if (!cancelled && ff && inst === ff) {
      try { if (mounted) { await ff.unmount('/in'); } else if (inPath) { await ff.deleteFile(inPath); } } catch (e) { /* ignore */ }
    }
  }
}
function showFileInfo(el, file) {
  const w = sizeWarning(file.size);
  el.innerHTML = `🎬 <b>${esc(file.name)}</b> · ${fmtSize(file.size)}` + (w ? `<span class="warn">⚠️ ${esc(w.text)}</span>` : '');
  el.hidden = false; return w;
}

// ---------- Video to MP3 ----------
{
  const u = ui('mp3'); let file = null;
  setupDrop('mp3-drop', 'mp3-file', ([f]) => {
    u.clearMsg(); u.result(''); u.hideProgress();
    if (!isVideoLike(f)) { file = null; $('mp3-go').disabled = true; $('mp3-info').hidden = true; u.error('Please choose a video file (MP4, MOV, MKV, WEBM, AVI…).'); return; }
    file = f; const w = showFileInfo($('mp3-info'), f); $('mp3-go').disabled = !!(w && w.block);
    getFFmpeg().catch(() => {}); // warm up
  });
  $('mp3-go').addEventListener('click', () => {
    if (!file) return;
    const br = $('mp3-bitrate').value, name = baseName(file.name) + '.mp3';
    runFF({
      file, u, goBtn: $('mp3-go'), cancelBtn: $('mp3-cancel'),
      job: async (c) => {
        const data = await c.exec(['-i', c.inPath, '-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-b:a', br, '-ar', '44100'], 'Converting…', 'output.mp3');
        const blob = new Blob([data], { type: 'audio/mpeg' }); const url = blobUrl('mp3', blob);
        u.result(`<h3>✅ Your MP3 is ready</h3><audio controls src="${url}"></audio>
          <div class="stats"><div class="stat"><b>${fmtSize(blob.size)}</b>MP3 size</div><div class="stat"><b>${br.replace('k', ' kbps')}</b>Quality</div></div>
          <a class="btn ok" id="mp3-dl" href="${url}" download="${esc(name)}">⬇️ Download MP3</a>`);
      },
    });
  });
}

// ---------- Video Compressor ----------
const RES_STEPS = [1080, 720, 480, 360, 240, 144];
// Pick audio + video bitrate and resolution for a target size (MB) and duration (s).
function planTarget(mb, dur, hasAudio, srcH, maxRes) {
  const total = (mb * 8192 / dur) * 0.95; // kbps incl. ~5% container/rate margin
  let a = 0, mono = false;
  if (hasAudio) {
    if (total >= 1200) a = 128; else if (total >= 600) a = 96; else if (total >= 300) a = 64; else if (total >= 150) { a = 48; mono = true; } else { a = 32; mono = true; }
  }
  let v = Math.floor(total - a);
  const MIN_V = 40; let impossible = false, tight = false;
  if (v < MIN_V) { impossible = true; v = MIN_V; } else if (v < 90) tight = true;
  let res = v >= 2000 ? 1080 : v >= 1000 ? 720 : v >= 500 ? 480 : v >= 250 ? 360 : v >= 110 ? 240 : 144;
  if (maxRes && maxRes !== 'auto' && +maxRes > 0) res = Math.min(res, +maxRes);
  if (srcH) res = Math.min(res, srcH);
  const fps = v < 250 ? 24 : 0;
  const minMB = ((MIN_V + (hasAudio ? 32 : 0)) * dur / 8192 / 0.95);
  return { total, a, mono, v, res, fps, impossible, tight, minMB };
}
const fmtDur = (s) => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? h + 'h ' : '') + (h || m ? m + 'm ' : '') + x + 's'; };
{
  const u = ui('cmp'); let file = null, mode = 'quality', fileDur = null, fileH = null;
  const resSel = $('cmp-res'), autoOpt = $('cmp-res-auto');
  const targetMB = () => { const v = parseFloat($('cmp-target').value); return isFinite(v) && v > 0 ? v : null; };
  function setMode(m) {
    mode = m;
    document.querySelectorAll('.mode').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
    $('cmp-quality-opts').hidden = m !== 'quality'; $('cmp-target-opts').hidden = m !== 'target';
    autoOpt.hidden = m !== 'target';
    if (m === 'target') resSel.value = 'auto'; else if (resSel.value === 'auto') resSel.value = '720';
    $('cmp-go').textContent = m === 'target' ? 'Compress to target size' : 'Compress video';
    updatePlan();
  }
  document.querySelectorAll('.mode').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
  document.querySelectorAll('#cmp-chips .chip').forEach((c) => c.addEventListener('click', () => {
    $('cmp-target').value = c.dataset.mb; document.querySelectorAll('#cmp-chips .chip').forEach((x) => x.classList.toggle('active', x === c)); updatePlan();
  }));
  $('cmp-target').addEventListener('input', () => { document.querySelectorAll('#cmp-chips .chip').forEach((x) => x.classList.toggle('active', x.dataset.mb === $('cmp-target').value)); updatePlan(); });
  resSel.addEventListener('change', updatePlan);
  function updatePlan() {
    const el = $('cmp-plan'); const mb = targetMB();
    if (mode !== 'target' || !file) { el.hidden = true; return; }
    el.hidden = false;
    if (!mb) { el.innerHTML = '<span class="warn">⚠️ Enter a target size in MB.</span>'; return; }
    if (mb * MB >= file.size) { el.innerHTML = `<span class="warn">⚠️ Your video is already ${fmtSize(file.size)} — choose a target smaller than that.</span>`; return; }
    if (!fileDur) { el.innerHTML = `🎯 Target: <b>${mb} MB</b> (from ${fmtSize(file.size)}). Reading video length…`; return; }
    const p = planTarget(mb, fileDur, true, fileH, resSel.value);
    el.innerHTML = `🎯 Target <b>${mb} MB</b> for a ${fmtDur(fileDur)} video → about <b>${p.res}p</b>, video ${p.v} kbps, audio ${p.a} kbps${p.mono ? ' mono' : ''}.` +
      (p.impossible ? `<span class="warn">⚠️ ${mb} MB is too small for a ${fmtDur(fileDur)} video. The smallest realistic size is about ${p.minMB < 10 ? (Math.ceil(p.minMB * 10) / 10).toFixed(1) : Math.ceil(p.minMB)} MB — the result will be bigger than your target.</span>`
        : p.tight ? '<span class="warn">⚠️ This is a very small size for this length, so the picture will be low quality.</span>' : '');
  }
  function readDuration(f) {
    fileDur = null; fileH = null;
    const v = document.createElement('video'); v.preload = 'metadata'; v.muted = true;
    const url = URL.createObjectURL(f);
    const done = () => { v.onloadedmetadata = v.onerror = null; v.removeAttribute('src'); v.load(); setTimeout(() => URL.revokeObjectURL(url), 1000); };
    v.onloadedmetadata = () => { if (file === f && isFinite(v.duration) && v.duration > 0) { fileDur = v.duration; fileH = Math.min(v.videoWidth, v.videoHeight) || null; } done(); updatePlan(); };
    v.onerror = () => { done(); updatePlan(); };
    v.src = url;
  }
  setupDrop('cmp-drop', 'cmp-file', ([f]) => {
    u.clearMsg(); u.result(''); u.hideProgress();
    if (!isVideoLike(f) || /^audio\//.test(f.type)) { file = null; $('cmp-go').disabled = true; $('cmp-info').hidden = true; $('cmp-plan').hidden = true; u.error('Please choose a video file (MP4, MOV, MKV, WEBM, AVI…).'); return; }
    file = f; const w = showFileInfo($('cmp-info'), f); $('cmp-go').disabled = !!(w && w.block);
    readDuration(f); updatePlan();
    getFFmpeg().catch(() => {});
  });
  const PRESETS = { high: { crf: 23, ab: 128 }, medium: { crf: 28, ab: 96 }, small: { crf: 33, ab: 64 }, lowest: { crf: 40, ab: 48, mono: true, fps: 24 } };
  const scaleFilter = (res, fps) => {
    // res = the short side (so 720p works for both landscape and vertical/phone videos)
    const s = res ? `scale='if(gt(iw,ih),-2,trunc(min(${res},iw)/2)*2)':'if(gt(iw,ih),trunc(min(${res},ih)/2)*2,-2)'` : 'scale=trunc(iw/2)*2:trunc(ih/2)*2';
    return fps ? `${s},fps=${fps}` : s;
  };
  function showResult(data, orig, origName, extraStats, note) {
    const blob = new Blob([data], { type: 'video/mp4' }); const url = blobUrl('cmp', blob);
    const name = baseName(origName) + '-compressed.mp4';
    const saved = 100 - (blob.size / orig) * 100, smaller = blob.size < orig;
    u.result(`<h3>${smaller ? '✅ Video compressed' : 'ℹ️ Finished'}</h3><video controls playsinline src="${url}"></video>
      <div class="stats"><div class="stat"><b>${fmtSize(orig)}</b>Before</div><div class="stat"><b>${fmtSize(blob.size)}</b>After</div>
      <div class="stat ${smaller ? 'good' : ''}"><b>${smaller ? Math.floor(saved) + '% smaller' : 'Not smaller'}</b>Saved ${smaller ? fmtSize(orig - blob.size) : '0'}</div>${extraStats || ''}</div>
      ${note || ''}
      ${smaller ? '' : `<p class="hint">This video was already well compressed. Try “Lowest” or a lower resolution. Your original “${esc(origName)}” is unchanged.</p>`}
      <a class="btn ok" id="cmp-dl" href="${url}" download="${esc(name)}">⬇️ Download compressed video</a>`);
  }
  $('cmp-go').addEventListener('click', () => {
    if (!file) return;
    const orig = file.size, origName = file.name, resChoice = resSel.value;
    if (mode === 'quality') {
      const q = document.querySelector('input[name=cmp-q]:checked').value, p = PRESETS[q];
      const res = resChoice === 'auto' ? 720 : +resChoice;
      runFF({
        file, u, goBtn: $('cmp-go'), cancelBtn: $('cmp-cancel'),
        job: async (c) => {
          const data = await c.exec(['-i', c.inPath, '-map', '0:v:0', '-map', '0:a:0?', '-vf', scaleFilter(res, p.fps), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', String(p.crf),
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', p.ab + 'k', ...(p.mono ? ['-ac', '1'] : []), '-movflags', '+faststart'], 'Compressing…', 'output.mp4');
          showResult(data, orig, origName);
        },
      });
      return;
    }
    const mb = targetMB();
    if (!mb) { u.error('Please enter a target size in MB (for example 12).'); return; }
    if (mb * MB >= orig) { u.error(`Your video is already ${fmtSize(orig)}. Choose a target smaller than that.`); return; }
    const targetBytes = mb * MB;
    runFF({
      file, u, goBtn: $('cmp-go'), cancelBtn: $('cmp-cancel'),
      job: async (c) => {
        u.progress(null, 'Checking video length…');
        const info = await c.probe();
        const dur = info.duration || fileDur;
        if (!dur) throw new Error('Could not read the length of this video, so a target size cannot be calculated. Please use “By quality” instead.');
        const p = planTarget(mb, dur, info.hasAudio, info.short, resChoice);
        const encode = (v, label, from, to) => c.exec(['-i', c.inPath, '-map', '0:v:0', '-map', '0:a:0?', '-vf', scaleFilter(p.res, p.fps),
          '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', v + 'k', '-maxrate', Math.round(v * 1.3) + 'k', '-bufsize', Math.round(v * 2) + 'k',
          '-pix_fmt', 'yuv420p', ...(info.hasAudio ? ['-c:a', 'aac', '-b:a', p.a + 'k', ...(p.mono ? ['-ac', '1'] : [])] : ['-an']), '-movflags', '+faststart'],
          label, 'output.mp4', { pctFrom: from, pctTo: to });
        let data = await encode(p.v, `Compressing to ${mb} MB (${p.res}p)…`, 0, 100);
        let retried = false;
        if (data.length > targetBytes && !p.impossible) {
          // Overshot: lower the video bitrate in proportion and try once more.
          const audioBytes = (p.a * 1000 / 8) * dur;
          const videoBytes = Math.max(1, data.length - audioBytes);
          const v2 = Math.max(30, Math.floor(p.v * ((targetBytes * 0.95 - audioBytes) / videoBytes)));
          if (v2 < p.v) { retried = true; data = await encode(v2, `A little over ${mb} MB — compressing again…`, 0, 100); }
        }
        const hit = data.length <= targetBytes;
        const note = hit ? '' : `<p class="hint">⚠️ The result is ${fmtSize(data.length)}, a bit above your ${mb} MB target${p.impossible ? ` — ${mb} MB is too small for a ${fmtDur(dur)} video` : ''}. Try a lower resolution or a larger target.</p>`;
        showResult(data, orig, origName,
          `<div class="stat ${hit ? 'good' : ''}"><b>${hit ? '✓ ' : ''}${mb} MB</b>Target</div><div class="stat"><b>${p.res}p</b>${fmtDur(dur)}${retried ? ' · 2 tries' : ''}</div>`, note);
      },
    });
  });
}

// ---------- Images to PDF ----------
{
  const u = ui('pdf'); let items = []; let dragIdx = null; let idc = 0;
  const thumbs = $('pdf-thumbs');
  function render() {
    thumbs.innerHTML = '';
    items.forEach((it, i) => {
      const d = document.createElement('div'); d.className = 'thumb'; d.draggable = true; d.dataset.i = i;
      d.innerHTML = `<span class="num">${i + 1}</span><img src="${it.url}" alt="" style="transform:rotate(${it.rot}deg)"><div class="name" title="${esc(it.file.name)}">${esc(it.file.name)}</div>
        <div class="tools"><button data-a="left" title="Move left" ${i === 0 ? 'disabled' : ''}>◀</button><button data-a="rot" title="Rotate">⟳</button><button data-a="del" title="Remove">✕</button><button data-a="right" title="Move right" ${i === items.length - 1 ? 'disabled' : ''}>▶</button></div>`;
      d.addEventListener('dragstart', () => { dragIdx = i; d.classList.add('dragging'); });
      d.addEventListener('dragend', () => { d.classList.remove('dragging'); });
      d.addEventListener('dragover', (e) => { e.preventDefault(); d.classList.add('over'); });
      d.addEventListener('dragleave', () => d.classList.remove('over'));
      d.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); d.classList.remove('over'); if (dragIdx != null && dragIdx !== i) { const [m] = items.splice(dragIdx, 1); items.splice(i, 0, m); dragIdx = null; render(); } });
      d.querySelector('.tools').addEventListener('click', (e) => {
        const a = e.target.closest('button')?.dataset.a; if (!a) return;
        if (a === 'left' && i > 0) [items[i - 1], items[i]] = [items[i], items[i - 1]];
        if (a === 'right' && i < items.length - 1) [items[i + 1], items[i]] = [items[i], items[i + 1]];
        if (a === 'rot') it.rot = (it.rot + 90) % 360;
        if (a === 'del') { URL.revokeObjectURL(it.url); items.splice(i, 1); }
        render();
      });
      thumbs.appendChild(d);
    });
    $('pdf-go').disabled = !items.length; $('pdf-clear').hidden = !items.length;
    $('pdf-go').textContent = items.length ? `Create PDF (${items.length} page${items.length > 1 ? 's' : ''})` : 'Create PDF';
  }
  setupDrop('pdf-drop', 'pdf-file', (files) => {
    u.clearMsg(); u.result('');
    const bad = files.filter((f) => !/^image\//.test(f.type) && !/\.(jpe?g|png|webp|gif|bmp|avif)$/i.test(f.name));
    const good = files.filter((f) => !bad.includes(f));
    good.forEach((f) => items.push({ id: ++idc, file: f, url: URL.createObjectURL(f), rot: 0 }));
    if (bad.length) u.error(`Skipped ${bad.length} file(s) that are not images: ${esc(bad.map((b) => b.name).join(', '))}`);
    render();
  });
  $('pdf-clear').addEventListener('click', () => { items.forEach((i) => URL.revokeObjectURL(i.url)); items = []; u.result(''); u.clearMsg(); u.hideProgress(); render(); });
  async function decode(file) {
    try { return await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch (e) {
      return await new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error('decode')); im.src = URL.createObjectURL(file); });
    }
  }
  const SIZES = { a4: [595.28, 841.89], letter: [612, 792], legal: [612, 1008] };
  $('pdf-go').addEventListener('click', async () => {
    if (!items.length) return;
    if (!window.jspdf) { u.error('The PDF engine failed to load. Please refresh the page.'); return; }
    const size = $('pdf-size').value, orient = $('pdf-orient').value, margin = +$('pdf-margin').value;
    const name = (($('pdf-name').value || 'images').trim().replace(/[\\/:*?"<>|]+/g, '_') || 'images').replace(/\.pdf$/i, '') + '.pdf';
    $('pdf-go').disabled = true; u.clearMsg(); u.result('');
    let doc = null; const failed = []; let pages = 0;
    try {
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        u.progress((i / items.length) * 100, `Adding page ${i + 1} of ${items.length}…`);
        await new Promise((r) => setTimeout(r, 0));
        let img;
        try { img = await decode(it.file); } catch (e) { failed.push(it.file.name); continue; }
        let w = img.width, h = img.height; const rot = it.rot % 180 !== 0;
        const scale = Math.min(1, 2400 / Math.max(w, h));
        const cw = Math.round((rot ? h : w) * scale), ch = Math.round((rot ? w : h) * scale);
        const c = document.createElement('canvas'); c.width = cw; c.height = ch;
        const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, cw, ch);
        g.translate(cw / 2, ch / 2); g.rotate((it.rot * Math.PI) / 180); g.drawImage(img, -w * scale / 2, -h * scale / 2, w * scale, h * scale);
        if (img.close) img.close();
        const jpg = await new Promise((r) => c.toBlob(r, 'image/jpeg', 0.9));
        const bytes = new Uint8Array(await jpg.arrayBuffer());
        let pw, ph;
        if (size === 'fit') { const k = 0.75 / scale; pw = cw * k + margin * 2; ph = ch * k + margin * 2; const cap = 14000 / Math.max(pw, ph); if (cap < 1) { pw *= cap; ph *= cap; } }
        else {
          [pw, ph] = SIZES[size];
          const land = orient === 'landscape' || (orient === 'auto' && cw > ch);
          if (land) [pw, ph] = [ph, pw];
        }
        const o = pw > ph ? 'l' : 'p';
        if (!doc) doc = new window.jspdf.jsPDF({ unit: 'pt', format: [pw, ph], orientation: o, compress: true });
        else doc.addPage([pw, ph], o);
        const aw = pw - margin * 2, ah = ph - margin * 2, f = Math.min(aw / cw, ah / ch);
        const dw = cw * f, dh = ch * f;
        doc.addImage(bytes, 'JPEG', (pw - dw) / 2, (ph - dh) / 2, dw, dh, undefined, 'FAST');
        pages++;
      }
      if (!doc) throw new Error('None of the images could be read. Please use JPG, PNG or WEBP images.');
      u.progress(100, 'Saving PDF…');
      const blob = doc.output('blob'); const url = blobUrl('pdf', blob);
      u.progress(100, 'Done');
      u.result(`<h3>✅ PDF ready – ${pages} page${pages > 1 ? 's' : ''}</h3><div class="stats"><div class="stat"><b>${fmtSize(blob.size)}</b>PDF size</div><div class="stat"><b>${pages}</b>Pages</div></div>
        <a class="btn ok" id="pdf-dl" href="${url}" download="${esc(name)}">⬇️ Download PDF</a> <a class="btn ghost" href="${url}" target="_blank" rel="noopener">👁️ Preview</a>`);
      if (failed.length) u.error(`These images could not be read and were skipped (HEIC photos must be converted to JPG first): ${esc(failed.join(', '))}`);
    } catch (e) { u.hideProgress(); u.error(esc(e.message || 'Could not create the PDF.')); console.debug(e); }
    finally { $('pdf-go').disabled = !items.length; }
  });
  render();
}

// ---------- Background Remover ----------
{
  const u = ui('bg'); let file = null, bgLib = null, lastCut = null, busy = false;
  const cfg = (progress) => ({ publicPath: abs('vendor/bgdata/'), model: 'isnet_quint8', device: 'cpu', output: { format: 'image/png' }, progress });
  $('bg-mode').addEventListener('change', () => { $('bg-color-wrap').hidden = $('bg-mode').value !== 'color'; if (lastCut) finish(); });
  $('bg-color').addEventListener('input', () => { if (lastCut) finish(); });
  setupDrop('bg-drop', 'bg-file', ([f]) => {
    u.clearMsg(); u.result(''); u.hideProgress(); lastCut = null; $('bg-compare').hidden = true;
    if (!/^image\/(png|jpe?g|webp|bmp|gif)$/i.test(f.type) && !/\.(jpe?g|png|webp|bmp|gif)$/i.test(f.name)) { file = null; $('bg-go').disabled = true; u.error('Please choose a JPG, PNG or WEBP photo. (HEIC iPhone photos: share them as JPG first.)'); return; }
    if (f.size > 40 * MB) { file = null; $('bg-go').disabled = true; u.error('This image is larger than 40 MB. Please use a smaller photo.'); return; }
    file = f; $('bg-go').disabled = false;
    $('bg-before').src = blobUrl('bg-before', f); $('bg-after').removeAttribute('src'); $('bg-compare').hidden = false;
  });
  async function finish() {
    const mode = $('bg-mode').value; let out = lastCut;
    if (mode !== 'transparent') {
      const bmp = await createImageBitmap(lastCut); const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height;
      const g = c.getContext('2d'); g.fillStyle = mode === 'white' ? '#ffffff' : $('bg-color').value; g.fillRect(0, 0, c.width, c.height); g.drawImage(bmp, 0, 0); bmp.close();
      out = await new Promise((r) => c.toBlob(r, 'image/png'));
    }
    const url = blobUrl('bg-after', out); $('bg-after').src = url;
    const name = baseName(file.name) + (mode === 'transparent' ? '-no-bg.png' : '-new-bg.png');
    u.result(`<h3>✅ Background removed</h3><div class="stats"><div class="stat"><b>PNG</b>${mode === 'transparent' ? 'Transparent background' : 'New background'}</div><div class="stat"><b>${fmtSize(out.size)}</b>File size</div></div>
      <a class="btn ok" id="bg-dl" href="${url}" download="${esc(name)}">⬇️ Download PNG</a>`);
  }
  $('bg-go').addEventListener('click', async () => {
    if (!file || busy) return; busy = true; $('bg-go').disabled = true; u.clearMsg(); u.result('');
    const t0 = performance.now();
    try {
      u.progress(null, 'Loading AI engine…');
      if (!bgLib) bgLib = await import('./vendor/bgremove.js');
      const blob = await bgLib.removeBackground(file, cfg((key, cur, total) => {
        if (key.startsWith('fetch')) u.progress(total ? (cur / total) * 100 : null, `Downloading AI model (first time only)… ${total ? Math.round((cur / total) * 100) + '%' : ''}`);
        else if (key.startsWith('compute')) u.progress(null, 'Removing background… (this can take 5–30 seconds)');
      }));
      lastCut = blob; await finish();
      u.progress(100, 'Done in ' + Math.round((performance.now() - t0) / 1000) + ' sec');
    } catch (e) {
      console.debug('bg error', e); u.hideProgress();
      const m = String(e && e.message || e);
      u.error(/fetch|network|Failed to load/i.test(m) ? 'Could not download the AI model. Check your internet connection and try again.' : /memory|RangeError|Aborted/i.test(m) ? 'Your device ran out of memory. Try a smaller photo or close other tabs.' : 'Could not process this image. Please try a different JPG or PNG photo.');
    } finally { busy = false; $('bg-go').disabled = !file; }
  });
}
