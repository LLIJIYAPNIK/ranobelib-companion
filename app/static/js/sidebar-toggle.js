// Sidebar toggle (PR 39): expands the 84px rail (icon over label, PR 249) to the 248px
// one with labels beside the icons. The chosen state is remembered in localStorage across
// page loads/navigations, since every page renders the sidebar collapsed by default
// (server-rendered, no per-visitor state) and this is purely a client-side preference.
(() => {
  const STORAGE_KEY = "sidebarExpanded";
  const sidebar = document.querySelector('[data-role="sidebar"]');
  const toggle = document.querySelector('[data-role="sidebar-toggle"]');
  if (!sidebar || !toggle) return;

  function savedExpanded() {
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      return sidebar.classList.contains("sidebar--expanded");
    }
  }

  // PR 320: the collapsed rail shows icons only - each label stays in the DOM as the
  // control's accessible name and becomes its hover tooltip; expanded, the visible label
  // says it already.
  const labelled = [...sidebar.querySelectorAll(".sidebar__link, .sidebar__bell, .sidebar__guest")];

  function syncTooltips(expanded) {
    for (const item of labelled) {
      const label = item.querySelector(".sidebar__label, .sidebar__guest-label");
      if (expanded || !label) item.removeAttribute("title");
      else item.title = label.textContent.trim();
    }
  }

  function apply(expanded) {
    sidebar.classList.toggle("sidebar--expanded", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", expanded ? "Свернуть меню" : "Развернуть меню");
    toggle.title = expanded ? "Свернуть меню" : "Развернуть меню";
    syncTooltips(expanded);
  }

  apply(savedExpanded());

  toggle.addEventListener("click", () => {
    const expanded = !sidebar.classList.contains("sidebar--expanded");
    apply(expanded);
    try {
      localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0");
    } catch {
      // The control still works for this page when persistence is unavailable.
    }
    window.dispatchEvent(
      new CustomEvent("sidebar:statechange", { detail: { expanded } }),
    );
  });
})();
