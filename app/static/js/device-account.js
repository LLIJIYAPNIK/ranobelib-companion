// PR 333: what this device keeps for one account must never reach another. Personal data
// here:
//   - the queue of reading progress/activity waiting for the network (sync-queue.js) -
//     sent later it would be credited to whoever is signed in by then;
//   - reading positions (tapToReadProgress:*, readerLastChapter:*) - the reader merges
//     them with the server's (PR 287), so the next account would adopt them as its own;
//   - downloadReadyDismissed (sessionStorage) - which of this account's exports were seen.
// Downloaded chapters (offline-store.js) are public content, but which titles they are
// says what someone reads: they stay only by an explicit «Оставить скачанное».
//
// Two moments clear it:
//   - logout (the profile menu's form): what's queued gets one last chance to go out,
//     then personal data is cleared; with downloads on the device the visitor first
//     chooses «Оставить скачанное» or «Удалить скачанное» (the logout sheet, base.html);
//   - another account on a signed-in page without a logout in between (the session ran
//     out, a different account signed in): this page's account differs from the one
//     recorded here (wnDeviceAccount) - personal data and downloads are cleared, nobody
//     chose to keep them. sync-queue.js waits for `ready` before its first send.
// Device settings (reader look, sidebar, catalog view) aren't personal and stay.
(() => {
  const userId = document.currentScript?.dataset.userId || "";
  const ACCOUNT_KEY = "wnDeviceAccount";
  const PERSONAL_PREFIXES = ["tapToReadProgress:", "readerLastChapter:"];
  const PERSONAL_SESSION_KEYS = ["downloadReadyDismissed"];
  const LAST_SEND_MS = 3000;

  function storageKeys(storage) {
    const keys = [];
    for (let i = 0; i < storage.length; i += 1) keys.push(storage.key(i));
    return keys;
  }

  function forgetLocal() {
    try {
      for (const key of storageKeys(localStorage)) {
        if (PERSONAL_PREFIXES.some((prefix) => key.startsWith(prefix))) localStorage.removeItem(key);
      }
      for (const key of PERSONAL_SESSION_KEYS) sessionStorage.removeItem(key);
    } catch {
      // no storage at all - nothing kept either
    }
  }

  async function clearPersonal() {
    forgetLocal();
    try {
      await window.syncQueue?.clear();
    } catch {
      // the queue's IndexedDB is unavailable - so is anything queued in it
    }
  }

  async function clearDownloads() {
    if (!window.offlineStore?.supported()) return;
    try {
      await window.offlineStore.clearAll();
    } catch {
      // best effort: the copy can still be removed in «Скачано»
    }
  }

  function recorded() {
    try {
      return localStorage.getItem(ACCOUNT_KEY) || "";
    } catch {
      return "";
    }
  }

  function record(value) {
    try {
      if (value) localStorage.setItem(ACCOUNT_KEY, value);
      else localStorage.removeItem(ACCOUNT_KEY);
    } catch {
      // private mode: the next page simply can't tell a switch apart
    }
  }

  let ready = Promise.resolve();
  const previous = recorded();
  if (userId && previous && previous !== userId) {
    ready = Promise.all([clearPersonal(), clearDownloads()]).then(() => {});
  }
  if (userId) record(userId);

  async function leave(form, keepDownloads) {
    try {
      await Promise.race([
        window.syncQueue?.flush(),
        new Promise((resolve) => setTimeout(resolve, LAST_SEND_MS)),
      ]);
      await clearPersonal();
      if (!keepDownloads) await clearDownloads();
      record("");
    } finally {
      form.submit(); // never stands between the visitor and logging out
    }
  }

  async function downloadedTitles() {
    if (!window.offlineStore?.supported()) return [];
    try {
      return await window.offlineStore.listTitles();
    } catch {
      return [];
    }
  }

  const sheet = document.getElementById("logout-offline-choice");
  let pendingForm = null;
  sheet?.querySelectorAll("[data-logout-keep]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!pendingForm) return;
      sheet.querySelectorAll("button").forEach((b) => (b.disabled = true));
      leave(pendingForm, button.dataset.logoutKeep === "1");
    });
  });

  document.querySelectorAll('form[action="/logout"]').forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const titles = await downloadedTitles();
      if (!titles.length || !sheet || !window.bottomSheet) {
        leave(form, false);
        return;
      }
      pendingForm = form;
      const count = sheet.querySelector('[data-role="logout-offline-count"]');
      count.textContent = String(titles.length);
      window.dispatchEvent(new Event("profile-menu:close"));
      window.bottomSheet.open({
        title: sheet.dataset.bottomSheetTitle,
        content: sheet,
        opener: event.submitter || form.querySelector("button"),
      });
    });
  });

  window.deviceAccount = { ready, clearPersonal, clearDownloads };
})();
