// PR 108/310: restore the desktop rail synchronously while <nav> is still being parsed.
// `sidebar--initializing` disables both rail and content transitions through the first
// paint; only a deliberate sidebar-toggle.js click is allowed to animate the geometry.
// Kept in a same-origin file (PR 189) so CSP needs no unsafe inline-script exception.
(() => {
  const sidebar = document.currentScript?.parentElement;
  if (!sidebar) return;

  sidebar.classList.add("sidebar--initializing");
  // Experiment: ?nav=top / ?nav=side picks the desktop layout and is remembered. Its own
  // try, so a failure here never costs the rail its restored state below.
  let top = false;
  try {
    const asked = new URLSearchParams(location.search).get("nav");
    if (asked === "top" || asked === "side") localStorage.setItem("navLayout", asked);
    top = localStorage.getItem("navLayout") === "top";
    if (top) document.documentElement.dataset.nav = "top";
  } catch {
    top = false;
  }

  try {
    sidebar.classList.toggle(
      "sidebar--expanded",
      !top && localStorage.getItem("sidebarExpanded") === "1",
    );
  } catch {
    // Storage can be unavailable in hardened/private contexts; collapsed remains valid.
  }

  requestAnimationFrame(() => {
    requestAnimationFrame(() => sidebar.classList.remove("sidebar--initializing"));
  });
})();
