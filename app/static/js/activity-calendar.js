// «Активность»: the month calendar works in place.
//
// Picking a day shows what was read that day without reloading the page. Each day is a
// link (?day=YYYY-MM-DD) that works without this script; the server renders a panel for
// every day of the month with reading (data-day-panel) and one empty panel
// (data-day-panel="") for a day without any. The day card's wash and the page's glow
// follow the picked day's cover (each panel's data-art), cross-fading as the library's
// shelves do; a day without a cover keeps the one shown.
//
// The month arrows (?month=YYYY-MM links) switch months in place as well: the month's
// page is fetched and only the parts that depend on the month (data-month-part - the
// calendar card and «Когда вы читаете») are swapped in, so nothing else reloads or plays
// its entrance again. Pages are kept once fetched, and the months the arrows point to
// are fetched ahead, so a click usually swaps at once. If a fetch fails the link is
// simply followed.
(function () {
  "use strict";

  var root = document.querySelector('[data-role="activity-calendar"]');
  if (!root) return;

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var pages = {}; // url -> Promise<Document>

  function calendar() {
    return document.querySelector('[data-role="activity-calendar"]');
  }

  // --- The wash --------------------------------------------------------------------------
  function washWith(url) {
    if (!url) return;
    var cal = calendar();
    [cal.querySelector('[data-role="activity-art"]'), document.querySelector(".ac .hm-ambient")]
      .filter(Boolean)
      .forEach(function (img) {
        if (img.getAttribute("src") === url) return;
        if (reduced) {
          img.src = url;
          return;
        }
        img.classList.add("is-hidden");
        window.setTimeout(function () {
          img.onload = function () {
            img.classList.remove("is-hidden");
          };
          img.src = url;
        }, 300);
      });
  }

  // --- Picking a day ---------------------------------------------------------------------
  function pickDay(link) {
    var cal = calendar();
    var iso = link.getAttribute("data-day");
    var shown = null;
    cal.querySelectorAll("[data-day-panel]").forEach(function (panel) {
      var match = panel.getAttribute("data-day-panel") === iso;
      panel.hidden = !match;
      if (match) shown = panel;
    });
    var emptyPanel = cal.querySelector('[data-day-panel=""]');
    if (!shown && emptyPanel) {
      cal.querySelector('[data-role="empty-day-title"]').textContent = link.getAttribute("data-heading") || "";
      emptyPanel.hidden = false;
    }
    if (shown) washWith(shown.getAttribute("data-art"));
    cal.querySelectorAll(".ac-day--selected").forEach(function (cell) {
      cell.classList.remove("ac-day--selected");
    });
    cal.querySelectorAll('[data-day][aria-pressed="true"]').forEach(function (other) {
      other.setAttribute("aria-pressed", "false");
    });
    link.parentElement.classList.add("ac-day--selected");
    link.setAttribute("aria-pressed", "true");
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", link.getAttribute("href"));
    }
  }

  // --- Switching months ------------------------------------------------------------------
  function fetchPage(url) {
    if (!pages[url]) {
      pages[url] = window
        .fetch(url, { credentials: "same-origin", headers: { Accept: "text/html" } })
        .then(function (response) {
          if (!response.ok) throw new Error(response.status);
          return response.text();
        })
        .then(function (html) {
          return new DOMParser().parseFromString(html, "text/html");
        });
      pages[url].catch(function () {
        delete pages[url];
      });
    }
    return pages[url];
  }

  // The months the arrows point to, fetched while the reader looks at this one.
  function prefetchNeighbours() {
    calendar()
      .querySelectorAll("a.ac-cal__arrow[href]")
      .forEach(function (arrow) {
        fetchPage(arrow.getAttribute("href"));
      });
  }

  function swapMonth(doc) {
    document.querySelectorAll("[data-month-part]").forEach(function (part) {
      var fresh = doc.querySelector('[data-month-part="' + part.getAttribute("data-month-part") + '"]');
      if (!fresh) return;
      // The section keeps its own element (no entrance replayed); its insides change,
      // except the wash, which cross-fades to the new month's cover.
      var art = part.querySelector('[data-role="activity-art"]');
      var freshArt = fresh.querySelector('[data-role="activity-art"]');
      Array.prototype.slice.call(part.children).forEach(function (child) {
        if (child !== art) child.remove();
      });
      Array.prototype.slice.call(fresh.children).forEach(function (child) {
        if (child.getAttribute("data-role") !== "activity-art") part.appendChild(child);
      });
      if (art && freshArt) washWith(freshArt.getAttribute("src"));
      else if (!art && freshArt) part.insertBefore(freshArt, part.firstChild);
    });
  }

  function goToMonth(arrow, push) {
    var url = arrow.getAttribute("href");
    var cal = calendar();
    cal.setAttribute("aria-busy", "true");
    cal.classList.add("is-loading");
    fetchPage(url)
      .then(function (doc) {
        swapMonth(doc);
        if (push && window.history && window.history.pushState) {
          window.history.pushState({ activityMonth: url }, "", url);
        }
        prefetchNeighbours();
      })
      .catch(function () {
        window.location.href = url;
      })
      .then(function () {
        var now = calendar();
        now.removeAttribute("aria-busy");
        now.classList.remove("is-loading");
      });
  }

  document.addEventListener("click", function (event) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    var cal = calendar();
    if (!cal.contains(event.target)) return;
    var day = event.target.closest("a[data-day]");
    if (day) {
      event.preventDefault();
      pickDay(day);
      return;
    }
    var arrow = event.target.closest("a.ac-cal__arrow[href]");
    if (arrow && window.fetch && window.DOMParser) {
      event.preventDefault();
      goToMonth(arrow, true);
    }
  });

  // Back and forward through months switched in place.
  window.addEventListener("popstate", function () {
    var url = window.location.pathname + window.location.search;
    goToMonth({ getAttribute: function () { return url; } }, false);
  });

  if (window.fetch && window.DOMParser) {
    var idle = window.requestIdleCallback || function (fn) { return window.setTimeout(fn, 800); };
    idle(prefetchNeighbours);
  }
})();
