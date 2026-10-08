// PR 330: the download queue for reading without a network - strictly one chapter at a
// time, never several requests at once (each chapter is one SDK call on the server, under
// its rate limit; CLAUDE.md forbids fanning out around it). Pure logic: what "download
// one chapter" means is handed in (`download`), so it's tested without a browser
// (tests/js/offline_queue_harness.mjs); offline-download.js wires it to the real one,
// OfflineQueue.downloadChapter below.
//
// States: idle → running → done | paused | cancelled. A pause keeps its place: «Продолжить»
// starts again from the chapter that was interrupted.
//   pause reason "user"       - the visitor paused;
//   "rate-limit" (429), "blocked" (503) - the source is pushing back: stop asking, offer
//     «Продолжить» rather than retrying on our own;
//   "quota" - the device is out of space (QuotaExceededError);
//   "offline" - the network went away (fetch itself failed).
// Any other failure (404, 403 paid chapter, a translation that needs choosing, ...) is
// that chapter's alone: it's listed in `failed` and the queue moves on; «Повторить»
// (retryFailed) runs just those again.
(() => {
  class HttpError extends Error {
    constructor(status, detail) {
      super(detail || `HTTP ${status}`);
      this.name = "HttpError";
      this.status = status;
      this.detail = detail || null;
    }
  }

  const PAUSING_STATUS = { 429: "rate-limit", 503: "blocked" };

  function pauseReasonFor(error) {
    if (error?.name === "QuotaExceededError") return "quota";
    if (error?.name === "HttpError") return PAUSING_STATUS[error.status] || null;
    if (error?.name === "TypeError") return "offline"; // fetch() rejecting: no network
    return null;
  }

  class OfflineQueue {
    constructor(items, { download, onChange = () => {} }) {
      this.items = [...items];
      this.download = download;
      this.onChange = onChange;
      this.state = "idle";
      this.pauseReason = null;
      this.position = 0; // index into items of the next chapter to fetch
      this.completed = 0;
      this.bytes = 0;
      this.failed = []; // [{ item, message }]
      this.current = null; // the item being fetched right now
      this._controller = null;
      this._run = null;
      this._token = 0;
    }

    get total() {
      return this.items.length;
    }

    // A run started while the previous one is still settling (pause aborts the request
    // in flight; it rejects a moment later) waits for it, and the old one, its token
    // stale, stops without touching anything - never two requests at once.
    start() {
      if (this.state === "running" || this.state === "cancelled") return this._run;
      this.state = "running";
      this.pauseReason = null;
      this._emit();
      const token = ++this._token;
      const previous = this._run;
      this._run = (async () => {
        if (previous) await previous;
        if (token === this._token && this.state === "running") await this._loop(token);
      })();
      return this._run;
    }

    resume() {
      return this.start();
    }

    pause(reason = "user") {
      if (this.state !== "running") return;
      this.state = "paused";
      this.pauseReason = reason;
      this._controller?.abort();
      this._emit();
    }

    cancel() {
      if (this.state === "done" || this.state === "cancelled") return;
      this.state = "cancelled";
      this._controller?.abort();
      this._emit();
    }

    // «Повторить»: the chapters that failed go back on the queue, after anything left.
    retryFailed() {
      if (!this.failed.length || this.state === "running" || this.state === "cancelled") return;
      const again = this.failed.map((entry) => entry.item);
      this.failed = [];
      this.items = [...this.items, ...again];
      return this.start();
    }

    async _loop(token) {
      const live = () => this.state === "running" && token === this._token;
      while (live() && this.position < this.items.length) {
        const item = this.items[this.position];
        this.current = item;
        this._controller = new AbortController();
        this._emit();
        try {
          const bytes = await this.download(item, this._controller.signal);
          if (!live()) break; // paused/cancelled mid-chapter: redo it later
          this.bytes += bytes || 0;
          this.completed += 1;
          this.position += 1;
        } catch (error) {
          if (!live()) break;
          const reason = pauseReasonFor(error);
          if (reason) {
            this.pause(reason);
            break;
          }
          this.failed.push({ item, message: error?.detail || error?.message || "Ошибка" });
          this.position += 1;
        }
      }
      if (token !== this._token) return;
      this.current = null;
      this._controller = null;
      if (this.state === "running") {
        this.state = "done";
        this._emit();
      }
    }

    _emit() {
      this.onChange(this);
    }
  }

  async function readError(response) {
    let detail = null;
    try {
      detail = (await response.json())?.detail || null;
    } catch {
      // not JSON
    }
    return new HttpError(response.status, detail);
  }

  // One chapter: its fragment (PR 329), then its images one after another through the
  // same-origin proxy, then into offlineStore. A missing image doesn't fail the chapter.
  async function downloadChapter(title, item, signal) {
    const query = item.branchId != null ? `?branch_id=${encodeURIComponent(item.branchId)}` : "";
    const response = await fetch(
      window.offlineStore.chapterUrl(title.slug, item.volume, item.number) + query,
      { signal, headers: { Accept: "application/json" } },
    );
    if (!response.ok) throw await readError(response);
    const fragment = await response.json();
    const images = [];
    for (const url of fragment.images) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      try {
        const image = await fetch(url, { signal });
        if (image.ok) images.push({ url, response: image });
      } catch (error) {
        if (signal.aborted) throw error;
      }
    }
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    return window.offlineStore.saveChapter(title, fragment, images);
  }

  OfflineQueue.HttpError = HttpError;
  OfflineQueue.pauseReasonFor = pauseReasonFor;
  OfflineQueue.downloadChapter = downloadChapter;
  window.OfflineQueue = OfflineQueue;
})();
