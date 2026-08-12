// PR 83: surfaces each chapter's tap-to-read progress (PR 62) right in the table of
// contents on the title page - a mini bar showing how much of the chapter has been
// revealed. Reads the same tapToReadProgress: entries tap-to-read.js itself writes
// (PR 62/79); there is nothing to fetch from the server, since that data never leaves
// the browser.
(() => {
  const PROGRESS_KEY_PREFIX = "tapToReadProgress:";

  // Keyed on pathname alone, then matched against every tapToReadProgress: entry -
  // matching against the raw key with startsWith() would be wrong here: chapter "2"'s
  // pathname is itself a string-prefix of chapter "20"'s, so a naive prefix match could
  // attribute chapter 20's progress to chapter 2. Splitting each key's own query string
  // off first (added by tap-to-read.js only when a non-default translation, PR 7, is in
  // play) and comparing the pathname exactly avoids that.
  function findProgress(pathname) {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key.startsWith(PROGRESS_KEY_PREFIX)) continue;
      const rest = key.slice(PROGRESS_KEY_PREFIX.length);
      const queryIndex = rest.indexOf("?");
      const keyPathname = queryIndex === -1 ? rest : rest.slice(0, queryIndex);
      if (keyPathname !== pathname) continue;

      try {
        const parsed = JSON.parse(localStorage.getItem(key));
        if (parsed && Number.isInteger(parsed.revealed) && Number.isInteger(parsed.total) && parsed.total > 0) {
          return parsed;
        }
      } catch {
        // pre-PR-83 entry (a bare revealed count, no total) - nothing to show a
        // percentage against, same as no saved progress at all.
      }
    }
    return null;
  }

  for (const link of document.querySelectorAll(".toc__chapter-link")) {
    const li = link.closest(".toc__chapter");
    if (!li) continue;

    const progress = findProgress(new URL(link.href, location.origin).pathname);
    if (!progress) continue;

    const percent = Math.round((progress.revealed / progress.total) * 100);
    const bar = document.createElement("span");
    bar.className = "toc__chapter-progress";
    bar.title = `Прочитано ${percent}%`;
    bar.setAttribute("aria-label", `Прочитано ${percent}%`);
    const fill = document.createElement("span");
    fill.className = "toc__chapter-progress__fill";
    fill.style.width = `${percent}%`;
    bar.appendChild(fill);
    li.appendChild(bar);
  }
})();
