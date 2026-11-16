// SHARX reader: readable text of the article page as it is rendered (Readability; falls back to the page's paragraphs).
// Evaluated inside the article page by the Android host; `Readability` comes from Readability.js in the same scope.
window.__sharxExtract = function () {
  var BLOCK = /captcha|verify (that )?you are (a )?human|just a moment|access denied|access to this page has been denied|security checkpoint|enable javascript and cookies|are you a robot|unusual traffic|checking your browser|attention required|please verify/i;
  // bot checks and captchas (Cloudflare, DataDome, PerimeterX, ...): the page is a check, not the article
  var CHECK = '#challenge-form,#challenge-running,#cf-challenge-running,.cf-browser-verification,[id^="cf-chl"],iframe[src*="captcha"],#px-captcha,.g-recaptcha,.h-captcha';
  var out = { url: location.href, title: document.title || '', text: '', site: '', byline: '', blocked: false };
  try {
    var body = document.body ? (document.body.innerText || '') : '';
    var head = (document.title || '') + ' ' + body.slice(0, 800);
    var article = null;
    try { article = new Readability(document.cloneNode(true), { charThreshold: 300 }).parse(); } catch (e) { article = null; }
    if (article && article.textContent) {
      out.text = article.textContent;
      out.title = article.title || out.title;
      out.site = article.siteName || '';
      out.byline = article.byline || '';
    }
    if (out.text.replace(/\s+/g, ' ').length < 400) {
      var root = document.querySelector('[itemprop="articleBody"]') || document.querySelector('article') || document.querySelector('main') || document.body;
      var ps = root ? root.querySelectorAll('p') : [];
      var parts = [];
      for (var i = 0; i < ps.length; i++) {
        var t = (ps[i].innerText || ps[i].textContent || '').replace(/\s+/g, ' ').trim();
        if (t.length >= 40) parts.push(t);
      }
      var joined = parts.join('\n\n');
      if (joined.length > out.text.length) out.text = joined;
    }
    out.text = out.text.replace(/[ \t ]+/g, ' ').replace(/\s*\n\s*\n\s*/g, '\n\n').replace(/\n /g, '\n').trim().slice(0, 60000);
    out.blocked = (BLOCK.test(head) && out.text.length < 1500) || (!!document.querySelector(CHECK) && out.text.length < 1500);
  } catch (e) {
    out.error = String((e && e.message) || e);
  }
  return out;
};
