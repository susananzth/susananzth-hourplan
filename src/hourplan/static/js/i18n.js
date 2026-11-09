// Interface texts for scripts. Spanish is the source; the page carries the other language's
// lookups in <script type="application/json" id="hp-i18n"> (data, not code: allowed by the CSP).
(function () {
  "use strict";

  var catalog = null;

  function load() {
    if (catalog === null) {
      var el = document.getElementById("hp-i18n");
      try { catalog = el ? JSON.parse(el.textContent) : {}; } catch (err) { catalog = {}; }
    }
    return catalog;
  }

  // hpT(text) -> the text in the page language.
  window.hpT = function (text) { return load()[text] || text; };

  // hpF(text, a, b) -> translated text with each %s filled in order.
  window.hpF = function (text) {
    var args = arguments;
    var i = 1;
    return window.hpT(text).replace(/%s/g, function () { return String(args[i++]); });
  };
})();
