// PR 203: title.html (app/templates/title.html) now renders a skeleton immediately, with
// no SDK call of its own - this fetches the real content from GET /titles/{slug}/data
// (app/api/titles.py's title_data()) right after the page loads and swaps it into
// [data-role="title-content"], so a cache miss on ranobelib.me shows a spinner/skeleton
// instead of leaving the browser's own tab-loading indicator as the only sign anything is
// happening (a cache hit, the common case, replaces it almost immediately).
//
// The scripts the injected markup itself depends on (chapter-export-panel.js,
// title-size-estimate.js, custom-dropdown.js, toc-tap-progress.js) each ran once, eagerly,
// at page load before this PR - now that their target elements don't exist until this
// fetch resolves, they instead expose an init function this callback calls explicitly once
// the real markup is in the DOM. image-lightbox.js needs no such call - it already opens
// for .title-hero__cover via a delegated document-level listener (see its own comments on
// why), which works the same whether that element was there from the start or not.
(() => {
  const container = document.querySelector('[data-role="title-content"]');
  if (!container) return;

  const errorEl = document.querySelector('[data-role="title-content-error"]');
  const slugUrl = container.dataset.slugUrl;

  function showError(message) {
    container.hidden = true;
    if (errorEl) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    }
  }

  fetch(`/titles/${slugUrl}/data`)
    .then(async (response) => {
      if (!response.ok) {
        let detail = "Не удалось загрузить тайтл";
        try {
          const data = await response.json();
          if (data && data.detail) detail = data.detail;
        } catch {
          // No JSON body (or not JSON at all) - keep the generic message.
        }
        showError(detail);
        return;
      }

      container.innerHTML = await response.text();

      const hero = container.querySelector(".title-hero[data-display-name]");
      if (hero) document.title = `${hero.dataset.displayName} — RanobeLib Companion`;

      if (window.enhanceCustomDropdowns) window.enhanceCustomDropdowns();
      if (window.initChapterExportPanel) window.initChapterExportPanel();
      if (window.initTitleSizeEstimate) window.initTitleSizeEstimate();
      if (window.initTocTapProgress) window.initTocTapProgress();
    })
    .catch(() => showError("Не удалось загрузить тайтл"));
})();
