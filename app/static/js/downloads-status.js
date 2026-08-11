// Polls the current user's active downloads (GET /downloads/status) - loaded on every
// page (base.html, like download-ready.js) so the sidebar badge (PR 56) stays live
// everywhere, and additionally updates the in-page list when [data-role="active-downloads"]
// is on the current page (the "Загрузки"/"Активность" sections, PR 17/18), same idea as
// download-status.js but for a whole list at once. There's deliberately one poll loop for
// both jobs, not two - a second one hitting the same endpoint would be redundant.
// Once nothing is active any more *and* the in-page list is showing, reloads the page once
// so the freshly finished job(s) show up under "История" (written server-side by
// app/jobs/download.py).
(() => {
  const badge = document.querySelector('[data-role="downloads-badge"]');
  const section = document.querySelector('[data-role="active-downloads"]');
  if (!badge && !section) return;

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

    if (badge) {
      badge.hidden = jobs.length === 0;
      if (jobs.length > 0) badge.textContent = jobs.length > 9 ? "9+" : String(jobs.length);
    }

    if (section) {
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
    }

    setTimeout(poll, section ? 1500 : 5000);
  }

  poll();
})();
