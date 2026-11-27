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
  function ic(name, cls) {
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
