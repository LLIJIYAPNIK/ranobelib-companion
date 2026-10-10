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
})();
