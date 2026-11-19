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
async function runFF({ file, args, outName, u, label, onDone, cancelBtn, goBtn }) {
  if (ffBusy) { u.error('Another video is being processed. Please wait for it to finish.'); return; }
  ffBusy = true; let cancelled = false; goBtn.disabled = true; cancelBtn.hidden = false;
  const onCancel = () => { cancelled = true; killFFmpeg(); };
  cancelBtn.onclick = onCancel;
  u.clearMsg(); u.result('');
  const inName = 'input' + ((/\.[a-z0-9]{1,5}$/i.exec(file.name) || ['.mp4'])[0].toLowerCase());
  let duration = null; const t0 = performance.now();
  try {
    u.progress(null, ff ? 'Preparing…' : 'Loading the converter (about 30 MB, only the first time)…');
    const inst = await getFFmpeg();
    if (cancelled) throw new Error('cancelled');
    u.progress(null, 'Reading your file…');
    await inst.writeFile(inName, new Uint8Array(await file.arrayBuffer()));
    ffLogs = [];
    ffLogListener = (m) => {
      if (duration == null && /Duration:/.test(m)) { const d = parseTime(m.split('Duration:')[1]); if (d) duration = d; }
      const tm = /time=\s*(-?[\d:.]+)/.exec(m);
      if (tm && duration) {
        const cur = parseTime(tm[1]); if (cur == null) return;
        const pct = Math.min(99, (cur / duration) * 100);
        const el = (performance.now() - t0) / 1000;
        const eta = pct > 3 ? Math.round(el * (100 - pct) / pct) : null;
        u.progress(pct, `${label} ${pct.toFixed(0)}%` + (eta != null ? ` · about ${eta >= 60 ? Math.floor(eta / 60) + ' min ' : ''}${eta % 60} sec left` : ''));
      }
    };
    u.progress(0, label + ' 0%');
    const code = await inst.exec([...args(inName), outName]);
    if (cancelled) throw new Error('cancelled');
    if (code !== 0) throw new Error(explainFFError(ffLogs, ''));
    const data = await inst.readFile(outName);
    if (!data || !data.length) throw new Error(explainFFError(ffLogs, 'The output file came out empty.'));
    try { await inst.deleteFile(inName); await inst.deleteFile(outName); } catch (e) { /* ignore */ }
    u.progress(100, 'Done in ' + Math.round((performance.now() - t0) / 1000) + ' sec');
    onDone(data);
  } catch (e) {
    if (cancelled || e.message === 'cancelled') { u.hideProgress(); u.info('Cancelled.'); }
    else { killFFmpeg(); u.hideProgress(); u.error(esc(explainFFError(ffLogs, e && e.message ? e.message : String(e)))); console.debug('ffmpeg error', e); }
  } finally {
    ffLogListener = null; ffBusy = false; goBtn.disabled = false; cancelBtn.hidden = true;
    if (!cancelled && ff) { try { await ff.deleteFile(inName); } catch (e) { /* already gone */ } }
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
      file, u, label: 'Converting…', outName: 'output.mp3', goBtn: $('mp3-go'), cancelBtn: $('mp3-cancel'),
      args: (inp) => ['-i', inp, '-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-b:a', br, '-ar', '44100'],
      onDone: (data) => {
        const blob = new Blob([data], { type: 'audio/mpeg' }); const url = blobUrl('mp3', blob);
        u.result(`<h3>✅ Your MP3 is ready</h3><audio controls src="${url}"></audio>
          <div class="stats"><div class="stat"><b>${fmtSize(blob.size)}</b>MP3 size</div><div class="stat"><b>${br.replace('k', ' kbps')}</b>Quality</div></div>
          <a class="btn ok" id="mp3-dl" href="${url}" download="${esc(name)}">⬇️ Download MP3</a>`);
      },
    });
  });
}

// ---------- Video Compressor ----------
{
  const u = ui('cmp'); let file = null;
  setupDrop('cmp-drop', 'cmp-file', ([f]) => {
    u.clearMsg(); u.result(''); u.hideProgress();
    if (!isVideoLike(f) || /^audio\//.test(f.type)) { file = null; $('cmp-go').disabled = true; $('cmp-info').hidden = true; u.error('Please choose a video file (MP4, MOV, MKV, WEBM, AVI…).'); return; }
    file = f; const w = showFileInfo($('cmp-info'), f); $('cmp-go').disabled = !!(w && w.block);
    getFFmpeg().catch(() => {});
  });
  const PRESETS = { high: { crf: 23, ab: '128k' }, medium: { crf: 28, ab: '96k' }, small: { crf: 33, ab: '64k' } };
  $('cmp-go').addEventListener('click', () => {
    if (!file) return;
    const q = document.querySelector('input[name=cmp-q]:checked').value, p = PRESETS[q], res = +$('cmp-res').value;
    const vf = res ? `scale=-2:'trunc(min(${res},ih)/2)*2'` : 'scale=trunc(iw/2)*2:trunc(ih/2)*2';
    const name = baseName(file.name) + '-compressed.mp4', orig = file.size, origName = file.name;
    runFF({
      file, u, label: 'Compressing…', outName: 'output.mp4', goBtn: $('cmp-go'), cancelBtn: $('cmp-cancel'),
      args: (inp) => ['-i', inp, '-map', '0:v:0', '-map', '0:a:0?', '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', String(p.crf),
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', p.ab, '-movflags', '+faststart'],
      onDone: (data) => {
        const blob = new Blob([data], { type: 'video/mp4' }); const url = blobUrl('cmp', blob);
        const saved = 100 - (blob.size / orig) * 100;
        const smaller = blob.size < orig;
        u.result(`<h3>${smaller ? '✅ Video compressed' : 'ℹ️ Finished'}</h3><video controls playsinline src="${url}"></video>
          <div class="stats"><div class="stat"><b>${fmtSize(orig)}</b>Before</div><div class="stat"><b>${fmtSize(blob.size)}</b>After</div>
          <div class="stat ${smaller ? 'good' : ''}"><b>${smaller ? saved.toFixed(0) + '% smaller' : 'Not smaller'}</b>Saved ${smaller ? fmtSize(orig - blob.size) : '0'}</div></div>
          ${smaller ? '' : `<p class="hint">This video was already well compressed. Try “Smallest file” or a lower resolution. Your original “${esc(origName)}” is unchanged.</p>`}
          <a class="btn ok" id="cmp-dl" href="${url}" download="${esc(name)}">⬇️ Download compressed video</a>`);
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
