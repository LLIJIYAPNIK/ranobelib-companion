// PR 342: the admin UI kit's behaviour (app/templates/admin/_kit.html). Every control
// already works as a plain link or form; this only adds what HTML can't do by itself.
(() => {
  const store = {
    get(key) {
      try {
        return JSON.parse(window.localStorage.getItem(key));
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        window.localStorage.setItem(key, JSON.stringify(value));
      } catch {
        // Private mode or blocked storage - the choice just isn't remembered.
      }
    },
  };

  // «Колонки»: hide/show a table's columns, remembered per browser and table.
  document.querySelectorAll("[data-kit-columns]").forEach((menu) => {
    const table = document.querySelector(`[data-kit-table="${menu.dataset.kitColumns}"]`);
    if (!table) return;
    const key = `admin-columns:${menu.dataset.kitColumns}`;
    const boxes = [...menu.querySelectorAll('input[type="checkbox"]')];
    const count = menu.querySelector("[data-kit-columns-count]");

    const apply = () => {
      boxes.forEach((box) => {
        table.querySelectorAll(`[data-col="${box.value}"]`).forEach((cell) => {
          cell.hidden = !box.checked;
        });
      });
      count.textContent = String(boxes.filter((box) => box.checked).length);
    };

    const saved = store.get(key);
    if (saved && typeof saved === "object") {
      boxes.forEach((box) => {
        if (!box.hasAttribute("data-locked") && box.value in saved) box.checked = !!saved[box.value];
      });
    }
    const save = () => store.set(key, Object.fromEntries(boxes.map((box) => [box.value, box.checked])));

    menu.addEventListener("change", () => {
      apply();
      save();
    });
    menu.querySelector("[data-kit-columns-all]").addEventListener("click", () => {
      boxes.forEach((box) => {
        box.checked = true;
      });
      apply();
      save();
    });
    menu.querySelector("[data-kit-columns-done]").addEventListener("click", () => {
      menu.open = false;
      menu.querySelector("summary").focus();
    });
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });

    apply();
    menu.hidden = false;
  });

  // A filter's select applies on change - the «Найти» button stays for the search box.
  document.querySelectorAll("[data-kit-filters]").forEach((form) => {
    form.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => form.requestSubmit());
    });
  });

  // Drawers and confirmation dialogs: native modal <dialog>s, opened by
  // [data-kit-open="id"]. Escape is the browser's; a click on the backdrop (the dialog
  // element itself, outside its box) and [data-kit-close] close it too.
  const reset = (dialog) => {
    dialog.querySelectorAll("form").forEach((form) => form.reset());
    dialog
      .querySelectorAll("[data-kit-ack]")
      .forEach((box) => box.dispatchEvent(new Event("change")));
    dialog.querySelectorAll("[data-kit-dirty]").forEach((state) => {
      state.textContent = "Изменений нет";
      state.removeAttribute("data-dirty");
    });
  };

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-kit-open]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.kitOpen);
      if (dialog && dialog.matches("[data-kit-dialog]") && !dialog.open) {
        reset(dialog);
        dialog.showModal();
      }
      return;
    }
    const closer = event.target.closest("[data-kit-close]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }
    if (event.target.matches("dialog[data-kit-dialog][open]")) {
      const box = event.target.getBoundingClientRect();
      const inside =
        event.clientX >= box.left &&
        event.clientX <= box.right &&
        event.clientY >= box.top &&
        event.clientY <= box.bottom;
      if (!inside) event.target.close();
    }
  });

  // An irreversible action: its button stays off until «Я понимаю…» is ticked (the box
  // is also `required`, so the form can't go without it even before this runs).
  document.querySelectorAll("[data-kit-confirm]").forEach((form) => {
    const ack = form.querySelector("[data-kit-ack]");
    const submit = form.querySelector("[data-kit-ack-submit]");
    if (!ack || !submit) return;
    const sync = () => {
      submit.disabled = !ack.checked;
    };
    ack.addEventListener("change", sync);
    sync();
  });

  // A drawer form says when it has unsaved changes.
  document.querySelectorAll("dialog[data-kit-dialog] [data-kit-dirty]").forEach((state) => {
    const form = state.closest("form");
    if (!form) return;
    form.addEventListener("input", () => {
      state.textContent = "Есть несохранённые изменения";
      state.setAttribute("data-dirty", "");
    });
  });

  // The toast: re-inserted into its live region so screen readers announce it, then
  // hidden after a while - but not while the pointer or focus is on it.
  const region = document.querySelector("[data-kit-toasts]");
  const toast = region && region.querySelector("[data-kit-toast]");
  if (toast) {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    region.removeChild(toast);
    let timer = 0;
    let held = false;
    const dismiss = () => {
      clearTimeout(timer);
      if (reduced) {
        toast.remove();
        return;
      }
      toast.setAttribute("data-leaving", "");
      toast.addEventListener("animationend", () => toast.remove(), { once: true });
    };
    const schedule = () => {
      clearTimeout(timer);
      if (!held) timer = setTimeout(dismiss, 6500);
    };
    const hold = (on) => {
      held = on;
      if (on) clearTimeout(timer);
      else schedule();
    };
    toast.addEventListener("pointerenter", () => hold(true));
    toast.addEventListener("pointerleave", () => hold(toast.contains(document.activeElement)));
    toast.addEventListener("focusin", () => hold(true));
    toast.addEventListener("focusout", (event) => {
      if (!toast.contains(event.relatedTarget)) hold(false);
    });
    toast.querySelector("[data-kit-toast-close]").addEventListener("click", dismiss);
    requestAnimationFrame(() => {
      region.appendChild(toast);
      schedule();
    });
  }

  // Chart hover: the column under the pointer shows its values in a tooltip, with a
  // cursor line (and a line chart's dot) at that point. The same numbers are in a
  // visually hidden table, so this is only for the eye.
  document.querySelectorAll("[data-kit-chart]").forEach((chart) => {
    const plot = chart.querySelector(".wn-admin-chart__plot");
    const cursor = chart.querySelector("[data-kit-cursor]");
    const dot = chart.querySelector("[data-kit-dot]");
    const tip = chart.querySelector("[data-kit-tip]");
    if (!plot || !cursor || !tip) return;

    const show = (hit) => {
      const style = hit.style;
      const x = style.getPropertyValue("--x");
      const y = style.getPropertyValue("--y");
      cursor.style.left = x;
      dot.hidden = !y;
      if (y) dot.style.top = y;
      tip.innerHTML = hit.querySelector(".wn-admin-chart__tipdata").innerHTML;
      cursor.hidden = false;
      tip.hidden = false;
      // Keep the tooltip inside the plot: right of the point, or left of it near the end.
      const left = (parseFloat(x) / 100) * plot.clientWidth;
      const flip = left + tip.offsetWidth + 16 > plot.clientWidth;
      tip.style.left = `${flip ? Math.max(0, left - tip.offsetWidth - 12) : left + 12}px`;
    };
    const hide = () => {
      cursor.hidden = true;
      tip.hidden = true;
    };

    plot.querySelectorAll("[data-kit-point]").forEach((hit) => {
      hit.addEventListener("pointerenter", () => show(hit));
    });
    plot.addEventListener("pointerleave", hide);
  });
})();
