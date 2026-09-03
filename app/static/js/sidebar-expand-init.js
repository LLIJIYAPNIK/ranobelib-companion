// PR 108: applied synchronously during parsing, before the sidebar is first painted, so
// the width/flex-basis transition (app.css .sidebar) never plays on a plain page
// navigation - only sidebar-toggle.js's later click handler should ever animate it.
// Deferred scripts run too late to avoid that flash-then-expand, and this had to move out
// of an inline <script> into its own file (PR 189) so script-src can drop 'unsafe-inline'.
if (localStorage.getItem("sidebarExpanded") === "1") {
  document.currentScript.parentElement.classList.add("sidebar--expanded");
}
