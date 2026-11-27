(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var setupH = $("[data-toggle=setup]");
  if (setupH) setupH.addEventListener("click", function () { $("#setupBody").classList.toggle("hidden"); $("#setup").classList.toggle("closed"); });
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var v = b.getAttribute("data-copy");
      (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).then(function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1500); }, function () { window.prompt("Copy", v); });
    });
  });
  function closeMenus(except) { document.querySelectorAll(".menu-pop").forEach(function (m) { if (m !== except) m.classList.add("hidden"); }); }
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-menu]");
    if (t) { var pop = t.nextElementSibling; closeMenus(pop); pop.classList.toggle("hidden"); e.stopPropagation(); return; }
    if (!e.target.closest(".menu-pop")) closeMenus();
  });
  var dlgEdit = $("#dlgEdit"), formEdit = $("#formEdit"), dlgConf = $("#dlgConfirm"), formConf = $("#formConfirm");
  document.querySelectorAll("[data-close]").forEach(function (b) { b.addEventListener("click", function () { b.closest("dialog").close(); }); });
  var labels = { youtube: "Channel name", instagram: "Instagram handle or link", facebook: "Page handle or link" };
  document.querySelectorAll("[data-add]").forEach(function (b) {
    b.addEventListener("click", function () {
      var p = b.getAttribute("data-add");
      formEdit.action = "/channels/add"; $("#editPlatform").value = p;
      $("#editTitle").textContent = "Add " + UP.names[p] + " account";
      $("#editSub").textContent = p === "youtube" ? "Then tap Connect and pick this channel in Google." : "Then connect with Facebook. Matching accounts link automatically.";
      $("#editName").value = ""; $("#editHandle").value = "";
      $("#editHandleWrap").classList.toggle("hidden", p === "youtube");
      $("#editHandleLabel").textContent = labels[p];
      $("#editSave").textContent = "Add";
      dlgEdit.showModal(); $("#editName").focus();
    });
  });
  document.querySelectorAll(".menu-pop [data-act]").forEach(function (b) {
    b.addEventListener("click", function () {
      closeMenus();
      var row = b.closest(".crow"), id = row.getAttribute("data-id"), name = row.getAttribute("data-name"), p = row.getAttribute("data-platform");
      var act = b.getAttribute("data-act");
      if (act === "rename") {
        formEdit.action = "/channels/" + id + "/rename";
        $("#editTitle").textContent = "Rename"; $("#editSub").textContent = "Only changes how it shows here.";
        $("#editName").value = name; $("#editHandle").value = row.getAttribute("data-handle");
        $("#editHandleWrap").classList.toggle("hidden", p === "youtube");
        $("#editHandleLabel").textContent = labels[p]; $("#editSave").textContent = "Save";
        dlgEdit.showModal(); $("#editName").select();
      } else {
        formConf.action = "/channels/" + id + "/" + act;
        $("#confTitle").textContent = (act === "remove" ? "Remove " : "Disconnect ") + name + "?";
        $("#confSub").textContent = act === "remove" ? "It disappears from this site. Waiting posts for it are canceled." : "You can connect it again anytime.";
        $("#confBtn").textContent = act === "remove" ? "Remove" : "Disconnect";
        dlgConf.showModal();
      }
    });
  });
})();
