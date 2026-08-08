// Polls the current user's active downloads (GET /downloads/status) and updates each
// row in place - the same idea as download-status.js, but for a whole list at once.
// Once nothing is active any more, reloads the page once so the freshly finished job(s)
// show up under "История" (already written server-side by app/jobs/download.py).
(() => {
  const section = document.querySelector('[data-role="active-downloads"]');
  if (!section) return;

  const STATUS_LABELS = {
    queued: () => "В очереди…",
    running: (job) =>
      job.total ? `Глава ${job.completed} из ${job.total}` : "Начинаем скачивание…",
    exporting: () => "Сборка файла…",
    needs_translation: () => "Нужен выбор перевода",
  };

  function formatEta(seconds) {
    const total = Math.round(seconds);
    if (total < 60) return `${total} с`;
    return `${Math.round(total / 60)} мин`;
  }

  async function poll() {
    let jobs;
    try {
      const response = await fetch("/downloads/status");
      jobs = await response.json();
    } catch {
      setTimeout(poll, 2000);
      return;
    }

    if (jobs.length === 0) {
      window.location.reload();
      return;
    }

    for (const job of jobs) {
      const row = section.querySelector(`[data-job-id="${job.job_id}"]`);
      if (!row) continue;

      const bar = row.querySelector('[data-role="bar-fill"]');
      if (bar && job.total) {
        bar.style.width = `${Math.min(100, (job.completed / job.total) * 100)}%`;
      }

      const text = row.querySelector('[data-role="status-text"]');
      const label = STATUS_LABELS[job.status];
      if (text && label) text.textContent = label(job);

      const etaText = row.querySelector('[data-role="eta-text"]');
      if (etaText) {
        etaText.textContent =
          job.status === "running" && job.eta_seconds != null
            ? `Осталось ≈ ${formatEta(job.eta_seconds)}`
            : "";
      }
    }

    setTimeout(poll, 1500);
  }

  poll();
})();
