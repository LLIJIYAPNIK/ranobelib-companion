// «Активность» and the profile: the month calendar works in place (_reading_month.html).
//
// Picking a day shows what was read that day without reloading the page. Each day is a
// link (?day=YYYY-MM-DD) that works without this script; the server renders a panel for
// every day of the month with reading (data-day-panel), one empty panel
// (data-day-panel="") for a day without any and the month's own summary
// (data-day-panel="month"), which the month's name over the calendar (data-day="month")
// brings back. The day card's wash and the page's glow
// follow the picked day's cover (each panel's data-art), cross-fading as the library's
// shelves do; a day without a cover keeps the one shown.
//
// The month arrows (?month=YYYY-MM links) switch months in place as well: the month's
// page is fetched and only the parts that depend on the month (data-month-part - the
// calendar card and «Когда вы читаете») are swapped in, so nothing else reloads or plays
// its entrance again. Pages are kept once fetched, and the months the arrows point to
// are fetched ahead, so a click usually swaps at once. If a fetch fails the link is
// simply followed. The swapped-in parts are copies: a kept page is used again on the
// next visit to its month and must stay whole. Of several quick clicks only the last
// one's month is shown - an earlier fetch that finishes later is dropped. On a touch
// screen a sideways swipe across the calendar switches months as the arrows do.
//
// The books of a day or of the month sit on a strip that scrolls sideways; a mouse can
// drag it (a drag never opens the cover it started on), and when it holds more than
// fits, a scrollbar under it shows where it is - its thumb can be dragged too.
(function () {
  "use strict";

  var root = document.querySelector('[data-role="activity-calendar"]');
  if (!root) return;

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var pages = {}; // url -> Promise<Document>
  var latest = 0; // the last month asked for; earlier answers are dropped

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
    updateBars();
    cal.querySelectorAll(".ac-day--selected").forEach(function (cell) {
      cell.classList.remove("ac-day--selected");
    });
    cal.querySelectorAll('[data-day][aria-pressed="true"]').forEach(function (other) {
      other.setAttribute("aria-pressed", "false");
    });
    if (link.parentElement.classList.contains("ac-day")) link.parentElement.classList.add("ac-day--selected");
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
        if (child.getAttribute("data-role") !== "activity-art") part.appendChild(document.importNode(child, true));
      });
      if (art && freshArt) washWith(freshArt.getAttribute("src"));
      else if (!art && freshArt) part.insertBefore(document.importNode(freshArt, true), part.firstChild);
    });
  }

  // --- The strip's scrollbar --------------------------------------------------------------
  function updateBar(list) {
    var bar = list.parentElement.querySelector('[data-role="strip-bar"]');
    if (!bar || !list.offsetParent) return; // in a hidden panel: measured when shown
    var width = list.scrollWidth;
    var view = list.clientWidth;
    bar.hidden = width <= view + 2;
    if (bar.hidden) return;
    var thumb = bar.querySelector(".ac-strip__thumb");
    thumb.style.setProperty("--strip-share", (view / width) * 100 + "%");
    thumb.style.setProperty("--strip-start", (list.scrollLeft / width) * 100 + "%");
  }

  function updateBars() {
    document.querySelectorAll('[data-role="title-strip"]').forEach(updateBar);
  }

  // The thumb is dragged (mouse, pen or finger); a press on the line beside it first
  // centres the thumb there, then drags on from that point.
  var barDrag = null;

  document.addEventListener("pointerdown", function (event) {
    var bar = event.target.closest && event.target.closest('[data-role="strip-bar"]');
    if (!bar || (event.pointerType === "mouse" && event.button !== 0)) return;
    var list = bar.parentElement.querySelector('[data-role="title-strip"]');
    var thumb = bar.querySelector(".ac-strip__thumb");
    var ratio = list.scrollWidth / bar.clientWidth; // strip px per bar px
    if (event.target !== thumb) {
      var box = bar.getBoundingClientRect();
      list.scrollLeft = (event.clientX - box.left - thumb.offsetWidth / 2) * ratio;
    }
    barDrag = { bar: bar, list: list, x: event.clientX, left: list.scrollLeft, ratio: ratio };
    bar.classList.add("is-dragging");
    if (bar.setPointerCapture) bar.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  document.addEventListener("pointermove", function (event) {
    if (!barDrag) return;
    barDrag.list.scrollLeft = barDrag.left + (event.clientX - barDrag.x) * barDrag.ratio;
    event.preventDefault();
  });

  function endBarDrag() {
    if (!barDrag) return;
    barDrag.bar.classList.remove("is-dragging");
    barDrag = null;
  }

  document.addEventListener("pointerup", endBarDrag);
  document.addEventListener("pointercancel", endBarDrag);

  document.addEventListener(
    "scroll",
    function (event) {
      var target = event.target;
      if (target.nodeType === 1 && target.getAttribute("data-role") === "title-strip") updateBar(target);
    },
    true
  );
  window.addEventListener("resize", updateBars);

  // --- Dragging the strip with a mouse ---------------------------------------------------
  var drag = null;
  var swallowClick = false;

  document.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    var list = event.target.closest('[data-role="title-strip"]');
    if (!list) return;
    drag = { list: list, x: event.clientX, left: list.scrollLeft, moved: false };
  });

  document.addEventListener("pointermove", function (event) {
    if (!drag) return;
    var dx = event.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) < 5) return; // a click, not a drag yet
    if (!drag.moved) {
      drag.moved = true;
      drag.list.classList.add("is-dragging");
    }
    drag.list.scrollLeft = drag.left - dx;
    event.preventDefault();
  });

  function endDrag() {
    if (!drag) return;
    if (drag.moved) {
      swallowClick = true;
      window.setTimeout(function () {
        swallowClick = false;
      }, 0);
    }
    drag.list.classList.remove("is-dragging");
    drag = null;
  }

  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  // The click a drag ends with doesn't open the cover it started on.
  window.addEventListener(
    "click",
    function (event) {
      if (!swallowClick) return;
      swallowClick = false;
      event.preventDefault();
      event.stopPropagation();
    },
    true
  );
  // No native image/link dragging out of the strip.
  document.addEventListener("dragstart", function (event) {
    if (event.target.closest && event.target.closest('[data-role="title-strip"]')) event.preventDefault();
  });

  // --- Swiping months on a touch screen ---------------------------------------------------
  var swipe = null;

  document.addEventListener(
    "touchstart",
    function (event) {
      var grid = event.target.closest && event.target.closest(".ac-cal__grid");
      if (!grid || event.touches.length !== 1) return;
      swipe = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    },
    { passive: true }
  );

  document.addEventListener(
    "touchend",
    function (event) {
      if (!swipe) return;
      var touch = event.changedTouches[0];
      var dx = touch.clientX - swipe.x;
      var dy = touch.clientY - swipe.y;
      swipe = null;
      // Sideways, and clearly more than up or down.
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      var label = dx < 0 ? "Следующий месяц" : "Предыдущий месяц";
      var arrow = calendar().querySelector('a.ac-cal__arrow[aria-label="' + label + '"]');
      if (!arrow || !window.fetch || !window.DOMParser) return;
      // The tap a swipe ends on must not pick that day.
      swallowClick = true;
      window.setTimeout(function () {
        swallowClick = false;
      }, 400);
      goToMonth(arrow, true, dx < 0 ? "next" : "prev");
    },
    { passive: true }
  );

  function goToMonth(arrow, push, from) {
    var url = arrow.getAttribute("href");
    var ticket = ++latest;
    var cal = calendar();
    cal.setAttribute("aria-busy", "true");
    cal.classList.add("is-loading");
    fetchPage(url)
      .then(function (doc) {
        if (ticket !== latest) return;
        swapMonth(doc);
        var grid = calendar().querySelector(".ac-cal__grid");
        if (from && grid) grid.classList.add(from === "next" ? "is-from-next" : "is-from-prev");
        updateBars();
        if (push && window.history && window.history.pushState) {
          window.history.pushState({ activityMonth: url }, "", url);
        }
        prefetchNeighbours();
      })
      .catch(function () {
        if (ticket === latest) window.location.href = url;
      })
      .then(function () {
        if (ticket !== latest) return;
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
      goToMonth(arrow, true, arrow.getAttribute("aria-label") === "Следующий месяц" ? "next" : "prev");
    }
  });

  // Back and forward through months switched in place.
  window.addEventListener("popstate", function () {
    var url = window.location.pathname + window.location.search;
    goToMonth({ getAttribute: function () { return url; } }, false);
  });

  updateBars();

  if (window.fetch && window.DOMParser) {
    var idle = window.requestIdleCallback || function (fn) { return window.setTimeout(fn, 800); };
    idle(prefetchNeighbours);
  }
})();
