// Sidebar toggle (PR 39): expands the 84px rail (icon over label, PR 249) to the 248px
// one with labels beside the icons. The chosen state is remembered in localStorage across
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
    toggle.setAttribute("aria-label", expanded ? "Свернуть меню" : "Развернуть меню");
  }

  apply(localStorage.getItem(STORAGE_KEY) === "1");

  toggle.addEventListener("click", () => {
    const expanded = !sidebar.classList.contains("sidebar--expanded");
    apply(expanded);
    localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0");
  });
})();
