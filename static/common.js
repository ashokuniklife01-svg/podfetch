(function () {
  "use strict";
  var csrf = document.body.getAttribute("data-csrf");
  function api(method, url, body) {
    var opt = { method: method, headers: { "X-CSRF-Token": csrf }, credentials: "same-origin" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); }
        if (!r.ok) { var e = new Error(j.error || ("Error " + r.status)); e.data = j; e.status = r.status; throw e; }
        return j;
      });
    });
  }
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach(function (c) { if (c !== null && c !== undefined && c !== false) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  var BRAND = {
    youtube: '<svg class="brand-icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="#FF0000" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z"/><path fill="#fff" d="M9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>',
    instagram: '<svg class="brand-icon" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="url(#ig-brand-grad)"/><rect x="6" y="6" width="12" height="12" rx="3.6" fill="none" stroke="#fff" stroke-width="1.7"/><circle cx="12" cy="12" r="3.15" fill="none" stroke="#fff" stroke-width="1.7"/><circle cx="16.45" cy="7.55" r="1.05" fill="#fff"/></svg>',
    facebook: '<svg class="brand-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#fff"/><path fill="#1877F2" d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.791-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>'
  };
  function ic(name, cls) {
    if (BRAND[name]) {
      var t = document.createElement("template");
      t.innerHTML = BRAND[name];
      var s = t.content.firstChild.cloneNode(true);
      if (cls) s.setAttribute("class", (s.getAttribute("class") || "") + " " + cls);
      return s;
    }
    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("aria-hidden", "true"); if (cls) s.setAttribute("class", cls);
    var u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", "#i-" + name); s.appendChild(u); return s;
  }
  function toast(msg, kind) {
    var box = document.getElementById("toasts");
    if (!box) { box = el("div", { class: "toasts", id: "toasts" }); document.body.appendChild(box); }
    var t = el("div", { class: "toast " + (kind || ""), text: msg });
    box.appendChild(t);
    setTimeout(function () { t.remove(); }, 4200);
  }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso), now = new Date();
    var sameDay = d.toDateString() === now.toDateString();
    var tm = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (sameDay) return "Today " + tm;
    var tmr = new Date(now); tmr.setDate(now.getDate() + 1);
    if (d.toDateString() === tmr.toDateString()) return "Tomorrow " + tm;
    return d.toLocaleDateString([], { day: "numeric", month: "short" }) + ", " + tm;
  }
  function fmtSize(b) {
    if (b > 1073741824) return (b / 1073741824).toFixed(2) + " GB";
    if (b > 1048576) return (b / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(b / 1024)) + " KB";
  }
  function fmtDur(s) {
    s = Math.round(s || 0); var m = Math.floor(s / 60), r = s % 60;
    if (m >= 60) return Math.floor(m / 60) + ":" + String(m % 60).padStart(2, "0") + ":" + String(r).padStart(2, "0");
    return m + ":" + String(r).padStart(2, "0");
  }
  window.UP = { api: api, el: el, ic: ic, toast: toast, fmtTime: fmtTime, fmtSize: fmtSize, fmtDur: fmtDur,
    names: { youtube: "YouTube", instagram: "Instagram", facebook: "Facebook" } };
  setTimeout(function () { var t = document.getElementById("toasts"); if (t) t.querySelectorAll(".toast").forEach(function (x) { x.remove(); }); }, 5000);
})();
