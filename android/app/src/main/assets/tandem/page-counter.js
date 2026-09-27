/*
 * Tandem page counter (issue #730). Runs in PageCounterWebView's shell
 * document, which already carries Readium's <html style> (the reader's
 * settings) and the head nodes Readium injected into the live page, so no
 * iframe is needed: each resource's body is swapped into this one document and
 * measured.
 *
 * Changing how this lays out or counts? Bump PageCountCache.COUNTER_VERSION,
 * or devices keep serving counts the old logic made.
 *
 * This is the fast method verified by the 2026-09-26 spike: it matched a slow
 * per-iframe count on all 120 resources of the test book and the live reader's
 * own counts. Layout is ~2 ms per resource; the cost is I/O, so every resource
 * is fetched up front and consumed in order. Book stylesheets stay loaded by
 * key and are disabled when a resource does not use them, instead of being
 * reloaded.
 *
 * Per resource it also redoes what Readium's ReadiumCss.injectHtml does to
 * that resource's text: ReadiumCSS-default.css only when the resource has no
 * styles of its own, the language fallback (injectLang), and the forced dir
 * (injectDir).
 *
 * Pages per resource: Math.round(scrollingElement.scrollWidth / innerWidth),
 * at least 1 (ReadiumCSS paginates with CSS columns one viewport wide).
 * Characters: the body's text with whitespace collapsed, as the web counts it.
 *
 * Results go back through the TandemPageCounter bridge: onProgress(n) after
 * each resource (the Kotlin side's idle timeout), then onDone(JSON of
 * {counts, chars}) or onError(message).
 */
