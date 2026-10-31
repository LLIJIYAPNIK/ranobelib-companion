// «Активность»: picking a day in the month calendar shows what was read that day without
// reloading the page. Each day is a link (?day=YYYY-MM-DD) that works without this script;
// the server renders a panel for every day of the month with reading (data-day-panel) and
// one empty panel (data-day-panel="") for a day without any.
(function () {
  "use strict";

  var calendar = document.querySelector('[data-role="activity-calendar"]');
  if (!calendar) return;

  var panels = calendar.querySelectorAll("[data-day-panel]");
  var emptyPanel = calendar.querySelector('[data-day-panel=""]');
  var emptyTitle = calendar.querySelector('[data-role="empty-day-title"]');

  function pick(link) {
    var iso = link.getAttribute("data-day");
    var shown = null;
    panels.forEach(function (panel) {
      var match = panel.getAttribute("data-day-panel") === iso;
      panel.hidden = !match;
      if (match) shown = panel;
    });
    if (!shown && emptyPanel) {
      emptyTitle.textContent = link.getAttribute("data-heading") || "";
      emptyPanel.hidden = false;
    }
    calendar.querySelectorAll(".ac-day--selected").forEach(function (cell) {
      cell.classList.remove("ac-day--selected");
    });
    calendar.querySelectorAll('[data-day][aria-pressed="true"]').forEach(function (other) {
      other.setAttribute("aria-pressed", "false");
    });
    link.parentElement.classList.add("ac-day--selected");
    link.setAttribute("aria-pressed", "true");
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", link.getAttribute("href"));
    }
  }

  calendar.addEventListener("click", function (event) {
    var link = event.target.closest("a[data-day]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    pick(link);
  });
})();
