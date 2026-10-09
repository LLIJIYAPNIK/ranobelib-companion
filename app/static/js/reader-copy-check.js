// PR 339: a downloaded copy opened while there is a network (the service worker gave the
// copy because the network was slow - app/pwa/service-worker.js) quietly checks itself
// against the site: the copy carries its content's version (data-content-version), the
// server says the current one (GET /offline/titles/{slug}/chapters/{v}/{n}/version - one
// SDK call, like opening the chapter online). When they differ, «Глава обновлена на
// сайте» with «Обновить копию»: the chapter is downloaded again (OfflineQueue's own
// single-chapter download, so the same requests one after another) and the page reloads.
//
// Without a network, with no version (a copy downloaded before PR 339) or on any error,
// nothing is shown - the copy is still what the reader asked to keep.
(() => {
  const chapter = document.querySelector('[data-role="chapter"][data-offline-copy="1"]');
  const banner = document.querySelector('[data-role="reader-copy-updated"]');
  if (!chapter || !banner) return;
  const { slugUrl, volume, number, branchId, contentVersion } = chapter.dataset;
  if (!contentVersion) return;

  const text = banner.querySelector('[data-role="reader-copy-updated-text"]');
  const button = banner.querySelector('[data-role="reader-copy-refresh"]');
  const path = ["titles", slugUrl, "chapters", volume, number].map(encodeURIComponent).join("/");
  const query = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : "";

  async function check() {
    if (navigator.onLine === false) return false;
    try {
      const response = await fetch(`/offline/${path}/version${query}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return false;
      const { version } = await response.json();
      if (!version || version === contentVersion) return false;
    } catch {
      return false;
    }
    banner.hidden = false;
    return true;
  }

  async function refresh() {
    const store = window.offlineStore;
    const title = (await store.listTitles()).find((t) => t.slug === slugUrl);
    if (!title) throw new Error("not downloaded any more");
    const before = (await store.chaptersOf(slugUrl)).find(
      (c) => c.volume === volume && c.number === number,
    );
    const item = { volume, number, branchId: branchId ? Number(branchId) : null };
    await window.OfflineQueue.downloadChapter(title, item, new AbortController().signal);
    await store.dropUnusedImages(before?.images || []);
  }

  button?.addEventListener("click", async () => {
    button.disabled = true;
    text.textContent = "Обновляем копию…";
    try {
      await refresh();
      window.location.reload();
    } catch {
      text.textContent = "Не удалось обновить копию — попробуйте позже.";
      button.disabled = false;
    }
  });

  window.readerCopyCheck = { check, refresh };
  if (window.offlineStore?.supported() && window.OfflineQueue) check();
})();