(function () {
  'use strict';

  var BASE = 'https://readium_package/';
  var XML_NS = 'http://www.w3.org/XML/1998/namespace';
  var WAIT_CAP_MS = 5000;
  var parser = new DOMParser();
  var loaded = new Map(); // book stylesheet key -> node in this document
  var appliedHtmlAttrs = []; // names copied onto <html> for the previous resource

  // Resolves when p settles or after ms, whichever is first. Only a guard
  // against a load event that never fires; the Kotlin side has its own timeout.
  function capped(p, ms) {
    return Promise.race([
      Promise.resolve(p).catch(function () {}),
      new Promise(function (r) { setTimeout(r, ms); }),
    ]);
  }

  function whenLoaded(link) {
    return capped(new Promise(function (r) { link.onload = link.onerror = r; }), WAIT_CAP_MS);
  }

  // Readium's ReadiumCss.hasStyles, on the resource's raw text.
  function hasStyles(text) {
    return /<link/i.test(text) || / style=/i.test(text) || /<style[\s\S]*?>/i.test(text);
  }

  // Mirrors PageCounterRequests.resolveLang (tested there). html/body are
  // {lang, xmlLang}, null meaning the attribute is absent.
  function resolveLang(xhtml, html, body, pubLang) {
    var h = { lang: html.lang, xmlLang: html.xmlLang };
    var b = { lang: body.lang, xmlLang: body.xmlLang };
    if (pubLang != null && h.lang == null && h.xmlLang == null) {
      if (b.lang != null || b.xmlLang != null) {
        h.xmlLang = b.xmlLang || b.lang || pubLang;
      } else {
        h.xmlLang = pubLang;
        b.xmlLang = pubLang;
      }
    }
    function effective(a) { return xhtml ? (a.xmlLang != null ? a.xmlLang : a.lang) : a.lang; }
    return { html: effective(h), body: effective(b) };
  }

  // The raw element's language attributes. In an XML document xml:lang lives
  // in the XML namespace; in an HTML one it is a plain attribute of that name.
  function langAttrs(el, xhtml) {
    return {
      lang: el.getAttribute('lang'),
      xmlLang: xhtml ? el.getAttributeNS(XML_NS, 'lang') : el.getAttribute('xml:lang'),
    };
  }

  function isXhtml(contentType, path) {
    if (contentType) return /xml/i.test(contentType);
    return /\.xht(ml)?$/i.test(path);
  }

  function parse(text, xhtml) {
    if (xhtml) {
      var d = parser.parseFromString(text, 'application/xhtml+xml');
      if (!d.getElementsByTagName('parsererror').length && d.body) return { doc: d, xhtml: true };
    }
    return { doc: parser.parseFromString(text, 'text/html'), xhtml: false };
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

  // Attributes handled separately: the reader settings, namespaces, language
  // (resolveLang) and, when Readium forces it, dir.
  function copied(name, forcedDir) {
    if (name === 'style' || /^xmlns(:|$)/.test(name)) return false;
    if (name === 'lang' || name === 'xml:lang') return false;
    return !(forcedDir && name === 'dir');
  }

  function applyHtmlAttributes(srcHtml, forcedDir) {
    var html = document.documentElement;
    appliedHtmlAttrs.forEach(function (name) { html.removeAttribute(name); });
    appliedHtmlAttrs = [];
    Array.prototype.slice.call(srcHtml.attributes).forEach(function (a) {
      if (a.name === 'style' || !copied(a.name, forcedDir)) return;
      setAttr(html, a.name, a.value);
      appliedHtmlAttrs.push(a.name);
    });
  }

  function applyBodyAttributes(srcBody, forcedDir) {
    var body = document.body;
    Array.prototype.slice.call(body.attributes).forEach(function (a) { body.removeAttribute(a.name); });
    Array.prototype.slice.call(srcBody.attributes).forEach(function (a) {
      if (a.name === 'style' || copied(a.name, forcedDir)) setAttr(body, a.name, a.value);
    });
  }

  // Readium puts the book's own head styles between its injected head
  // (before.css and friends) and ReadiumCSS-after.css.
  function afterCss() {
    return document.head.querySelector('link[href*="ReadiumCSS-after.css"]');
  }

  function inDocumentOrder(nodes) {
    for (var i = 1; i < nodes.length; i++) {
      if (!(nodes[i - 1].compareDocumentPosition(nodes[i]) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
    }
    return true;
  }

  async function applyStylesheets(src, url) {
    var anchor = afterCss();
    var want = [];
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
      if (want.indexOf(key) >= 0) continue;
      want.push(key);
      if (!loaded.has(key)) {
        var c = document.importNode(n, true);
        if (href) c.setAttribute('href', key);
        document.head.insertBefore(c, anchor);
        loaded.set(key, c);
        if (href) await whenLoaded(c);
      }
    }
    var wanted = want.map(function (k) { return loaded.get(k); });
    if (!inDocumentOrder(wanted)) {
      // Rare: two resources use shared sheets in different orders. Re-add
      // them in this resource's order; links as fresh clones, whose load event
      // is certain to fire.
      for (var j = 0; j < want.length; j++) {
        var old = loaded.get(want[j]);
        if (old.localName.toLowerCase() === 'link') {
          var fresh = old.cloneNode(true);
          document.head.insertBefore(fresh, anchor);
          old.remove();
          loaded.set(want[j], fresh);
          await whenLoaded(fresh);
        } else {
          document.head.insertBefore(old, anchor);
        }
      }
    }
    loaded.forEach(function (node, key) { node.disabled = want.indexOf(key) < 0; });
  }

  async function measure(parsed, text, url, base, options) {
    var src = parsed.doc;
    var forcedDir = options.dir || null;
    base.href = url;

    var defaultCss = document.head.querySelector('link[data-tandem-default-css]');
    if (defaultCss) defaultCss.disabled = hasStyles(text);
    await applyStylesheets(src, url);

    var html = document.documentElement;
    var srcHtml = src.documentElement;
    var srcBody = src.body;
    applyHtmlAttributes(srcHtml, forcedDir);

    sanitize(srcBody);
    applyBodyAttributes(srcBody, forcedDir);
    document.body.replaceChildren.apply(
      document.body,
      Array.prototype.slice.call(srcBody.childNodes).map(function (n) { return document.importNode(n, true); })
    );

    var lang = resolveLang(parsed.xhtml, langAttrs(srcHtml, parsed.xhtml), langAttrs(srcBody, parsed.xhtml), options.pubLang);
    setAttr(html, 'lang', lang.html);
    setAttr(document.body, 'lang', lang.body);
    if (forcedDir) {
      html.setAttribute('dir', forcedDir);
      document.body.setAttribute('dir', forcedDir);
    }

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

  async function run(hrefs, options) {
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
      } else {
        var parsed = parse(got.text, isXhtml(got.type, urls[i].pathname));
        var m = await measure(parsed, got.text, urls[i].href, base, options);
        counts.push(m.pages);
        chars.push(m.chars);
      }
      TandemPageCounter.onProgress(i + 1);
    }
    return { counts: counts, chars: chars };
  }

  window.tandemCountPages = function (hrefs, options) {
    run(hrefs, options || {}).then(
      function (r) { TandemPageCounter.onDone(JSON.stringify(r)); },
      function (e) { TandemPageCounter.onError(String((e && e.stack) || e)); }
    );
  };

  // Pure helpers, exposed only so they can be checked outside a WebView.
  window.tandemPageCounterPure = { hasStyles: hasStyles, resolveLang: resolveLang };
})();
