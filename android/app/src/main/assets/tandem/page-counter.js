/*
 * Tandem page counter (issue #730). Runs in PageCounterWebView's shell
 * document, which already carries Readium's <html style> (the reader's
 * settings) and the head nodes Readium injected into the live page, so no
 * iframe is needed: each resource's body is swapped into this one document and
 * measured.
 *
 * This is the fast method verified by the 2026-09-26 spike: it matched a slow
 * per-iframe count on all 120 resources of the test book and the live reader's
 * own counts. Layout is ~2 ms per resource; the cost is I/O, so every resource
 * is fetched up front and consumed in order. Book stylesheets stay loaded by
 * key and are disabled when a resource does not use them, instead of being
 * reloaded.
 *
 * Pages per resource: Math.round(scrollingElement.scrollWidth / innerWidth),
 * at least 1 (ReadiumCSS paginates with CSS columns one viewport wide).
 * Characters: the body's text with whitespace collapsed, as the web counts it.
 *
 * Results go back through the TandemPageCounter bridge: onDone(JSON of
 * {counts, chars}) or onError(message).
 */
(function () {
  'use strict';

  var BASE = 'https://readium_package/';
  var WAIT_CAP_MS = 5000;
  var parser = new DOMParser();
  var loaded = new Map(); // stylesheet key -> node in this document

  // Resolves when p settles or after ms, whichever is first. Only a guard
  // against a load event that never fires; the Kotlin side has its own timeout.
  function capped(p, ms) {
    return Promise.race([
      Promise.resolve(p).catch(function () {}),
      new Promise(function (r) { setTimeout(r, ms); }),
    ]);
  }

  function isXhtml(contentType, path) {
    if (contentType) return /xml/i.test(contentType);
    return /\.xht(ml)?$/i.test(path);
  }

  function parse(text, xhtml) {
    if (xhtml) {
      var d = parser.parseFromString(text, 'application/xhtml+xml');
      if (!d.getElementsByTagName('parsererror').length && d.body) return d;
    }
    return parser.parseFromString(text, 'text/html');
  }

  function isStylesheetLink(n) {
    if (n.localName.toLowerCase() !== 'link') return false;
    var rel = (n.getAttribute('rel') || '').toLowerCase().split(/\s+/);
    return rel.indexOf('stylesheet') >= 0 && rel.indexOf('alternate') < 0;
  }

  // Book content shares this document with the bridge, so none of its
  // scripts or inline handlers may run here. (The live reader runs them; they
  // do not change layout in any book we have seen.)
  function sanitize(root) {
    root.querySelectorAll('script').forEach(function (s) { s.remove(); });
    var all = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));
    all.forEach(function (el) {
      Array.prototype.slice.call(el.attributes).forEach(function (a) {
        if (/^on/i.test(a.name)) el.removeAttribute(a.name);
      });
    });
  }

  function setAttr(el, name, value) {
    try {
      if (value == null) el.removeAttribute(name); else el.setAttribute(name, value);
    } catch (e) { /* a name HTML will not take */ }
  }

  function copyAttributes(from, to) {
    Array.prototype.slice.call(to.attributes).forEach(function (a) { to.removeAttribute(a.name); });
    Array.prototype.slice.call(from.attributes).forEach(function (a) {
      if (!/^xmlns(:|$)/.test(a.name)) setAttr(to, a.name, a.value);
    });
  }

  async function applyStylesheets(src, url) {
    var want = new Set();
    var nodes = src.head ? Array.prototype.slice.call(src.head.children) : [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var tag = n.localName.toLowerCase();
      if (tag !== 'style' && !isStylesheetLink(n)) continue;
      var href = n.getAttribute('href');
      if ((href || '').indexOf('readium_assets') >= 0) continue;
      // A <style>'s relative url()s resolve against the document it was
      // parsed for, so the same text in another folder is a different sheet.
      var key = href ? new URL(href, url).href : 'style:' + new URL('.', url).href + '\n' + n.textContent;
      want.add(key);
      if (!loaded.has(key)) {
        var c = document.importNode(n, true);
        if (href) c.setAttribute('href', key);
        document.head.appendChild(c);
        loaded.set(key, c);
        if (href) {
          await capped(new Promise(function (r) { c.onload = c.onerror = r; }), WAIT_CAP_MS);
        }
      }
    }
    loaded.forEach(function (node, key) { node.disabled = !want.has(key); });
  }

  async function measure(src, url, base) {
    base.href = url;
    await applyStylesheets(src, url);

    var html = document.documentElement;
    var srcHtml = src.documentElement;
    setAttr(html, 'lang', srcHtml.getAttribute('lang') || srcHtml.getAttribute('xml:lang'));
    setAttr(html, 'dir', srcHtml.getAttribute('dir'));

    sanitize(src.body);
    copyAttributes(src.body, document.body);
    document.body.replaceChildren.apply(
      document.body,
      Array.prototype.slice.call(src.body.childNodes).map(function (n) { return document.importNode(n, true); })
    );

    // Force style and layout first, so any web font the new content needs has
    // started loading before fonts.ready is read.
    void document.body.offsetWidth;
    await capped(document.fonts.ready, WAIT_CAP_MS);
    await capped(Promise.all(Array.prototype.slice.call(document.images).map(function (im) {
      return im.complete ? 0 : new Promise(function (r) { im.onload = im.onerror = r; });
    })), WAIT_CAP_MS);

    var se = document.scrollingElement;
    return {
      pages: Math.max(1, Math.round(se.scrollWidth / window.innerWidth)),
      chars: (document.body.textContent || '').replace(/\s+/g, ' ').trim().length,
    };
  }

  async function run(hrefs) {
    var base = document.createElement('base');
    document.head.prepend(base);

    var urls = hrefs.map(function (h) {
      var u = new URL(h, BASE);
      u.hash = '';
      return u;
    });
    // Fetched concurrently, consumed in order.
    var texts = urls.map(function (u) {
      return fetch(u.href).then(function (r) {
        if (!r.ok) return { ok: false };
        return r.text().then(function (t) { return { ok: true, text: t, type: r.headers.get('content-type') }; });
      }, function () { return { ok: false }; });
    });

    var counts = [];
    var chars = [];
    for (var i = 0; i < urls.length; i++) {
      var got = await texts[i];
      texts[i] = null;
      if (!got.ok) {
        // Readium shows an error page for a resource it cannot read: one page.
        counts.push(1);
        chars.push(0);
        continue;
      }
      var src = parse(got.text, isXhtml(got.type, urls[i].pathname));
      var m = await measure(src, urls[i].href, base);
      counts.push(m.pages);
      chars.push(m.chars);
    }
    return { counts: counts, chars: chars };
  }

  window.tandemCountPages = function (hrefs) {
    run(hrefs).then(
      function (r) { TandemPageCounter.onDone(JSON.stringify(r)); },
      function (e) { TandemPageCounter.onError(String((e && e.stack) || e)); }
    );
  };
})();
