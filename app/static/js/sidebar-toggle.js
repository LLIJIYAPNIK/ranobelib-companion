// Sidebar burger toggle (PR 39): expands the icon-only sidebar (PR 23/26) to show a
// text label next to each icon.
(() => {
  const sidebar = document.querySelector('[data-role="sidebar"]');
  const toggle = document.querySelector('[data-role="sidebar-toggle"]');
  if (!sidebar || !toggle) return;

  function apply(expanded) {
    sidebar.classList.toggle("sidebar--expanded", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
  }

  toggle.addEventListener("click", () => {
    apply(!sidebar.classList.contains("sidebar--expanded"));
  });
})();
