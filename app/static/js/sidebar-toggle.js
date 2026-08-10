// Sidebar burger toggle (PR 39): expands the icon-only sidebar (PR 23/26) to show a
// text label next to each icon. The chosen state is remembered in localStorage across
// page loads/navigations, since every page renders the sidebar collapsed by default
// (server-rendered, no per-visitor state) and this is purely a client-side preference.
(() => {
  const STORAGE_KEY = "sidebarExpanded";
  const sidebar = document.querySelector('[data-role="sidebar"]');
  const toggle = document.querySelector('[data-role="sidebar-toggle"]');
  if (!sidebar || !toggle) return;

  function apply(expanded) {
    sidebar.classList.toggle("sidebar--expanded", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
  }

  apply(localStorage.getItem(STORAGE_KEY) === "1");

  toggle.addEventListener("click", () => {
    const expanded = !sidebar.classList.contains("sidebar--expanded");
    apply(expanded);
    localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0");
  });
})();
