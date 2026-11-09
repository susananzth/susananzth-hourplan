// Side menu. On wide screens the button hides or shows the menu and the content widens
// (remembered per browser). On narrow screens the menu slides over the content with a
// backdrop. Loaded in <head> without defer so the saved state applies before painting.
(function () {
  "use strict";
  var KEY = "hp-sidebar";
  var root = document.documentElement;
  var wide = window.matchMedia("(min-width: 64rem)");

  try {
    if (localStorage.getItem(KEY) === "closed") root.dataset.sidebar = "closed";
  } catch (e) {
    /* storage blocked: the menu starts open */
  }

  function mobileOpen() {
    return root.classList.contains("hp-nav-open");
  }

  function sync() {
    var button = document.getElementById("hp-nav-toggle");
    if (!button) return;
    var open = wide.matches ? root.dataset.sidebar !== "closed" : mobileOpen();
    button.setAttribute("aria-expanded", open ? "true" : "false");
    var sidebar = document.getElementById("hp-sidebar");
    if (sidebar) sidebar.inert = !open;
  }

  function closeMobile() {
    root.classList.remove("hp-nav-open");
    sync();
  }

  function toggle() {
    if (wide.matches) {
      var closed = root.dataset.sidebar !== "closed";
      if (closed) root.dataset.sidebar = "closed";
      else delete root.dataset.sidebar;
      try {
        localStorage.setItem(KEY, closed ? "closed" : "open");
      } catch (e) {
        /* not remembered */
      }
    } else {
      root.classList.toggle("hp-nav-open");
      if (mobileOpen()) {
        var first = document.querySelector("#hp-sidebar a, #hp-sidebar summary");
        if (first) first.focus();
      }
    }
    sync();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("hp-nav-toggle");
    if (!button) return;
    button.addEventListener("click", toggle);
    var backdrop = document.getElementById("hp-backdrop");
    if (backdrop) backdrop.addEventListener("click", closeMobile);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && mobileOpen()) {
        closeMobile();
        button.focus();
      }
    });
    wide.addEventListener("change", closeMobile);
    sync();
  });
})();
