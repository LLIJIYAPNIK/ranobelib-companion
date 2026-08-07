// Polls a background download job's status endpoint and updates the progress bar/text
// in place. On any terminal state (done/error/needs_translation) it reloads the page
// once, rather than duplicating that state's markup here - the server-rendered page
// already knows how to show each of those (see app/templates/download_status.html).
(() => {
  const root = document.querySelector(".download-status");
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const bar = root.querySelector('[data-role="bar-fill"]');
  const text = root.querySelector('[data-role="status-text"]');
  if (!statusUrl || !text) return;

  const RUNNING_LABELS = {
    queued: () => "В очереди…",
    running: (data) =>
      data.total ? `Глава ${data.completed} из ${data.total}` : "Начинаем скачивание…",
    exporting: () => "Сборка файла…",
  };

  const TERMINAL_STATUSES = new Set(["done", "error", "needs_translation"]);

  async function poll() {
    let data;
    try {
      const response = await fetch(statusUrl);
      data = await response.json();
    } catch {
      setTimeout(poll, 2000);
      return;
    }

    if (TERMINAL_STATUSES.has(data.status)) {
      window.location.reload();
      return;
    }

    if (bar && data.total) {
      bar.style.width = `${Math.min(100, (data.completed / data.total) * 100)}%`;
    }
    const label = RUNNING_LABELS[data.status];
    if (label) text.textContent = label(data);

    setTimeout(poll, 1500);
  }

  poll();
})();
