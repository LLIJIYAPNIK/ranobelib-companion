// PR 255: applies the reader theme (readerSettings.theme) before first paint - loaded
// synchronously in <head> on the chapter page, so a Sepia or Light reader doesn't flash
// dark first. reader-settings.js owns the setting and applies everything else once the
// page has loaded.
(() => {
  try {
    const theme = JSON.parse(localStorage.getItem("readerSettings") || "{}").theme;
    if (theme && theme !== "aurora") document.documentElement.dataset.readerTheme = theme;
  } catch {
    // no storage - the default theme it is
  }
})();
