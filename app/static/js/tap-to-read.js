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
(() => {
  const SETTINGS_KEY = "readerSettings";
  const PROGRESS_KEY_PREFIX = "tapToReadProgress:";

  function isEnabled() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}").tapToRead === true;
    } catch {
      return false;
    }
  }

  if (!isEnabled()) return;

  const content = document.querySelector('[data-role="chapter"]');
  if (!content) return;

  const paragraphs = [...content.children];
  if (paragraphs.length === 0) return;

  // Keyed by the full path+query, not just the chapter's slug - a different branch_id
  // (PR 7) can mean genuinely different content at the same volume/number, and shouldn't
  // share reveal progress with another translation.
  const progressKey = `${PROGRESS_KEY_PREFIX}${location.pathname}${location.search}`;

  function loadRevealedCount() {
    const stored = Number(localStorage.getItem(progressKey));
    if (!Number.isInteger(stored) || stored < 1) return 1;
    return Math.min(stored, paragraphs.length);
  }

  let revealedCount = loadRevealedCount();
  let hint = null;

  function render() {
    paragraphs.forEach((el, index) => {
      el.classList.toggle("reader-content__paragraph--hidden", index >= revealedCount);
    });

    if (revealedCount >= paragraphs.length) {
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
  render();

  content.addEventListener("click", () => {
    if (revealedCount >= paragraphs.length) return;
    revealedCount += 1;
    localStorage.setItem(progressKey, String(revealedCount));
    render();
  });
})();
