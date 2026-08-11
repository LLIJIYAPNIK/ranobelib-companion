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
(() => {
  const SETTINGS_KEY = "readerSettings";
  const PROGRESS_KEY_PREFIX = "tapToReadProgress:";

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

  let revealedCount = 0;
  let hint = null;

  function reveal(count) {
    for (let i = revealedCount; i < count; i++) {
      wraps[i].classList.remove("reader-content__paragraph--hidden");
      stampTime(wraps[i]);
    }
    revealedCount = count;

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

  content.classList.add("reader-content--tap-to-read");
  reveal(loadRevealedCount());

  content.addEventListener("click", () => {
    if (revealedCount >= wraps.length) return;
    const next = revealedCount + 1;
    localStorage.setItem(progressKey, String(next));
    reveal(next);
  });
})();
