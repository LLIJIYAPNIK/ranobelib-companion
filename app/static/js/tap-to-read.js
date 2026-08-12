// Progressive paragraph-by-tap reading (PR 62): an alternative way to read a chapter -
// instead of the whole thing rendered at once, .reader-content's direct children (not
// just <p> - a sanitized chapter can have a bare <img> or other block sitting right
// alongside them, see chapters made entirely of illustrations) reveal one at a time on
// tap/click, in a growing feed rather than a slideshow - every paragraph shown so far
// stays exactly where it is, the next one is appended below it.
//
// Off by default: readerSettings.tapToRead (PR 63 adds the actual switch on /settings,
// same localStorage key reader-settings.js already owns) has to be explicitly true, so
// until that switch exists nobody can turn this on and regular reading is unaffected.
//
// PR 64: each revealed paragraph gets wrapped in its own background plate, in one of two
// styles picked by readerSettings.paragraphStyle - "chat" (a message-bubble look plus a
// timestamp for when it was revealed) or "plain" (the same plate, no timestamp). The
// timestamp is stamped fresh from Date.now() whenever a paragraph is (re-)shown, never
// persisted - purely decorative, not a real read receipt.
//
// PR 65: readerSettings.paragraphAnimation picks how a paragraph enters. Every option but
// "typewriter" is a CSS @keyframes animation (app/static/css/app.css) added as a class at
// the same moment the wrap is un-hidden - browsers replay a CSS animation automatically
// whenever `display` goes from `none` to visible, no JS re-triggering needed.
// "typewriter" instead reveals the wrap's own text nodes one character at a time (walking
// the DOM so nested tags like <em>/<a> stay intact), themed to go with PR 64's chat
// style; a paragraph with no text at all (a bare <img>) has nothing to type, so it just
// appears immediately - same "don't break on non-<p> content" rule as everything else
// here.
(() => {
  const SETTINGS_KEY = "readerSettings";
  const PROGRESS_KEY_PREFIX = "tapToReadProgress:";
  const CSS_ANIMATIONS = new Set(["slide-up", "slide-left", "fade", "blur-focus"]);

  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    } catch {
      return {};
    }
  }

  const settings = loadSettings();
  if (settings.tapToRead !== true) return;

  const paragraphStyle = settings.paragraphStyle === "plain" ? "plain" : "chat";
  const paragraphAnimation =
    CSS_ANIMATIONS.has(settings.paragraphAnimation) || settings.paragraphAnimation === "typewriter"
      ? settings.paragraphAnimation
      : "none";

  const content = document.querySelector('[data-role="chapter"]');
  if (!content) return;

  const originalParagraphs = [...content.children];
  if (originalParagraphs.length === 0) return;

  // Wrap every paragraph-equivalent unit in its own plate - the reveal mechanic's
  // hidden/shown toggle and the background-plate styling both need one element to act
  // on regardless of what's actually inside it (<p>, a bare <img>, ...).
  const wraps = originalParagraphs.map((el) => {
    const wrap = document.createElement("div");
    wrap.className = `reader-content__paragraph-wrap reader-content__paragraph-wrap--${paragraphStyle} reader-content__paragraph--hidden`;
    el.replaceWith(wrap);
    wrap.appendChild(el);
    return wrap;
  });

  // Keyed by the full path+query, not just the chapter's slug - a different branch_id
  // (PR 7) can mean genuinely different content at the same volume/number, and shouldn't
  // share reveal progress with another translation.
  const progressKey = `${PROGRESS_KEY_PREFIX}${location.pathname}${location.search}`;

  function loadRevealedCount() {
    const stored = Number(localStorage.getItem(progressKey));
    if (!Number.isInteger(stored) || stored < 1) return 1;
    return Math.min(stored, wraps.length);
  }

  function stampTime(wrap) {
    if (paragraphStyle !== "chat") return;
    const time = document.createElement("span");
    time.className = "reader-content__paragraph-time";
    time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    wrap.appendChild(time);
  }

  function textNodesOf(wrap) {
    const walker = document.createTreeWalker(wrap, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let node;
    while ((node = walker.nextNode())) nodes.push(node);
    return nodes;
  }

  // Clears the wrap's text (capturing what to type back in) *before* it's un-hidden, so
  // there's never a flash of the full paragraph ahead of the reveal.
  function prepareTypewriter(wrap) {
    const captured = textNodesOf(wrap).map((node) => ({ node, text: node.nodeValue }));
    for (const { node } of captured) node.nodeValue = "";
    return captured;
  }

  function startTypewriter(captured) {
    const totalChars = captured.reduce((sum, { text }) => sum + text.length, 0);
    if (totalChars === 0) return; // nothing to type (e.g. a bare <img>) - already visible

    // A fixed-ish overall duration regardless of paragraph length feels more like a
    // reveal animation and less like actually waiting for someone to type a long one:
    // short paragraphs get a leisurely per-character delay, long ones a brisker one.
    const delay = Math.min(20, Math.max(4, 900 / totalChars));
    let nodeIndex = 0;
    let charIndex = 0;

    const timer = setInterval(() => {
      if (nodeIndex >= captured.length) {
        clearInterval(timer);
        return;
      }
      const current = captured[nodeIndex];
      current.node.nodeValue += current.text[charIndex];
      charIndex += 1;
      if (charIndex >= current.text.length) {
        nodeIndex += 1;
        charIndex = 0;
      }
    }, delay);
  }

  let revealedCount = 0;
  let hint = null;

  // `scroll` is only true for a tap-triggered reveal (see the click handler below) - the
  // initial reveal(loadRevealedCount()) on page load restores possibly many paragraphs
  // at once from saved progress, and jumping the page around right after load there would
  // fight with wherever the browser/reader itself puts the initial scroll position, not
  // help it.
  function reveal(count, { scroll = false } = {}) {
    let lastRevealed = null;
    for (let i = revealedCount; i < count; i++) {
      const wrap = wraps[i];
      const pendingTypewriter =
        paragraphAnimation === "typewriter" ? prepareTypewriter(wrap) : null;

      if (CSS_ANIMATIONS.has(paragraphAnimation)) {
        wrap.classList.add(`reader-content__paragraph-wrap--anim-${paragraphAnimation}`);
      }
      wrap.classList.remove("reader-content__paragraph--hidden");
      stampTime(wrap);

      if (pendingTypewriter) startTypewriter(pendingTypewriter);
      lastRevealed = wrap;
    }
    revealedCount = count;

    if (scroll && lastRevealed) {
      lastRevealed.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    if (revealedCount >= wraps.length) {
      hint?.remove();
      return;
    }
    if (!hint) {
      hint = document.createElement("p");
      hint.className = "reader-content__tap-hint";
      hint.textContent = "Тапните, чтобы читать дальше";
    }
    content.appendChild(hint); // keep it last, after whatever was just revealed
  }

  // PR 75: what a tap does once every paragraph in this chapter is already revealed -
  // move on to the next chapter, same as clicking the ordinary "Следующая глава ›" link
  // (_chapter_nav.html tags it data-role="next-chapter-link" for exactly this), or, if
  // this was the title's last chapter (no such link anywhere on the page), back to the
  // title page with a "Тайтл прочитан" notice.
  function goPastLastParagraph() {
    const nextLink = document.querySelector('[data-role="next-chapter-link"]');
    if (nextLink) {
      location.href = nextLink.href;
      return;
    }
    const slugUrl = content.dataset.slugUrl;
    location.href = slugUrl ? `/titles/${slugUrl}?finished=1` : "/";
  }

  content.classList.add("reader-content--tap-to-read");
  reveal(loadRevealedCount());

  // PR 73: the tap zone is the whole reading area (<main class="content">, which chapter.html
  // wraps .reader-content in), not just .reader-content itself - that element is only ever
  // as tall as the paragraphs revealed so far and only as wide as --reader-width, so a tap
  // beside the text column or below the last revealed paragraph (before the page has scrolled
  // enough to fill the viewport) used to miss it entirely. Falls back to .reader-content if
  // the expected wrapper isn't there for some reason, rather than not working at all.
  const tapZone = content.closest("main.content") || content;

  tapZone.addEventListener("click", (event) => {
    // Links and buttons - the back-to-ToC link, prev/next chapter nav, per-chapter export
    // links - keep their own behavior; a tap on them shouldn't also advance the reveal.
    // Images too (PR 74): image-lightbox.js opens its fullscreen viewer on the same tap,
    // no longer disabling itself just because tap-to-read is on - without this exclusion
    // that same tap would also silently reveal the next paragraph behind the lightbox.
    if (event.target.closest("a, button, img")) return;
    if (revealedCount >= wraps.length) {
      goPastLastParagraph();
      return;
    }
    const next = revealedCount + 1;
    localStorage.setItem(progressKey, String(next));
    reveal(next, { scroll: true });
  });
})();
