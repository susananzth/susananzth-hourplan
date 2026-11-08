// Stopwatch bar: second counter, browser notifications and inactivity prompt.
// The server owns the time; this file only shows it and reports what the browser sees.
(function () {
  "use strict";

  var activeSince = Date.now();
  var idleSince = null;
  var detector = null;
  var dialog = null;

  function bar() { return document.getElementById("timer-bar"); }
  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  // ---- counter
  function tick() {
    var now = Date.now();
    document.querySelectorAll("time[data-elapsed]").forEach(function (el) {
      if (!el._t0) { el._t0 = now; }
      var seconds = parseInt(el.dataset.elapsed, 10);
      if (el.dataset.running === "1") { seconds += Math.floor((now - el._t0) / 1000); }
      el.textContent = pad(Math.floor(seconds / 3600)) + ":" + pad(Math.floor(seconds % 3600 / 60)) + ":" + pad(seconds % 60);
    });
  }
  setInterval(tick, 1000);

  // ---- alerts
  function show(item) {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(item.title, { body: item.body });
      return;
    }
    var region = document.getElementById("alert-region");
    if (!region) { return; }
    var box = document.createElement("div");
    box.className = "rounded-md border border-line-strong bg-surface-raised p-3 text-sm text-ink shadow-sm";
    var strong = document.createElement("strong");
    strong.textContent = item.title;
    var text = document.createElement("p");
    text.textContent = item.body;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "mt-1 text-xs underline";
    close.textContent = "Cerrar";
    close.addEventListener("click", function () { box.remove(); });
    box.append(strong, text, close);
    region.append(box);
  }
  function deliverAlerts() {
    var el = bar();
    if (!el || !el.dataset.alerts) { return; }
    var items = [];
    try { items = JSON.parse(el.dataset.alerts); } catch (e) { items = []; }
    items.forEach(show);
    el.dataset.alerts = "[]";
  }
  document.body.addEventListener("htmx:afterSwap", function () { tick(); deliverAlerts(); });

  // ---- inactivity
  function threshold() {
    var el = bar();
    return el ? parseInt(el.dataset.idleSeconds || "300", 10) * 1000 : 300000;
  }
  function running() {
    var el = bar();
    return !!el && el.dataset.running === "1";
  }
  function markIdle(since) {
    if (running() && idleSince === null) { idleSince = since; }
  }
  function back() {
    if (idleSince === null || !dialog || dialog.open) { return; }
    var minutes = Math.max(1, Math.round((Date.now() - idleSince) / 60000));
    document.getElementById("idle-minutes").textContent = String(minutes);
    dialog.showModal();
  }
  function activity() {
    var now = Date.now();
    if (idleSince === null && running() && now - activeSince > threshold()) { idleSince = activeSince; }
    activeSince = now;
    back();
  }
  function answer(choice) {
    var seconds = Math.round((Date.now() - idleSince) / 1000);
    idleSince = null;
    dialog.close();
    var body = new URLSearchParams({ opcion: choice, segundos: String(seconds) });
    fetch("/cronometro/inactividad", {
      method: "POST",
      headers: { "X-CSRFToken": csrf(), "Content-Type": "application/x-www-form-urlencoded" },
      body: body,
      credentials: "same-origin"
    }).then(function () {
      if (window.htmx) { window.htmx.ajax("POST", "/cronometro/latido", { target: "#timer-bar", swap: "outerHTML" }); }
    });
  }

  function startDetector() {
    if (!("IdleDetector" in window)) { return Promise.resolve(false); }
    var controller = new AbortController();
    detector = new window.IdleDetector();
    detector.addEventListener("change", function () {
      var idle = detector.userState === "idle" || detector.screenState === "locked";
      if (idle) { markIdle(Date.now() - threshold()); } else { back(); }
    });
    return detector.start({ threshold: Math.max(60000, threshold()), signal: controller.signal })
      .then(function () { return true; }, function () { detector = null; return false; });
  }

  document.addEventListener("DOMContentLoaded", function () {
    dialog = document.getElementById("idle-dialog");
    if (dialog) {
      dialog.querySelectorAll("[data-idle-choice]").forEach(function (button) {
        button.addEventListener("click", function () { answer(button.dataset.idleChoice); });
      });
    }
    ["mousemove", "keydown", "click", "scroll", "touchstart"].forEach(function (name) {
      document.addEventListener(name, activity, { passive: true });
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { activity(); } });
    setInterval(function () {
      if (!detector && running() && idleSince === null && Date.now() - activeSince > threshold()) {
        markIdle(activeSince);
      }
    }, 10000);

    if (navigator.permissions && "IdleDetector" in window) {
      navigator.permissions.query({ name: "idle-detection" }).then(function (state) {
        if (state.state === "granted") { startDetector(); }
      }, function () {});
    }
    var notifications = document.querySelector("[data-enable-notifications]");
    if (notifications) {
      notifications.addEventListener("click", function () {
        if ("Notification" in window) { Notification.requestPermission(); }
      });
    }
    var precise = document.querySelector("[data-enable-idle]");
    if (precise) {
      precise.addEventListener("click", function () {
        if ("IdleDetector" in window) {
          window.IdleDetector.requestPermission().then(function (result) {
            if (result === "granted") { startDetector(); }
          });
        }
      });
    }
  });
})();
