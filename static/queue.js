(function () {
  "use strict";
  var U = window.UP, el = U.el, ic = U.ic;
  var KIND = { short: "Short", long: "Long video", reel: "Reel", video: "Video", photo: "Photo", carousel: "Carousel", photos: "Album", text: "Post" };
  var filter = "all", timer = null, last = null;
  document.querySelectorAll("#filters button").forEach(function (b) {
    b.addEventListener("click", function () {
      filter = b.getAttribute("data-f");
      document.querySelectorAll("#filters button").forEach(function (x) { x.classList.toggle("on", x === b); });
      render();
    });
  });
  function group(st) {
    if (st === "published") return "published";
    if (st === "failed") return "failed";
    if (st === "scheduled" || st === "waiting") return "scheduled";
    if (st === "canceled") return "canceled";
    return "active";
  }
  function pill(d) {
    var st = d.status;
    if (st === "published") return el("span", { class: "pill ok" }, [el("span", { class: "dot" }), "Published"]);
    if (st === "scheduled") return el("span", { class: "pill blue" }, [ic("clock"), U.fmtTime(d.schedule_at)]);
    if (st === "waiting") return el("span", { class: "pill blue" }, [ic("clock"), d.schedule_at ? U.fmtTime(d.schedule_at) : "Waiting"]);
    if (st === "failed") return el("span", { class: "pill err" }, [el("span", { class: "dot" }), "Failed"]);
    if (st === "canceled") return el("span", { class: "pill" }, ["Canceled"]);
    if (st === "uploading") return el("span", { class: "pill warn" }, [el("span", { class: "dot" }), "Uploading" + (d.progress != null ? " " + d.progress + "%" : "")]);
    if (st === "processing") return el("span", { class: "pill warn" }, [el("span", { class: "dot" }), "Processing"]);
    return el("span", { class: "pill warn" }, [el("span", { class: "dot" }), "Starting"]);
  }
  function act(url, okMsg) {
    U.api("POST", url).then(function () { if (okMsg) U.toast(okMsg); load(); }, function (e) { U.toast(e.message, "error"); });
  }
  function sub(d) {
    var bits = [KIND[d.kind] || d.kind];
    if (d.status === "scheduled") bits.push(d.platform === "youtube" ? "held by YouTube" : "held by Facebook");
    else if (d.status === "waiting") bits.push("posts automatically");
    else if (d.detail && d.status !== "failed") bits.push(d.detail);
    if ((d.status === "queued" || d.status === "uploading") && d.schedule_at) bits.push("for " + U.fmtTime(d.schedule_at));
    return bits.join(" · ");
  }
  function render() {
    var list = document.getElementById("qlist"); list.innerHTML = "";
    var posts = (last && last.posts) || [];
    var shown = 0;
    posts.forEach(function (p) {
      var ds = p.destinations.filter(function (d) { return filter === "all" || group(d.status) === filter; });
      if (!ds.length) return;
      shown++;
      var thumb = el("div", { class: "qthumb" }, [p.thumb ? el("img", { src: p.thumb, alt: "" }) : ic(p.type === "video" ? "video" : (p.type === "photo" ? "image" : "text"))]);
      var card = el("div", { class: "card" }, [el("div", { class: "qpost-h" }, [
        thumb,
        el("div", { class: "who" }, [el("b", { text: p.label || "Post" }), el("small", { text: U.fmtTime(p.created_at) + " · " + p.destinations.length + (p.destinations.length === 1 ? " account" : " accounts") })]),
        el("button", { class: "icon-btn", title: "Delete from list", "aria-label": "Delete from list", onclick: function () {
          if (confirm("Remove this post from the list? Posts already live stay live.")) act("/api/posts/" + p.id + "/delete");
        } }, [ic("trash")])
      ])]);
      ds.forEach(function (d) {
        var acts = el("div", { class: "acts" });
        if (d.status === "uploading" && d.progress != null) { var bar = el("div", { class: "bar" }, [el("i")]); bar.firstChild.style.width = d.progress + "%"; acts.appendChild(bar); }
        if (d.url && (d.status === "published" || d.status === "scheduled")) acts.appendChild(el("a", { class: "btn btn-sm btn-ghost", href: d.url, target: "_blank", rel: "noopener" }, [ic("ext"), "Open"]));
        if (d.status === "failed" || d.status === "canceled") acts.appendChild(el("button", { class: "btn btn-sm", onclick: function () { act("/api/destinations/" + d.id + "/retry", "Retrying"); } }, [ic("refresh"), "Retry"]));
        if (d.status === "queued" || d.status === "waiting") acts.appendChild(el("button", { class: "btn btn-sm btn-ghost", onclick: function () { act("/api/destinations/" + d.id + "/cancel", "Canceled"); } }, ["Cancel"]));
        card.appendChild(el("div", { class: "qdest" }, [
          el("span", { class: "pf" }, [ic(d.platform)]),
          el("div", { class: "who" }, [el("b", { text: d.channel }), el("small", { text: sub(d) }), d.status === "failed" && d.detail ? el("small", { class: "e", text: d.detail }) : null]),
          el("div", { class: "st" }, [pill(d)]),
          acts
        ]));
      });
      list.appendChild(card);
    });
    document.getElementById("qempty").classList.toggle("hidden", shown > 0);
  }
  function load() {
    clearTimeout(timer);
    U.api("GET", "/api/queue").then(function (j) { last = j; render(); timer = setTimeout(load, j.active ? 4000 : 30000); },
      function () { timer = setTimeout(load, 10000); });
  }
  load();
})();
