// PR 84: reading progress for the ordinary (non-tap) reading mode, mirroring what
// tap-to-read.js (PR 62) already tracks for its own mode - so the title page's table of
// contents (toc-tap-progress.js, PR 83) can show the same bar/checkmark regardless of
// which mode a chapter was actually read in. There is no discrete "revealed count" like
// tap mode has here (every paragraph is already on the page); instead an
// IntersectionObserver watches .reader-content's own paragraphs and treats the furthest
// one that has ever scrolled into view as "revealed".
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

  // tap-to-read.js is the one tracking progress when that mode is on - two observers
  // writing to the same entry would be redundant at best, racy at worst.
  if (loadSettings().tapToRead === true) return;

  const content = document.querySelector('[data-role="chapter"]');
  if (!content) return;

  const paragraphs = [...content.children];
  if (paragraphs.length === 0) return;

  // Same key tap-to-read.js writes to (PR 83) - one storage entry per chapter+
  // translation regardless of which mode read it, so switching modes mid-chapter picks
  // up from wherever progress actually got to instead of resetting it.
  const progressKey = `${PROGRESS_KEY_PREFIX}${location.pathname}${location.search}`;

  function loadFurthest() {
    const raw = localStorage.getItem(progressKey);
    if (!raw) return 0;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && Number.isInteger(parsed.revealed)) return parsed.revealed;
    } catch {
      // not JSON - fall through to the legacy bare-number format below
    }
    const legacy = Number(raw);
    return Number.isInteger(legacy) && legacy >= 1 ? legacy : 0;
  }

  // Never regresses: whichever mode got further into the chapter wins.
  let furthest = Math.min(loadFurthest(), paragraphs.length);

  function save() {
    localStorage.setItem(progressKey, JSON.stringify({ revealed: furthest, total: paragraphs.length }));
  }

  // Re-saves immediately so a legacy bare-number entry (no `total`) gains one right
  // away, without waiting for the visitor to scroll past anything new this visit.
  if (furthest > 0) save();

  const observer = new IntersectionObserver(
    (entries) => {
      let changed = false;
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const index = paragraphs.indexOf(entry.target);
        if (index === -1 || index + 1 <= furthest) continue;
        furthest = index + 1;
        changed = true;
      }
      if (changed) save();
    },
    { threshold: 0 }
  );

  for (const paragraph of paragraphs) observer.observe(paragraph);
})();
