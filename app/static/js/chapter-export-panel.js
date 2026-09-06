// Floating "download selected chapters" panel (PR 37): without this script the panel
// (app/templates/_title_content.html) is always visible above the table of contents, same
// as before this PR - a safe no-JS fallback. With it, the panel starts hidden and slides
// into its sticky position as soon as the first chapter checkbox is checked, instead of
// sitting in view (and scrolling out of reach on a long table of contents) the whole time.
//
// PR 203: this markup now only exists once title-content-load.js has fetched and injected
// the title page's real content (GET /titles/{slug}/data) - self-running at parse time
// (like every other one-off page script in this app) would find nothing yet, so this
// exposes an init function that call site runs explicitly once that markup is in the DOM.
(() => {
  function initChapterExportPanel() {
    const panel = document.querySelector('[data-role="chapter-export-panel"]');
    const form = document.querySelector('[data-role="chapter-toc-form"]');
    if (!panel || !form) return;

    panel.classList.add("toc__export-panel--js");

    function updateVisibility() {
      const anyChecked = form.querySelector(".toc__chapter-checkbox:checked") !== null;
      panel.classList.toggle("toc__export-panel--visible", anyChecked);
    }

    form.addEventListener("change", (event) => {
      if (event.target instanceof HTMLInputElement && event.target.matches(".toc__chapter-checkbox")) {
        updateVisibility();
      }
    });

    updateVisibility();
  }

  window.initChapterExportPanel = initChapterExportPanel;
})();
