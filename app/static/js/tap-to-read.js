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
// appears immediately - same "don't break on non-<p> content" rule as everything here.
//
// PR 79: readerSettings.revealTempo (default "instant", i.e. everything above unchanged)
// stretches a tap-triggered reveal over roughly how long the paragraph would actually take
// to read at readerSettings.readingSpeedWpm (PR 77/78 - falls back to DEFAULT_WPM if
// neither was ever set), instead of showing it all at once. Five tempo mechanics, all
// timed the same way (see computeDurationMs): word-by-word, a WPM-paced version of the
// existing typewriter effect, line-by-line, a running highlight over already-visible
// text, and a per-word blur-to-focus dissolve. Only applies to an actual tap - the initial
// reveal(loadRevealedCount()) restoring saved progress on page load skips it, same as
// PR 76's autoscroll skips that call: animating a whole backlog of paragraphs right after
// load would be a worse experience, not a better one. When active, it fully replaces
// paragraphAnimation/typewriter for that reveal rather than layering on top of it - both
// would otherwise fight over the same text nodes (typewriter-speed) or just be visually
// redundant (the others).
//
// PR 129: separately from all of the above, a *single* scroll restore runs once, right
// after that initial reveal(loadRevealedCount()) finishes - not the per-paragraph
// scroll/tempo machinery PR 76/79 skip for it, just a one-time jump straight to whatever
// was last revealed, so reopening an already-started chapter doesn't strand the visitor
// at the top of a long backlog of already-read paragraphs.
(() => {
  const SETTINGS_KEY = "readerSettings";
  const PROGRESS_KEY_PREFIX = "tapToReadProgress:";
  const CSS_ANIMATIONS = new Set(["slide-up", "slide-left", "fade", "blur-focus"]);
  const TEMPO_OPTIONS = new Set([
    "word-by-word",
    "typewriter-speed",
    "line-by-line",
    "highlight-sweep",
    "word-dissolve",
  ]);
  // Average adult silent-reading pace - used only when revealTempo is on but the visitor
  // never took the PR 77 test or filled in PR 78's manual field, so tempo still does
  // something reasonable instead of needing a measured speed as a hard prerequisite.
  const DEFAULT_WPM = 200;
  // Bounds on a single paragraph's stretched-reveal duration, regardless of what its own
  // word count and readingSpeedWpm work out to - the roadmap's own concern: a very long
  // paragraph at a slow speed shouldn't turn into an uncomfortably long pause, and a very
  // short one at a fast speed shouldn't flicker by unreadably fast.
  const MIN_TEMPO_DURATION_MS = 400;
  const MAX_TEMPO_DURATION_MS = 15_000;

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
  const revealTempo = TEMPO_OPTIONS.has(settings.revealTempo) ? settings.revealTempo : "instant";
  const readingSpeedWpm = Number(settings.readingSpeedWpm) > 0 ? Number(settings.readingSpeedWpm) : DEFAULT_WPM;

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

  // PR 83: stored as {revealed, total} (not a bare number) so the title page's table of
  // contents (toc-tap-progress.js) can turn it into a percentage/checkmark without
  // re-fetching or re-parsing the chapter itself - total is exactly wraps.length, which
  // only this page ever computes. Still accepts a bare number for a progress entry saved
  // before this change existed, just without a total to show a percentage against.
  function readStoredProgress() {
    const raw = localStorage.getItem(progressKey);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && Number.isInteger(parsed.revealed)) return parsed;
    } catch {
      // not JSON - fall through to the legacy bare-number format below
    }
    const legacy = Number(raw);
    return Number.isInteger(legacy) && legacy >= 1 ? { revealed: legacy, total: null } : null;
  }

  function saveProgress(revealed) {
    localStorage.setItem(progressKey, JSON.stringify({ revealed, total: wraps.length }));
  }

  function loadRevealedCount() {
    const stored = readStoredProgress();
    if (!stored || stored.revealed < 1) return 1;
    return Math.min(stored.revealed, wraps.length);
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

  // `delay` is in ms/character - PR 65's own fixed-budget call site and PR 79's
  // WPM-derived one both just compute it differently and share everything after that.
  // `done` (PR 79 only) fires once every character has been typed back in.
  function startTypewriter(captured, delay, done) {
    const totalChars = captured.reduce((sum, { text }) => sum + text.length, 0);
    if (totalChars === 0) {
      done?.(); // nothing to type (e.g. a bare <img>) - already visible
      return;
    }

    let nodeIndex = 0;
    let charIndex = 0;

    const timer = setInterval(() => {
      if (nodeIndex >= captured.length) {
        clearInterval(timer);
        done?.();
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

  // A fixed-ish overall duration regardless of paragraph length feels more like a reveal
  // animation and less like actually waiting for someone to type a long one: short
  // paragraphs get a leisurely per-character delay, long ones a brisker one. Only used by
  // paragraphAnimation === "typewriter" - PR 79's typewriter-speed tempo computes its own
  // delay from readingSpeedWpm instead (see runTempo below).
  function fixedTypewriterDelay(totalChars) {
    return Math.min(20, Math.max(4, 900 / totalChars));
  }

  // PR 79: word-splitting shared by every tempo mode that reveals/highlights word by
  // word. Walks the same text nodes prepareTypewriter() would (so nested tags like
  // <em>/<a> keep their own words separate rather than getting merged), and replaces each
  // one with a run of <span class="reader-content__word"> plus the original whitespace
  // between them, so spacing survives untouched.
  function wrapWordsInSpans(wrap) {
    const spans = [];
    for (const node of textNodesOf(wrap)) {
      if (!node.nodeValue.trim()) continue; // pure whitespace between tags - leave as is
      const fragment = document.createDocumentFragment();
      for (const part of node.nodeValue.split(/(\s+)/)) {
        if (part === "") continue;
        if (/^\s+$/.test(part)) {
          fragment.append(part);
          continue;
        }
        const span = document.createElement("span");
        span.className = "reader-content__word";
        span.textContent = part;
        fragment.append(span);
        spans.push(span);
      }
      node.replaceWith(fragment);
    }
    return spans;
  }

  function wordCount(wrap) {
    const text = wrap.textContent.trim();
    return text ? text.split(/\s+/).length : 0;
  }

  function computeTempoDurationMs(wrap) {
    const words = wordCount(wrap);
    if (words === 0) return 0; // nothing to time (e.g. a bare <img>)
    const raw = (words / readingSpeedWpm) * 60_000;
    return Math.min(MAX_TEMPO_DURATION_MS, Math.max(MIN_TEMPO_DURATION_MS, raw));
  }

  // Reveals `spans` one at a time, `durationMs` spread evenly across all of them -
  // shared by word-by-word and word-dissolve, which only differ in the CSS class that
  // controls how a still-pending word looks (see app/static/css/app.css).
  function revealSpansSequentially(spans, durationMs, pendingClass, done) {
    if (spans.length === 0) {
      done();
      return;
    }
    const interval = durationMs / spans.length;
    let i = 0;
    const timer = setInterval(() => {
      spans[i].classList.remove(pendingClass);
      i += 1;
      if (i >= spans.length) {
        clearInterval(timer);
        done();
      }
    }, interval);
  }

  function runWordByWord(wrap, durationMs, done) {
    const spans = wrapWordsInSpans(wrap);
    spans.forEach((span) => span.classList.add("reader-content__word--pending"));
    revealSpansSequentially(spans, durationMs, "reader-content__word--pending", done);
  }

  function runWordDissolve(wrap, durationMs, done) {
    const spans = wrapWordsInSpans(wrap);
    spans.forEach((span) => span.classList.add("reader-content__word--dissolved"));
    revealSpansSequentially(spans, durationMs, "reader-content__word--dissolved", done);
  }

  // Groups spans by their rendered top offset (words on the same visual line share it)
  // instead of assuming a fixed character count per line - text wrapping depends on the
  // reader's own font/width settings (PR 30/34), so only the actual layout can say where
  // one line ends and the next begins. The words stay laid out (just invisible via
  // opacity, not display: none) from the moment they're wrapped, so this measurement
  // reflects their real final position with no extra reflow to wait for.
  function groupSpansByLine(spans) {
    const lines = [];
    let lastTop = null;
    for (const span of spans) {
      const top = span.offsetTop;
      if (lastTop === null || Math.abs(top - lastTop) > 2) {
        lines.push([span]);
        lastTop = top;
      } else {
        lines[lines.length - 1].push(span);
      }
    }
    return lines;
  }

  function runLineByLine(wrap, durationMs, done) {
    const spans = wrapWordsInSpans(wrap);
    if (spans.length === 0) {
      done();
      return;
    }
    spans.forEach((span) => span.classList.add("reader-content__word--pending"));
    const lines = groupSpansByLine(spans);
    const interval = durationMs / lines.length;
    let i = 0;
    const timer = setInterval(() => {
      for (const span of lines[i]) span.classList.remove("reader-content__word--pending");
      i += 1;
      if (i >= lines.length) {
        clearInterval(timer);
        done();
      }
    }, interval);
  }

  // Unlike the other tempo modes, the text itself is fully visible immediately - only a
  // highlight sweeps across it, at reading pace, as a "you should be about here" pace-
  // setter rather than something hiding content from the visitor.
  function runHighlightSweep(wrap, durationMs, done) {
    const spans = wrapWordsInSpans(wrap);
    if (spans.length === 0) {
      done();
      return;
    }
    const interval = durationMs / spans.length;
    let i = 0;
    const timer = setInterval(() => {
      spans[i - 1]?.classList.remove("reader-content__word--highlighted");
      spans[i].classList.add("reader-content__word--highlighted");
      i += 1;
      if (i >= spans.length) {
        clearInterval(timer);
        spans[spans.length - 1].classList.remove("reader-content__word--highlighted");
        done();
      }
    }, interval);
  }

  function runTypewriterSpeed(wrap, durationMs, done) {
    const captured = prepareTypewriter(wrap);
    const totalChars = captured.reduce((sum, { text }) => sum + text.length, 0);
    if (totalChars === 0) {
      done();
      return;
    }
    startTypewriter(captured, durationMs / totalChars, done);
  }

  const TEMPO_RUNNERS = {
    "word-by-word": runWordByWord,
    "typewriter-speed": runTypewriterSpeed,
    "line-by-line": runLineByLine,
    "highlight-sweep": runHighlightSweep,
    "word-dissolve": runWordDissolve,
  };

  let revealedCount = 0;
  let hint = null;

  // Either shows the "tap to continue" hint, or removes it for good once every paragraph
  // in the chapter is revealed - shared tail end of both the instant reveal() below and
  // PR 79's tempo-driven revealNextWithTempo().
  function afterReveal() {
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

      if (pendingTypewriter) {
        const totalChars = pendingTypewriter.reduce((sum, { text }) => sum + text.length, 0);
        startTypewriter(pendingTypewriter, fixedTypewriterDelay(totalChars));
      }
      lastRevealed = wrap;
    }
    revealedCount = count;

    if (scroll && lastRevealed) {
      lastRevealed.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    afterReveal();
  }

  // PR 79: the tap-triggered, tempo-paced counterpart to reveal() above - always exactly
  // one new paragraph (the click handler only ever advances by one), stretched over
  // computeTempoDurationMs() instead of appearing all at once. Falls back to the instant
  // reveal() for a paragraph with nothing to time (e.g. a bare <img>, wordCount() === 0).
  function revealNextWithTempo(count) {
    const wrap = wraps[revealedCount];
    const durationMs = computeTempoDurationMs(wrap);
    if (durationMs === 0) {
      reveal(count, { scroll: true });
      return;
    }

    wrap.classList.remove("reader-content__paragraph--hidden");
    wrap.scrollIntoView({ behavior: "smooth", block: "end" });

    // stampTime() only runs once the tempo reveal is done, not before - every runner
    // walks wrap's own text nodes (to split into words or capture for typewriter-speed),
    // and the timestamp span's own text ("16:45") would otherwise get counted as part of
    // the paragraph and end up revealed/typed right alongside it.
    TEMPO_RUNNERS[revealTempo](wrap, durationMs, () => {
      stampTime(wrap);
      revealedCount = count;
      afterReveal();
    });
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
  const initialRevealedCount = loadRevealedCount();
  reveal(initialRevealedCount);

  // PR 129: same "land where you left off" restore as reader-progress.js's non-tap
  // mode, applied to the freshly-revealed wraps here instead of plain paragraphs.
  // loadRevealedCount() always floors at 1 (the first paragraph reveals by default even
  // with nothing saved), so it alone can't tell "nothing saved yet" apart from "really
  // did save revealed: 1" - re-checking readStoredProgress() directly is what decides
  // whether to scroll at all, so a chapter with no saved progress stays at the natural
  // top-of-page start instead of getting a pointless nudge toward the first paragraph
  // it's already showing. `block: "start"` plus a small upward nudge, not "center"/"end"
  // - reading continues *downward* from here, so the restored paragraph belongs near the
  // top of the viewport with room below it, not centered or flush at the bottom.
  if (readStoredProgress()) {
    wraps[initialRevealedCount - 1].scrollIntoView({ block: "start" });
    window.scrollBy(0, -24);
  }

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
    saveProgress(next);
    if (revealTempo === "instant") {
      reveal(next, { scroll: true });
    } else {
      revealNextWithTempo(next);
    }
  });
})();
