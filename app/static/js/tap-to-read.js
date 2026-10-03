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
//
// PR 254 (Aurora Ink "Tap Focus"): the tap is read by where it lands, across the whole
// screen outside open layers - left 28% goes back one paragraph (undoing a stray tap),
// the middle 24% toggles the reader HUD (reader-hud.js), the right 48% reveals the next
// one. Space/↓/→ and ↑/← do the same from the keyboard. After the last paragraph a tap
// only highlights the end-of-chapter card - switching chapters is its «Следующая глава»
// button, never a stray tap (this used to jump straight on, PR 75). The newest paragraph
// is the active one (soft tint + accent marker in the default "book" style, with «⋯»
// opening paragraph-menu.js) and settles at about 45% of the screen height; everything
// above it reads as already-read. "chat" and "plain" (PR 64) stay available. A one-time
// onboarding overlay explains the zones.
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

  const PARAGRAPH_STYLES = new Set(["book", "plain", "chat"]);
  const paragraphStyle = PARAGRAPH_STYLES.has(settings.paragraphStyle)
    ? settings.paragraphStyle
    : "book";
  const socialEnabled = settings.showParagraphSocial !== false;
  function animationFrom(s) {
    return CSS_ANIMATIONS.has(s.paragraphAnimation) || s.paragraphAnimation === "typewriter"
      ? s.paragraphAnimation
      : "none";
  }
  function tempoFrom(s) {
    return TEMPO_OPTIONS.has(s.revealTempo) ? s.revealTempo : "instant";
  }
  // PR 255: the Aa panel's «Появление абзаца» applies to the next reveal, no reload.
  let paragraphAnimation = animationFrom(settings);
  let revealTempo = tempoFrom(settings);
  document.addEventListener("reader-settings:change", (event) => {
    const next = event.detail?.settings;
    if (!next) return;
    paragraphAnimation = animationFrom(next);
    revealTempo = tempoFrom(next);
  });
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
    // PR 288: reading-progress-tick.js sends it on to the server (throttled).
    emit("reader:position", { revealed, total: wraps.length });
  }

  function loadRevealedCount() {
    const stored = readStoredProgress();
    if (!stored || stored.revealed < 1) return 1;
    return Math.min(stored.revealed, wraps.length);
  }

  // Wave 35 (PR 287): the server's saved position for this chapter (data-saved-paragraph,
  // possibly written from another device) vs this device's own entry - whichever got
  // further wins, the same rule reader-progress.js applies between the two modes. A
  // server win is copied into localStorage, so everything below - and the table of
  // contents' toc-tap-progress.js - keeps reading the one local entry.
  function adoptServerProgress() {
    const serverRevealed = Number(content.dataset.savedParagraph);
    if (!Number.isInteger(serverRevealed) || serverRevealed < 1) return;
    const stored = readStoredProgress();
    if (stored && stored.revealed >= serverRevealed) return;
    saveProgress(Math.min(serverRevealed, wraps.length));
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

  // PR 239/270: chapter.html's footnotes block is collapsed (a closed <details>) by
  // default - its footnote paragraphs aren't actually rendered, but .textContent still
  // includes them, so wordCount() above would otherwise count text nobody can currently
  // see. Without this, a chapter with many footnotes could turn this one wrap's tempo-
  // paced reveal into a multi-second wait over words that are invisible either way.
  function isFootnotesWrap(wrap) {
    return wrap.querySelector('[data-role="reader-footnotes"]') !== null;
  }

  function computeTempoDurationMs(wrap) {
    if (isFootnotesWrap(wrap)) return 0;
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
  const endCard = document.querySelector('[data-role="reader-end-card"]');
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const ZONE_BACK = 0.28;
  const ZONE_HUD = 0.52;
  const MOVE_TOLERANCE_PX = 10;
  const LONG_PRESS_MS = 500;
  const END_FLASH_MS = 160;

  // Keeps the active paragraph around 45% of the viewport height (a tall one starts near
  // the top instead, so its first lines stay on screen).
  function settle(wrap, { instant = false } = {}) {
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const vh = window.innerHeight;
    const targetTop = Math.max(vh * 0.12, vh * 0.45 - rect.height / 2);
    const behavior = instant || reducedMotion.matches ? "auto" : "smooth";
    window.scrollBy({ top: rect.top - targetTop, behavior });
  }

  function emit(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  }

  // «⋯» on the active paragraph opens the same menu as a right-click / long press:
  // paragraph-menu.js listens for "contextmenu" on .reader-content and works out the
  // paragraph from the event target, so a synthetic one from inside the wrap is enough.
  let moreButton = null;
  function paragraphMoreButton() {
    if (moreButton) return moreButton;
    moreButton = document.createElement("button");
    moreButton.type = "button";
    moreButton.className = "reader-paragraph-more";
    moreButton.setAttribute("aria-label", "Действия с абзацем");
    moreButton.innerHTML =
      '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
    moreButton.addEventListener("click", (event) => {
      event.stopPropagation(); // paragraph-menu.js closes on any document click
      const rect = moreButton.getBoundingClientRect();
      moreButton.dispatchEvent(
        new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: rect.left,
          clientY: rect.bottom,
        })
      );
    });
    return moreButton;
  }

  // Active = the newest revealed paragraph; everything before it is read. The end card
  // only shows once the last paragraph is out.
  function afterReveal() {
    wraps.forEach((wrap, i) => {
      wrap.classList.toggle("reader-content__paragraph-wrap--active", i === revealedCount - 1);
      wrap.classList.toggle("reader-content__paragraph-wrap--read", i < revealedCount - 1);
    });
    const active = wraps[revealedCount - 1];
    if (socialEnabled && active && paragraphStyle === "book") {
      active.appendChild(paragraphMoreButton());
    }
    if (endCard) endCard.hidden = revealedCount < wraps.length;
    emit("reader:progress", { revealed: revealedCount, total: wraps.length });
  }

  // PR 289: putting a saved position back on load. Every one of these paragraphs was
  // already read last time, so they return as a finished stretch: no appear animation,
  // typewriter, tempo or chat timestamp on any of them, the active one included - only
  // the read/active marking. Running them through reveal() played its per-paragraph
  // animation N times over on every open of a half-read chapter. A real tap (next())
  // still goes through reveal()/revealNextWithTempo() as before.
  function restore(count) {
    for (let i = revealedCount; i < count; i++) {
      wraps[i].classList.remove("reader-content__paragraph--hidden");
    }
    revealedCount = count;
    afterReveal();
  }

  // `scroll` is only true for a tap-triggered reveal.
  function reveal(count, { scroll = false } = {}) {
    let lastRevealed = null;
    for (let i = revealedCount; i < count; i++) {
      const wrap = wraps[i];
      // PR 239: don't type out invisible, collapsed footnote text.
      const pendingTypewriter =
        paragraphAnimation === "typewriter" && !isFootnotesWrap(wrap)
          ? prepareTypewriter(wrap)
          : null;

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
    afterReveal();
    if (scroll && lastRevealed) settle(lastRevealed);
  }

  // PR 79: the tempo-paced counterpart to reveal() - always exactly one new paragraph,
  // stretched over computeTempoDurationMs(). Falls back to reveal() when there's nothing
  // to time (e.g. a bare <img>) or motion is reduced.
  function revealNextWithTempo(count) {
    const wrap = wraps[revealedCount];
    const durationMs = reducedMotion.matches ? 0 : computeTempoDurationMs(wrap);
    if (durationMs === 0) {
      reveal(count, { scroll: true });
      return;
    }

    wrap.classList.remove("reader-content__paragraph--hidden");
    revealedCount = count;
    afterReveal();
    settle(wrap);

    // stampTime() only once the tempo reveal is done - every runner walks the wrap's own
    // text nodes, and the timestamp's text would otherwise be revealed along with it.
    TEMPO_RUNNERS[revealTempo](wrap, durationMs, () => stampTime(wrap));
  }

  function next() {
    if (revealedCount >= wraps.length) {
      flashEndCard();
      return;
    }
    const count = revealedCount + 1;
    saveProgress(count);
    emit("reader:reveal");
    if (revealTempo === "instant") reveal(count, { scroll: true });
    else revealNextWithTempo(count);
  }

  // Left zone: take the newest paragraph back (a stray tap), never below the first.
  function back() {
    if (revealedCount <= 1) return;
    const wrap = wraps[revealedCount - 1];
    wrap.classList.add("reader-content__paragraph--hidden");
    wrap.querySelectorAll(".reader-content__paragraph-time").forEach((el) => el.remove());
    revealedCount -= 1;
    saveProgress(revealedCount);
    afterReveal();
    settle(wraps[revealedCount - 1]);
  }

  function flashEndCard() {
    if (!endCard) return;
    endCard.hidden = false;
    endCard.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
    endCard.classList.remove("reader-end--flash");
    void endCard.offsetWidth; // restart the animation on a repeat tap
    endCard.classList.add("reader-end--flash");
    setTimeout(() => endCard.classList.remove("reader-end--flash"), END_FLASH_MS);
  }

  content.classList.add("reader-content--tap-to-read", `reader-content--${paragraphStyle}`);
  adoptServerProgress();
  const initialRevealedCount = loadRevealedCount();
  reveal(initialRevealedCount);

  // PR 129: reopening an already-started chapter lands on the last revealed paragraph.
  if (readStoredProgress()) settle(wraps[initialRevealedCount - 1], { instant: true });

  // --- taps ----------------------------------------------------------------------------
  // Anything interactive, and every layer above the text, keeps its own behavior. PR 74:
  // images open image-lightbox.js. PR 146: the reactions/comments UI (composer textarea,
  // emoji picker, ...) is excluded as whole containers.
  const NOT_A_TAP =
    "a, button, input, textarea, select, label, img, sup, summary, dialog, [contenteditable], " +
    "[role='toolbar'], [role='dialog'], .reader-hud-bottom, .reader-end, .reader-onboarding, " +
    ".paragraph-reactions, .paragraph-comments, .paragraph-reactions-host, .paragraph-menu__panel, " +
    ".image-lightbox";
  const OPEN_LAYER = "dialog[open], .paragraph-menu__panel--open, .image-lightbox--open";

  let press = null;
  document.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button > 0) return;
    press = { x: event.clientX, y: event.clientY, t: Date.now(), target: event.target };
  });

  document.addEventListener("pointerup", (event) => {
    const start = press;
    press = null;
    if (!start || !event.isPrimary) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > MOVE_TOLERANCE_PX) return;
    if (Date.now() - start.t >= LONG_PRESS_MS) return;
    if (start.target.closest(NOT_A_TAP) || event.target.closest(NOT_A_TAP)) return;
    if (String(window.getSelection() || "").trim()) return;
    if (document.querySelector(OPEN_LAYER)) return;

    const x = event.clientX / window.innerWidth;
    if (x < ZONE_BACK) back();
    else if (x < ZONE_HUD) emit("reader:toggle-hud");
    else next();
  });

  // --- keyboard ------------------------------------------------------------------------
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest("input, textarea, select, button, a, [contenteditable]")) return;
    if (document.querySelector(OPEN_LAYER)) return;
    if (event.key === " " || event.key === "ArrowDown" || event.key === "ArrowRight") {
      event.preventDefault();
      next();
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      event.preventDefault();
      back();
    }
  });

  // --- onboarding (once per device) -----------------------------------------------------
  const ONBOARDING_KEY = "readerTapFocusOnboarded";
  const onboarding = document.querySelector('[data-role="reader-onboarding"]');
  let onboarded = false;
  try {
    onboarded = localStorage.getItem(ONBOARDING_KEY) === "1";
  } catch {
    onboarded = true; // storage blocked - don't show it on every chapter
  }
  if (onboarding && !onboarded) {
    const ok = onboarding.querySelector('[data-role="reader-onboarding-ok"]');
    const dismiss = onboarding.querySelector('[data-role="reader-onboarding-dismiss"]');
    onboarding.hidden = false;
    ok?.focus();
    const finish = () => {
      if (dismiss?.checked) {
        try {
          localStorage.setItem(ONBOARDING_KEY, "1");
        } catch {
          // storage blocked - it just shows again next time
        }
      }
      onboarding.hidden = true;
    };
    ok?.addEventListener("click", finish);
    onboarding.addEventListener("keydown", (event) => {
      if (event.key === "Escape") finish();
    });
  }
})();
