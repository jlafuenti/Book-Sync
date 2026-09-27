/*
 * Tandem live page probe (issue #730; right-to-left since #736). The reader
 * evaluates this in Readium's live page, followed by a call to
 * window.tandemLiveProbe(fragments) - see LivePageProbe.script. It reads which
 * CSS column (screen page) the reader shows, how many the resource has, and
 * which of the given page-list element ids sit at or before that column, and
 * returns them as a JSON string.
 *
 * Right-to-left: Readium paginates an RTL resource with columns that run from
 * the right, and the scrolling element then reports scrollLeft <= 0 - 0 on the
 * first page, -k * innerWidth on page k + 1. An element's column counts from
 * that start too.
 *
 * Tested by web/src/androidReaderScripts.test.js.
 */
(function () {
  'use strict';

  window.tandemLiveProbe = function (fragments) {
    var se = document.scrollingElement;
    var w = window.innerWidth;
    var rtl = se.scrollLeft < 0 || getComputedStyle(document.documentElement).direction === 'rtl';
    var col = Math.round(Math.abs(se.scrollLeft) / w);
    var out = { page: col + 1, total: Math.max(1, Math.round(se.scrollWidth / w)), before: [] };
    (fragments || []).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      // Where the element is when the page is scrolled back to the start.
      var x = el.getBoundingClientRect().left + se.scrollLeft;
      var elCol = rtl ? -Math.floor(x / w) : Math.floor(x / w);
      if (elCol <= col) out.before.push(id);
    });
    return JSON.stringify(out);
  };
})();
