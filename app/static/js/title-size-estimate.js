// Async title size estimate (PR 43): estimate_title_size() samples several chapters'
// content, which can take a few seconds - fetched after the title page itself has
// already rendered (app/api/titles.py's title_size_estimate route) instead of blocking
// show_title() on it.
(() => {
  const el = document.querySelector('[data-role="title-size-estimate"]');
  if (!el) return;

  fetch(`/titles/${el.dataset.slugUrl}/size-estimate`)
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      if (data && data.label) {
        el.textContent = `≈ ${data.label}`;
      } else {
        el.remove();
      }
    })
    .catch(() => el.remove());
})();
