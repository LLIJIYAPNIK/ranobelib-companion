// Polls GET /downloads/ready from any page and shows a toast for each finished download
// the visitor hasn't picked up yet (PR 50) - unlike download-status.js, this isn't tied
// to a specific job's own status page, so a title downloaded and left running in the
// background still gets delivered wherever the visitor ends up.
//
// Skipped entirely on the job's own status page (.download-status) - that page already
// shows its own "Скачать файл" link once done, so polling here too would just duplicate
// it.
(() => {
  if (document.querySelector(".download-status")) return;

  const container = document.querySelector('[data-role="download-ready"]');
  if (!container) return;

  const DISMISSED_KEY = "downloadReadyDismissed";
  const shown = new Set();

  function dismissedIds() {
    try {
      return new Set(JSON.parse(sessionStorage.getItem(DISMISSED_KEY) || "[]"));
    } catch {
      return new Set();
    }
  }

  function dismiss(jobId) {
    const ids = dismissedIds();
    ids.add(jobId);
    sessionStorage.setItem(DISMISSED_KEY, JSON.stringify([...ids]));
    shown.delete(jobId);
    render();
  }

  let ready = [];

  function render() {
    const ids = dismissedIds();
    const visible = ready.filter((job) => !ids.has(job.job_id));
    container.hidden = visible.length === 0;
    container.innerHTML = "";

    for (const job of visible) {
      const toast = document.createElement("div");
      toast.className = "download-ready__toast";

      const text = document.createElement("span");
      text.className = "download-ready__text";
      text.textContent = `Файл «${job.slug_url}» (${job.fmt.toUpperCase()}) готов`;
      toast.append(text);

      const link = document.createElement("a");
      link.className = "btn btn--sm";
      link.href = job.file_url;
      link.textContent = "Скачать";
      link.addEventListener("click", () => dismiss(job.job_id));
      toast.append(link);

      const close = document.createElement("button");
      close.type = "button";
      close.className = "download-ready__dismiss";
      close.setAttribute("aria-label", "Скрыть уведомление");
      close.textContent = "×";
      close.addEventListener("click", () => dismiss(job.job_id));
      toast.append(close);

      container.append(toast);
    }
  }

  async function poll() {
    try {
      const response = await fetch("/downloads/ready");
      if (response.ok) ready = await response.json();
    } catch {
      // Network hiccup - just try again on the next tick.
    }
    render();
    setTimeout(poll, 10000);
  }

  poll();
})();
