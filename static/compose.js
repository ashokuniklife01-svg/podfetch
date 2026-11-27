(function () {
  "use strict";
  var U = window.UP, el = U.el, ic = U.ic, $ = function (s) { return document.querySelector(s); };
  var boot = JSON.parse($("#boot").textContent);
  var L = boot.limits;
  var channels = boot.channels;
  var byId = {}; channels.forEach(function (c) { byId[c.id] = c; });
  var ORDER = ["youtube", "instagram", "facebook"];
  var KIND = { short: "Short", long: "Long video", reel: "Reel", video: "Video", photo: "Photo", carousel: "Carousel", photos: "Album", text: "Post" };
  var S = { type: "video", video: null, photos: [], override: null, selected: [], dests: {}, busy: false };

  // ---------- routing (mirrors routing.py) ----------
  function shape() {
    var v = S.video; if (!v || !v.w || !v.h || !v.dur) return "long";
    return (v.h >= v.w && v.dur <= 180) ? "short" : "long";
  }
  function effShape() { return S.override || shape(); }
  function kindFor(p) {
    if (S.type === "video") {
      var s = effShape();
      if (p === "youtube") return s;
      if (p === "instagram") return "reel";
      return (s === "short" && S.video && S.video.dur > 0 && S.video.dur <= 90) ? "reel" : "video";
    }
    if (S.type === "photo") {
      if (p === "youtube") return null;
      var n = S.photos.length;
      if (p === "instagram") return n > 1 ? "carousel" : "photo";
      return n > 1 ? "photos" : "photo";
    }
    return p === "facebook" ? "text" : null;
  }

  // ---------- type tabs ----------
  document.querySelectorAll("#types button").forEach(function (b) {
    b.addEventListener("click", function () { setType(b.getAttribute("data-type")); });
  });
  function setType(t) {
    if (t === S.type) return;
    S.type = t;
    document.querySelectorAll("#types button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-type") === t); });
    S.selected = S.selected.filter(function (id) { return kindFor(byId[id].platform); });
    var f = $("#file");
    f.accept = t === "photo" ? "image/jpeg,image/png,image/webp" : "video/*";
    f.multiple = t === "photo";
    $("#mediaCard").classList.toggle("hidden", t === "text");
    renderMedia(); renderAccounts(); renderDests(); renderFoot();
  }

  // ---------- upload ----------
  var drop = $("#drop"), fileIn = $("#file");
  fileIn.addEventListener("change", function () { addFiles(fileIn.files); fileIn.value = ""; });
  ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { addFiles(e.dataTransfer.files); });

  function addFiles(list) {
    var files = Array.prototype.slice.call(list || []);
    if (!files.length) return;
    if (S.type === "video") {
      var f = files[0];
      if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(f.name)) { U.toast("Pick a video file", "error"); return; }
      if (f.size > boot.maxUploadMb * 1048576) { U.toast("Video is over " + boot.maxUploadMb + " MB", "error"); return; }
      removeVideo();
      var v = { file: f, progress: 0, ready: false, url: URL.createObjectURL(f), w: 0, h: 0, dur: 0 };
      S.video = v; S.override = null;
      readMeta(v).then(function () { renderMedia(); renderDests(); renderFoot(); upload(v); });
      renderMedia(); renderFoot();
    } else {
      var room = L.carousel_max - S.photos.length;
      files.filter(function (f) { return /^image\/(jpeg|png|webp)$/.test(f.type) || /\.(jpe?g|png|webp)$/i.test(f.name); })
        .slice(0, room).forEach(function (f) {
          var p = { file: f, progress: 0, ready: false, url: URL.createObjectURL(f) };
          S.photos.push(p); upload(p);
        });
      if (files.length > room) U.toast("Up to " + L.carousel_max + " photos");
      renderMedia(); renderDests(); renderFoot();
    }
  }
  function readMeta(v) {
    return new Promise(function (res) {
      var vid = document.createElement("video"); vid.preload = "metadata"; vid.muted = true;
      var done = false;
      function fin() { if (done) return; done = true; v.w = vid.videoWidth; v.h = vid.videoHeight; v.dur = isFinite(vid.duration) ? vid.duration : 0; res(); }
      vid.onloadedmetadata = fin; vid.onerror = fin; setTimeout(fin, 8000);
      vid.src = v.url;
    });
  }
  function upload(item) {
    var f = item.file;
    item.abort = false;
    U.api("POST", "/api/uploads", { name: f.name, size: f.size, type: f.type, width: item.w || null, height: item.h || null, duration: item.dur || null })
      .then(function (r) {
        item.id = r.id; var chunk = r.chunk, offset = 0, tries = 0;
        function next() {
          if (item.abort) return;
          if (offset >= f.size) {
            return U.api("POST", "/api/uploads/" + item.id + "/complete").then(function () {
              item.ready = true; item.progress = 100; renderProgress(item); renderFoot();
            });
          }
          var end = Math.min(offset + chunk, f.size);
          var xhr = new XMLHttpRequest(); item.xhr = xhr;
          xhr.open("PUT", "/api/uploads/" + item.id + "/chunk?offset=" + offset);
          xhr.setRequestHeader("X-CSRF-Token", document.body.getAttribute("data-csrf"));
          xhr.setRequestHeader("Content-Type", "application/octet-stream");
          xhr.upload.onprogress = function (e) { item.progress = Math.min(99, Math.floor((offset + e.loaded) * 100 / f.size)); renderProgress(item); };
          xhr.onload = function () {
            if (xhr.status === 200) { offset = JSON.parse(xhr.responseText).received; tries = 0; next(); }
            else if (xhr.status === 409) { try { offset = JSON.parse(xhr.responseText).received; } catch (e) {} retry(); }
            else retry();
          };
          xhr.onerror = retry;
          xhr.send(f.slice(offset, end));
        }
        function retry() {
          if (item.abort) return;
          if (++tries > 6) { fail("Upload failed. Check your connection and try again."); return; }
          setTimeout(function () {
            U.api("GET", "/api/uploads/" + item.id).then(function (s) { offset = s.received; next(); }, function () { retry(); });
          }, Math.min(1000 * Math.pow(2, tries), 15000));
        }
        next();
      })
      .catch(function (e) { fail(e.message); });
    function fail(msg) { item.error = msg; renderMedia(); renderFoot(); U.toast(msg, "error"); }
  }
  function dropItem(item) {
    if (!item) return;
    item.abort = true; if (item.xhr) item.xhr.abort();
    if (item.id) U.api("DELETE", "/api/uploads/" + item.id).catch(function () {});
    if (item.url) URL.revokeObjectURL(item.url);
  }
  function removeVideo() { dropItem(S.video); S.video = null; S.override = null; }

  function renderProgress(item) {
    var bar = document.querySelector('[data-bar="' + (item.key || "v") + '"]');
    if (bar) { bar.firstChild.style.width = item.progress + "%"; bar.classList.toggle("done", item.ready); }
    var t = document.querySelector('[data-pct="' + (item.key || "v") + '"]');
    if (t) t.textContent = item.ready ? "Uploaded" : (item.progress + "%");
    if (!item.ready) renderFoot();
  }

  // ---------- media view ----------
  var keySeq = 0;
  function renderMedia() {
    var view = $("#mediaView"); view.innerHTML = "";
    var hasMedia = S.type === "video" ? !!S.video : S.photos.length > 0;
    drop.classList.toggle("hidden", hasMedia);
    view.classList.toggle("hidden", !hasMedia);
    $("#dropTitle").textContent = S.type === "photo" ? "Add photos" : "Add video";
    $("#dropSub").textContent = S.type === "photo" ? "Up to 10 · JPG or PNG" : "Drop here or tap to choose · original quality";
    if (!hasMedia) return;
    if (S.type === "video") {
      var v = S.video;
      var orient = v.w && v.h ? (v.h > v.w ? "v" : (v.h < v.w ? "h" : "")) : "";
      var meta = [U.fmtSize(v.file.size)];
      if (v.w) meta.unshift(v.w + "×" + v.h);
      if (v.dur) meta.unshift(U.fmtDur(v.dur));
      var vid = el("video", { src: v.url, muted: true, playsinline: true, preload: "metadata" });
      vid.muted = true;
      view.appendChild(el("div", { class: "media" }, [
        el("div", { class: "media-thumb " + orient }, [vid]),
        el("div", { class: "media-info" }, [
          el("div", { class: "media-name", text: v.file.name }),
          el("div", { class: "media-meta", text: meta.join(" · ") }),
          el("div", { class: "media-meta" }, [el("span", { "data-pct": "v", text: v.error ? "Failed" : (v.ready ? "Uploaded" : v.progress + "%") })]),
          el("div", { class: "bar" + (v.ready ? " done" : ""), "data-bar": "v" }, [el("i")])
        ]),
        el("button", { class: "x-btn", title: "Remove", "aria-label": "Remove video", onclick: function () { removeVideo(); renderMedia(); renderDests(); renderFoot(); } }, [ic("x")])
      ]));
      renderProgress(v);
      var s = effShape(), auto = shape();
      var seg = el("div", { class: "seg sm" }, ["short", "long"].map(function (k) {
        return el("button", { class: s === k ? "on" : "", onclick: function () { S.override = (k === auto ? null : k); renderMedia(); renderDests(); } }, [k === "short" ? "Short" : "Long"]);
      }));
      view.appendChild(el("div", { class: "route" }, [
        el("div", { class: "route-l" }, [el("b", { text: s === "short" ? "Short" : "Long video" }), S.override ? " · set by you" : " · auto"]),
        seg
      ]));
      if (v.error) view.appendChild(el("div", { class: "dest-err", text: v.error }));
    } else {
      var grid = el("div", { class: "photos" });
      S.photos.forEach(function (p) {
        if (!p.key) p.key = "p" + (++keySeq);
        grid.appendChild(el("div", { class: "ph" }, [
          el("img", { src: p.url, alt: "" }),
          el("button", { class: "x", "aria-label": "Remove photo", onclick: function () { dropItem(p); S.photos = S.photos.filter(function (x) { return x !== p; }); renderMedia(); renderDests(); renderFoot(); } }, [ic("x")]),
          p.ready ? null : el("div", { class: "bar", "data-bar": p.key }, [el("i")])
        ]));
      });
      if (S.photos.length < L.carousel_max) grid.appendChild(el("label", { class: "ph add", title: "Add photos" }, [ic("plus"), el("input", { type: "file", class: "sr", accept: "image/jpeg,image/png,image/webp", multiple: true, onchange: function (e) { addFiles(e.target.files); } })]));
      view.appendChild(grid);
      S.photos.forEach(function (p) { if (!p.ready) renderProgress(p); });
    }
  }

  // ---------- accounts ----------
  function renderAccounts() {
    var box = $("#accounts"); box.innerHTML = "";
    ORDER.forEach(function (p) {
      var list = channels.filter(function (c) { return c.platform === p; });
      if (!list.length) return;
      var ok = !!kindFor(p);
      var g = el("div", { class: "acc-group" });
      var live = list.filter(function (c) { return c.status === "connected"; });
      var head = el("div", { class: "acc-gh" }, [el("div", { class: "t" }, [ic(p), U.names[p]])]);
      if (ok && live.length > 1) head.appendChild(el("button", { class: "link", onclick: function () {
        var all = live.every(function (c) { return S.selected.indexOf(c.id) >= 0; });
        live.forEach(function (c) { toggle(c.id, !all); });
        renderAccounts(); renderDests(); renderFoot();
      } }, [live.every(function (c) { return S.selected.indexOf(c.id) >= 0; }) ? "Clear" : "All"]));
      g.appendChild(head);
      if (!ok) {
        g.appendChild(el("div", { class: "na" }, [ic("info"), p === "youtube" ? (S.type === "photo" ? "YouTube doesn't let apps make Community posts" : "YouTube needs a video") : "Instagram needs a photo or video"]));
        box.appendChild(g); return;
      }
      var accs = el("div", { class: "accs" });
      list.forEach(function (c) {
        var on = S.selected.indexOf(c.id) >= 0, connected = c.status === "connected";
        var sub = connected ? (c.handle ? "@" + c.handle : "Connected") : (c.status === "error" ? "Reconnect needed" : "Not connected");
        var av = el("span", { class: "avatar" }, [c.avatar ? el("img", { src: c.avatar, alt: "" }) : (c.name || "?").charAt(0).toUpperCase()]);
        var b = el(connected ? "button" : "div", { type: connected ? "button" : null, class: "acc" + (on ? " on" : "") + (connected ? "" : " off"), "aria-pressed": on ? "true" : "false" }, [
          av, el("span", { class: "acc-n" }, [el("b", { text: c.name }), el("small", { text: sub })]),
          connected ? el("span", { class: "check" }, [ic("check")]) : el("a", { class: "link", href: "/channels", text: "Connect" })
        ]);
        if (connected) b.addEventListener("click", function () { toggle(c.id); renderAccounts(); renderDests(); renderFoot(); });
        accs.appendChild(b);
      });
      g.appendChild(accs); box.appendChild(g);
    });
    $("#selCount").textContent = S.selected.length ? S.selected.length + " selected" : "";
  }
  function toggle(id, force) {
    var i = S.selected.indexOf(id), want = force === undefined ? i < 0 : force;
    if (want && i < 0) {
      S.selected.push(id);
      if (!S.dests[id]) S.dests[id] = { title: "", body: "", link: "", privacy: "public", when: "now", at: "", open: true, share: true };
    } else if (!want && i >= 0) S.selected.splice(i, 1);
    S.selected.sort(function (a, b) { return ORDER.indexOf(byId[a].platform) - ORDER.indexOf(byId[b].platform) || channels.indexOf(byId[a]) - channels.indexOf(byId[b]); });
  }

  // ---------- destination cards ----------
  function localMin() {
    var d = new Date(Date.now() + 5 * 60000); d.setSeconds(0, 0);
    var z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    return z.toISOString().slice(0, 16);
  }
  function counter(input, max) {
    var c = el("span", { class: "count" });
    function upd() { var n = input.value.length; c.textContent = n + "/" + max; c.classList.toggle("over", n > max); }
    input.addEventListener("input", upd); upd(); return c;
  }
  function field(label, input, max, extra) {
    return el("label", { class: "field" }, [el("div", { class: "field-top" }, [el("span", { class: "label", text: label }), max ? counter(input, max) : (extra || null)]), input]);
  }
  function bind(input, d, key) { input.value = d[key] || ""; input.addEventListener("input", function () { d[key] = input.value; input.classList.remove("bad"); renderFoot(); }); return input; }

  function renderDests() {
    var box = $("#dests"); box.innerHTML = "";
    $("#destEmpty").classList.toggle("hidden", S.selected.length > 0);
    $("#quick").classList.toggle("hidden", S.selected.length < 2);
    $("#quickAt").min = localMin();
    S.selected.forEach(function (id, idx) {
      var c = byId[id], d = S.dests[id], p = c.platform, kind = kindFor(p);
      var card = el("div", { class: "card dest" + (d.open ? "" : " closed"), "data-id": id });
      var h = el("div", { class: "dest-h" }, [
        el("span", { class: "pf" }, [ic(p)]),
        el("div", { class: "who" }, [el("b", { text: c.name }), el("small", { text: U.names[p] + (d.when === "later" && d.at ? " · " + U.fmtTime(new Date(d.at).toISOString()) : "") })]),
        el("span", { class: "pill " + (kind === "short" || kind === "reel" ? "dark" : "") , text: KIND[kind] || "" }),
        ic("chev", "chev")
      ]);
      h.addEventListener("click", function () { d.open = !d.open; card.classList.toggle("closed", !d.open); });
      card.appendChild(h);
      var b = el("div", { class: "dest-b" });
      if (p === "youtube") {
        b.appendChild(field("Title", bind(el("input", { class: "input", maxlength: 120, placeholder: "Video title", "data-k": "title" }), d, "title"), L.yt_title));
        b.appendChild(field("Description", bind(el("textarea", { class: "textarea", placeholder: "Description, links, #hashtags", "data-k": "body" }), d, "body"), L.yt_desc));
        var sel = el("select", { class: "select" }, ["public", "unlisted", "private"].map(function (v) { return el("option", { value: v, text: v.charAt(0).toUpperCase() + v.slice(1) }); }));
        sel.value = d.privacy; sel.addEventListener("change", function () { d.privacy = sel.value; });
        b.appendChild(field("Visibility", sel));
        if (kind === "short") b.appendChild(el("div", { class: "hint" }, [ic("info"), "#Shorts is added for you."]));
      } else if (p === "instagram") {
        b.appendChild(field("Caption", bind(el("textarea", { class: "textarea", placeholder: "Caption and #hashtags", "data-k": "body" }), d, "body"), L.ig_caption));
        if (kind === "reel") {
          var sw = el("input", { type: "checkbox" }); sw.checked = d.share !== false;
          sw.addEventListener("change", function () { d.share = sw.checked; });
          b.appendChild(el("label", { class: "switch" }, [sw, "Also show in profile grid"]));
        }
      } else {
        if (kind === "video") b.appendChild(field("Title", bind(el("input", { class: "input", maxlength: 255, placeholder: "Video title", "data-k": "title" }), d, "title")));
        b.appendChild(field(kind === "text" ? "Post" : (kind === "video" ? "Description" : "Caption"), bind(el("textarea", { class: "textarea", placeholder: kind === "text" ? "What's new?" : "Write a caption", "data-k": "body" }), d, "body")));
        if (kind === "text") b.appendChild(field("Link", bind(el("input", { class: "input", type: "url", placeholder: "https://", "data-k": "link" }), d, "link")));
      }
      // when
      var at = el("input", { class: "input" + (d.when === "later" ? "" : " hidden"), type: "datetime-local", min: localMin(), "aria-label": "Schedule time" });
      at.value = d.at || "";
      at.addEventListener("change", function () { d.at = at.value; at.classList.remove("bad"); updHead(); renderFoot(); });
      var seg = el("div", { class: "seg sm" });
      [["now", "Now"], ["later", "Schedule"]].forEach(function (o) {
        seg.appendChild(el("button", { type: "button", class: d.when === o[0] ? "on" : "", onclick: function () {
          d.when = o[0]; seg.querySelectorAll("button").forEach(function (x, i) { x.classList.toggle("on", i === (o[0] === "now" ? 0 : 1)); });
          at.classList.toggle("hidden", d.when !== "later");
          if (d.when === "later" && !d.at) { var q = $("#quickAt").value; if (q) { d.at = q; at.value = q; } }
          if (d.when === "later") at.focus();
          updHead(); renderFoot();
        } }, [o[1]]));
      });
      function updHead() { h.querySelector("small").textContent = U.names[p] + (d.when === "later" && d.at ? " · " + U.fmtTime(new Date(d.at).toISOString()) : ""); }
      b.appendChild(el("div", { class: "when" }, [seg, at]));
      if (p === "instagram") {
        var igh = el("div", { class: "hint" + (d.when === "later" ? "" : " hidden") }, [ic("info"), "Instagram has no built-in scheduling. UNIK Post publishes it at the time."]);
        seg.addEventListener("click", function () { igh.classList.toggle("hidden", d.when !== "later"); });
        b.appendChild(igh);
      }
      if (idx === 0 && S.selected.length > 1) {
        b.appendChild(el("div", { class: "hint" }, [el("button", { type: "button", class: "link", onclick: copyAll }, ["Copy text to all accounts"])]));
      }
      b.appendChild(el("div", { class: "dest-err hidden" }));
      card.appendChild(b);
      box.appendChild(card);
    });
  }
  function copyAll() {
    var first = S.dests[S.selected[0]];
    S.selected.slice(1).forEach(function (id) { var d = S.dests[id]; d.body = first.body; if (byId[id].platform !== "instagram") d.title = first.title; if (!d.title && first.body && byId[id].platform === "youtube") d.title = first.body.split("\n")[0].slice(0, 100); });
    renderDests(); renderFoot(); U.toast("Copied to all");
  }
  $("#quickApply").addEventListener("click", function () {
    var v = $("#quickAt").value; if (!v) { $("#quickAt").focus(); return; }
    S.selected.forEach(function (id) { S.dests[id].when = "later"; S.dests[id].at = v; });
    renderDests(); renderFoot(); U.toast("Same time set for all");
  });
  $("#quickNow").addEventListener("click", function () { S.selected.forEach(function (id) { S.dests[id].when = "now"; }); renderDests(); renderFoot(); });

  // ---------- footer + submit ----------
  function mediaState() {
    if (S.type === "text") return { ok: true };
    var items = S.type === "video" ? (S.video ? [S.video] : []) : S.photos;
    if (!items.length) return { ok: false, msg: S.type === "video" ? "Add a video" : "Add photos" };
    if (items.some(function (i) { return i.error; })) return { ok: false, msg: "Upload failed. Remove and add again." };
    var pending = items.filter(function (i) { return !i.ready; });
    if (pending.length) {
      var total = items.reduce(function (a, i) { return a + i.file.size; }, 0), done = items.reduce(function (a, i) { return a + i.file.size * (i.ready ? 100 : i.progress) / 100; }, 0);
      return { ok: false, msg: "Uploading " + Math.floor(done * 100 / total) + "%", uploading: true };
    }
    return { ok: true };
  }
  function renderFoot() {
    var sum = $("#sum"), btn = $("#postBtn"), ms = mediaState(), n = S.selected.length;
    var later = S.selected.filter(function (id) { return S.dests[id].when === "later"; }).length;
    btn.textContent = !n || later === 0 ? "Post now" : (later === n ? "Schedule" : "Post & schedule");
    sum.innerHTML = "";
    if (!n) { sum.textContent = ms.ok || S.type === "text" ? "Pick accounts" : ms.msg; }
    else {
      sum.appendChild(el("b", { text: n + (n === 1 ? " account" : " accounts") }));
      var parts = []; if (n - later) parts.push((n - later) + " now"); if (later) parts.push(later + " scheduled");
      sum.appendChild(document.createTextNode(" · " + parts.join(" · ")));
      if (!ms.ok) sum.appendChild(document.createTextNode(" · " + ms.msg));
    }
    btn.disabled = S.busy || !n || !ms.ok;
  }
  function showErr(id, msg) {
    var card = document.querySelector('.dest[data-id="' + id + '"]'); if (!card) return;
    card.classList.remove("closed"); S.dests[id].open = true;
    var e = card.querySelector(".dest-err"); e.textContent = msg; e.classList.remove("hidden");
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function validate() {
    var minT = Date.now() + 2 * 60000;
    for (var i = 0; i < S.selected.length; i++) {
      var id = S.selected[i], c = byId[id], d = S.dests[id], k = kindFor(c.platform);
      var card = document.querySelector('.dest[data-id="' + id + '"]');
      if (c.platform === "youtube" && !d.title.trim()) { card.querySelector('[data-k="title"]').classList.add("bad"); return [id, "Add a title"]; }
      if (c.platform === "youtube" && d.title.length > L.yt_title) return [id, "Title is too long"];
      if (c.platform === "youtube" && /[<>]/.test(d.title)) return [id, "Title can't have < or >"];
      if (c.platform === "instagram" && d.body.length > L.ig_caption) return [id, "Caption is too long"];
      if (k === "text" && !d.body.trim() && !d.link.trim()) return [id, "Write something"];
      if (d.when === "later") {
        if (!d.at) return [id, "Pick a time"];
        if (new Date(d.at).getTime() < minT) return [id, "Pick a time at least 2 minutes ahead"];
      }
    }
    return null;
  }
  $("#postBtn").addEventListener("click", function () {
    document.querySelectorAll(".dest-err").forEach(function (e) { e.classList.add("hidden"); });
    var bad = validate(); if (bad) { showErr(bad[0], bad[1]); return; }
    var media = S.type === "video" ? [S.video.id] : (S.type === "photo" ? S.photos.map(function (p) { return p.id; }) : []);
    var body = { type: S.type, override: S.override, media_ids: media, destinations: S.selected.map(function (id) {
      var d = S.dests[id];
      return { channel_id: id, title: d.title, body: d.body, link: d.link, privacy: d.privacy, share_to_feed: d.share !== false,
        schedule_at: d.when === "later" && d.at ? new Date(d.at).toISOString() : null };
    }) };
    S.busy = true; renderFoot(); $("#postBtn").textContent = "Sending…";
    U.api("POST", "/api/posts", body).then(function () {
      U.toast("Sent to queue"); setTimeout(function () { location.href = "/queue"; }, 500);
    }, function (e) {
      S.busy = false; renderFoot();
      if (e.data && e.data.channel_id) showErr(e.data.channel_id, e.message); else U.toast(e.message, "error");
    });
  });
  window.addEventListener("beforeunload", function (e) {
    var up = (S.video && !S.video.ready && !S.video.error) || S.photos.some(function (p) { return !p.ready; });
    if (up && !S.busy) { e.preventDefault(); e.returnValue = ""; }
  });

  renderMedia(); renderAccounts(); renderDests(); renderFoot();
  window.__S = S;
})();
