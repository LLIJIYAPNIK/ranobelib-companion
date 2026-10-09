// PR 331: the reader's side of reading without a network. On every chapter page it notes
// which chapter of the title was opened last (readerLastChapter:{slug}) - the offline
// page's «Продолжить» (offline-page.js) starts from it. In a downloaded copy (the page
// the service worker serves offline, data-offline-copy) it also marks the links to
// neighbouring chapters that aren't on the device - HUD arrows, the bottom bar, the end
// card - as «не скачана», so it's clear before tapping that they won't open offline.
// PR 335: for a downloaded title it also notes when it was opened (offlineStore.touchTitle,
// shown in «Офлайн» in the settings) and, with the auto-cleanup on there (cleanBehind N),
// removes the chapters more than N behind this one - never this one or the ones after it.
// PR 336: touchTitle keeps this chapter too - «Продолжить чтение» (/continue) opens it
// when there's no network.
(() => {
  const chapter = document.querySelector('[data-role="chapter"]');
  if (!chapter) return;
  const { slugUrl, volume, number } = chapter.dataset;
  try {
    localStorage.setItem(`readerLastChapter:${slugUrl}`, `${volume}--${number}`);
  } catch {
    // no storage: «Продолжить» falls back to the first downloaded chapter
  }

  if (!window.offlineStore?.supported()) return;
  const store = window.offlineStore;
  store.touchTitle(slugUrl, new Date(), { volume, number }).catch(() => {});
  const { cleanBehind } = store.settings();
  if (cleanBehind > 0) store.deleteRead(slugUrl, cleanBehind, { volume, number }).catch(() => {});

  if (chapter.dataset.offlineCopy !== "1") return;

  const CHAPTER_PATH = /^\/titles\/([^/]+)\/chapters\/([^/]+)\/([^/]+)$/;
  const neighbours = [...document.querySelectorAll("a[href]")]
    .map((link) => ({ link, match: CHAPTER_PATH.exec(new URL(link.href, location.href).pathname) }))
    .filter(({ match }) => match && decodeURIComponent(match[1]) === slugUrl);

  window.offlineStore
    .savedKeys(slugUrl)
    .then((saved) => {
      for (const { link, match } of neighbours) {
        const key = `${decodeURIComponent(match[2])}--${decodeURIComponent(match[3])}`;
        if (saved.has(key)) continue;
        link.classList.add("reader-link--not-downloaded");
        const label = link.getAttribute("aria-label");
        if (label) link.setAttribute("aria-label", `${label} — не скачана`);
        if (link.textContent.trim()) {
          const note = document.createElement("span");
          note.className = "reader-link__note";
          note.textContent = " · не скачана";
          link.append(note);
        }
      }
    })
    .catch(() => {});
})();
