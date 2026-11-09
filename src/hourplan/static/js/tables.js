// Every data table gets a search box, a page size selector and a pager, in the browser.
// The rows are already on the page; this file only hides and shows them.
// Opt out with data-tabla="fija" on the table. Rows with data-fija stay always visible
// (summaries); a row with a single cell spanning the table is the "no data" message.
(function () {
  "use strict";

  var SIZES = [10, 20, 50, 100];
  var WINDOW = 5; // page numbers shown at once
  var count = 0;

  var hpT = function (s) { return window.hpT ? window.hpT(s) : s; };
  var hpF = function () { return window.hpF ? window.hpF.apply(null, arguments) : arguments[0]; };

  var BUTTON = "inline-flex min-w-9 items-center justify-center rounded-md border border-line px-2.5 py-1 text-sm text-ink hover:bg-surface focus-visible:outline-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent";
  var CURRENT = "inline-flex min-w-9 items-center justify-center rounded-md border border-brand-600 bg-brand-600 px-2.5 py-1 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600";
  var FIELD = "rounded-md border border-line-strong bg-surface px-2 py-1 text-sm text-ink focus:border-brand-600 focus:outline-2 focus:outline-brand-600";

  function plain(text) {
    return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  // What a person sees in the row: its text, without the unchosen options of its lists.
  function rowText(row) {
    var parts = [];
    var walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.parentElement.closest("select, option, script, .sr-only")) { parts.push(node.nodeValue); }
    }
    row.querySelectorAll("select").forEach(function (s) {
      if (s.selectedOptions[0]) { parts.push(s.selectedOptions[0].textContent); }
    });
    row.querySelectorAll("input:not([type=hidden])").forEach(function (i) { parts.push(i.value); });
    return plain(parts.join(" "));
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

  function enhance(table) {
    if (table.dataset.tablaLista || table.dataset.tabla === "fija" || !table.tBodies.length) { return; }
    table.dataset.tablaLista = "1";
    var id = "tabla-" + (++count);

    var rows = [];
    var placeholders = [];
    Array.prototype.forEach.call(table.tBodies, function (body) {
      Array.prototype.forEach.call(body.rows, function (row) {
        if (row.hasAttribute("data-fija")) { return; }
        if (row.cells.length === 1 && row.cells[0].colSpan > 1) { placeholders.push(row); } else { rows.push(row); }
      });
    });
    var columns = table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells.length : 1;
    var state = { page: 1, size: SIZES[0], query: "" };

    // Controls around the scrolling wrapper, inside one container so a re-render replaces them.
    var scroller = table.parentElement && table.parentElement.classList.contains("overflow-x-auto") ? table.parentElement : table;
    var box = scroller.parentElement;
    if (!box.dataset.tablaCaja) {
      box = el("div");
      box.dataset.tablaCaja = "1";
      box.className = scroller.className.match(/\bmb-\d+\b/) ? scroller.className.match(/\bmb-\d+\b/)[0] : "";
      scroller.classList.remove(box.className || "_");
      scroller.parentElement.insertBefore(box, scroller);
      box.appendChild(scroller);
    } else {
      box.querySelectorAll("[data-tabla-control]").forEach(function (old) { old.remove(); });
    }

    var top = el("div", "mb-2 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-muted");
    top.dataset.tablaControl = "1";
    var sizeLabel = el("label", "flex items-center gap-2");
    var size = el("select", FIELD);
    size.id = id + "-tam";
    SIZES.forEach(function (n) { var o = el("option", "", String(n)); o.value = String(n); size.appendChild(o); });
    sizeLabel.append(hpT("Mostrar"), size, hpT("registros"));
    var searchLabel = el("label", "flex items-center gap-2");
    var search = el("input", FIELD);
    search.type = "search";
    search.id = id + "-buscar";
    search.autocomplete = "off";
    search.setAttribute("aria-controls", table.id || (table.id = id));
    searchLabel.append(hpT("Buscar") + ":", search);
    top.append(sizeLabel, searchLabel);

    var bottom = el("div", "mt-2 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-muted");
    bottom.dataset.tablaControl = "1";
    var info = el("p");
    info.setAttribute("aria-live", "polite");
    var pager = el("nav", "flex flex-wrap items-center gap-1");
    pager.setAttribute("aria-label", hpT("Paginación de la tabla"));
    bottom.append(info, pager);

    box.insertBefore(top, scroller);
    box.appendChild(bottom);

    var empty = el("tr");
    empty.dataset.tablaVacia = "1";
    var emptyCell = el("td", "px-4 py-4 text-ink-muted", hpT("No hay registros que coincidan."));
    emptyCell.colSpan = columns;
    empty.appendChild(emptyCell);
    table.tBodies[0].appendChild(empty);

    function button(label, text, page, disabled, current) {
      var b = el("button", current ? CURRENT : BUTTON, text);
      b.type = "button";
      b.dataset.ir = String(page);
      b.setAttribute("aria-label", label);
      if (current) { b.setAttribute("aria-current", "page"); }
      b.disabled = disabled;
      return b;
    }

    function render(focusAction) {
      var words = plain(state.query).split(/\s+/).filter(Boolean);
      var matched = words.length ? rows.filter(function (r) {
        var text = rowText(r);
        return words.every(function (w) { return text.indexOf(w) >= 0; });
      }) : rows;
      var total = matched.length;
      var pages = Math.max(1, Math.ceil(total / state.size));
      state.page = Math.min(Math.max(1, state.page), pages);
      var from = (state.page - 1) * state.size;
      var to = Math.min(from + state.size, total);

      rows.forEach(function (r) { r.hidden = true; });
      matched.slice(from, to).forEach(function (r) { r.hidden = false; });
      placeholders.forEach(function (r) { r.hidden = rows.length > 0; });
      empty.hidden = !(rows.length > 0 && total === 0);

      var shown = total ? hpF("Mostrando %s a %s de %s registros", from + 1, to, total) : hpF("Mostrando %s a %s de %s registros", 0, 0, 0);
      if (words.length) { shown += " " + hpF("(filtrados de %s en total)", rows.length); }
      info.textContent = shown + " · " + hpF("Página %s de %s", state.page, pages);

      pager.textContent = "";
      var first = state.page === 1;
      var last = state.page === pages;
      pager.append(
        button(hpT("Primera página"), "«", 1, first),
        button(hpT("Página anterior"), "‹", state.page - 1, first)
      );
      var start = Math.max(1, Math.min(state.page - Math.floor(WINDOW / 2), pages - WINDOW + 1));
      for (var n = start; n <= Math.min(pages, start + WINDOW - 1); n++) {
        pager.appendChild(button(hpF("Página %s", n), String(n), n, false, n === state.page));
      }
      pager.append(
        button(hpT("Página siguiente"), "›", state.page + 1, last),
        button(hpT("Última página"), "»", pages, last)
      );
      if (focusAction) {
        var again = pager.querySelector('[aria-label="' + focusAction + '"]:not(:disabled)') || pager.querySelector("[aria-current]");
        if (again) { again.focus(); }
      }
    }

    pager.addEventListener("click", function (event) {
      var b = event.target.closest("button[data-ir]");
      if (!b || b.disabled) { return; }
      state.page = parseInt(b.dataset.ir, 10);
      render(b.getAttribute("aria-label"));
    });
    size.addEventListener("change", function () { state.size = parseInt(size.value, 10); state.page = 1; render(); });
    search.addEventListener("input", function () { state.query = search.value; state.page = 1; render(); });
    render();
  }

  function scan(root) {
    (root || document).querySelectorAll("main table").forEach(enhance);
  }

  document.addEventListener("DOMContentLoaded", function () { scan(document); });
  document.addEventListener("htmx:afterSettle", function () { scan(document); });
})();
