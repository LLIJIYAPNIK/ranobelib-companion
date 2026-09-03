// Same synchronous-before-first-paint trick as sidebar-expand-init.js, so a visitor who
// already dismissed the banner never sees it flash in. Moved out of an inline <script>
// into its own file (PR 189) so script-src can drop 'unsafe-inline'.
if (localStorage.getItem("cookieNoticeDismissed") === "1") {
  document.querySelector('[data-role="cookie-notice"]').hidden = true;
}
