// Polls a background download job's status endpoint and updates the progress bar/text
// in place. On done, triggers the file download automatically (the visitor already
// clicked "Скачать" once to get here) instead of making them click a second button.
// On any other terminal state (error/needs_translation) it reloads the page once,
// rather than duplicating that state's markup here - the server-rendered page already
// knows how to show each of those (see app/templates/download_status.html). The script
// itself is only ever included for a job that's still in progress (see
// download_status.html) - a job that's already terminal on page load doesn't get here
// at all, which is what keeps this from reload-looping forever once it does.
(() => {
  const root = document.querySelector(".download-status");
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const fileUrl = root.dataset.fileUrl;
  const bar = root.querySelector('[data-role="bar-fill"]');
  const text = root.querySelector('[data-role="status-text"]');
  const etaText = root.querySelector('[data-role="eta-text"]');
  if (!statusUrl || !text) return;

  const RUNNING_LABELS = {
    queued: () => "В очереди…",
    running: (data) =>
      data.total ? `Глава ${data.completed} из ${data.total}` : "Начинаем скачивание…",
    exporting: () => "Сборка файла…",
  };

  const TERMINAL_STATUSES = new Set(["done", "error", "needs_translation"]);

  function formatEta(seconds) {
    const total = Math.round(seconds);
    if (total < 60) return `${total} с`;
    return `${Math.round(total / 60)} мин`;
  }

  async function poll() {
    let data;
    try {
      const response = await fetch(statusUrl);
      data = await response.json();
    } catch {
      setTimeout(poll, 2000);
      return;
    }

    if (data.status === "done") {
      // Attachment Content-Disposition (see app/api/downloads.py's FileResponse) makes
      // this trigger the browser's download without navigating away from the page.
      if (fileUrl) window.location.href = fileUrl;
      if (bar) bar.style.width = "100%";
      text.textContent = "Готово";
      if (etaText) etaText.textContent = "";
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
    if (etaText) {
      etaText.textContent =
        data.status === "running" && data.eta_seconds != null
          ? `Осталось ≈ ${formatEta(data.eta_seconds)}`
          : "";
    }

    setTimeout(poll, 1500);
  }

  poll();
})();
