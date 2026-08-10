// Async title size estimate (PR 43): estimate_title_size() samples several chapters'
// content, which can take a few seconds - fetched after the title page itself has
// already rendered (app/api/titles.py's title_size_estimate route) instead of blocking
// show_title() on it.
(() => {
  const el = document.querySelector('[data-role="title-size-estimate"]');
  if (!el) return;

  // Rotates through status captions while the fetch is in flight - there's no real
  // step-by-step progress to report (estimate_title_size() has no on_chapter-style
  // callback, unlike download_title()), just an indication that something's happening.
  const STATUSES = ["Загружаем главы…", "Подсчитываем размер…"];
  const statusEl = el.querySelector('[data-role="title-size-estimate-status"]');
  let statusIndex = 0;
  const cycle = setInterval(() => {
    statusIndex = (statusIndex + 1) % STATUSES.length;
    if (statusEl) statusEl.textContent = STATUSES[statusIndex];
  }, 1800);

  fetch(`/titles/${el.dataset.slugUrl}/size-estimate`)
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      clearInterval(cycle);
      if (data && data.label) {
        el.textContent = `≈ ${data.label}`;
      } else {
        el.remove();
      }
    })
    .catch(() => {
      clearInterval(cycle);
      el.remove();
    });
})();
