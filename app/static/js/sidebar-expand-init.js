// PR 108/310: restore the desktop rail synchronously while <nav> is still being parsed.
// `sidebar--initializing` disables both rail and content transitions through the first
// paint; only a deliberate sidebar-toggle.js click is allowed to animate the geometry.
// Kept in a same-origin file (PR 189) so CSP needs no unsafe inline-script exception.
(() => {
  const sidebar = document.currentScript?.parentElement;
  if (!sidebar) return;

  sidebar.classList.add("sidebar--initializing");
  try {
    sidebar.classList.toggle(
      "sidebar--expanded",
      localStorage.getItem("sidebarExpanded") === "1",
    );
  } catch {
    // Storage can be unavailable in hardened/private contexts; collapsed remains valid.
  }

  requestAnimationFrame(() => {
    requestAnimationFrame(() => sidebar.classList.remove("sidebar--initializing"));
  });
})();
