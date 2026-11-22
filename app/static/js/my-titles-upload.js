// Progressive enhancement for the EPUB upload form on /my-titles (issue 432).
// Plain HTML form submission works without JS; this adds selected-filename feedback,
// client-side file size pre-check, and an uploading status indicator.
(() => {
  const form = document.querySelector('[data-role="epub-upload-form"]');
  if (!form) return;

  const input = form.querySelector('[data-role="epub-file-input"]');
  const label = form.querySelector('[data-role="epub-file-label"]');
  const submit = form.querySelector('[data-role="epub-upload-submit"]');
  const status = form.querySelector('[data-role="epub-upload-status"]');
  const errorEl = document.querySelector('[data-role="epub-upload-error"]');
  const maxBytes = Number(form.dataset.maxBytes) || 0;
  const maxLabel = form.dataset.maxLabel || "";

  function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = !message;
  }

  if (input && label) {
    input.addEventListener("change", () => {
      showError("");
      const file = input.files && input.files[0];
      if (!file) {
        label.textContent = "Выберите файл EPUB";
        return;
      }
      label.textContent = file.name;
      if (maxBytes > 0 && file.size > maxBytes) {
        showError(`Файл слишком большой (максимум ${maxLabel}).`);
      }
    });
  }

  form.addEventListener("submit", (event) => {
    const file = input && input.files && input.files[0];
    if (!file) return;
    if (maxBytes > 0 && file.size > maxBytes) {
      event.preventDefault();
      showError(`Файл слишком большой (максимум ${maxLabel}).`);
      return;
    }
    showError("");
    if (submit) submit.disabled = true;
    if (status) {
      status.textContent = "Проверяем и сохраняем EPUB…";
      status.hidden = false;
    }
  });
})();
