// PR 353: the admin panel's global search (app/admin_search.py). The box is a plain GET form
// to /admin/search and works without this; here it gets ⌘/Ctrl-K, a drop-down of the first
// hits from /admin/search/suggest as the admin types (arrows + Enter, Escape closes) and,
// on a phone, the icon that opens the box over the top bar.
(() => {
  const slot = document.querySelector('[data-role="admin-search-slot"]');
  const form = slot && slot.querySelector('[data-role="admin-search"]');
  const input = form && form.querySelector('[data-role="admin-search-input"]');
  const list = form && form.querySelector('[data-role="admin-search-results"]');
  const toggle = slot && slot.querySelector('[data-role="admin-search-toggle"]');
  const closer = form && form.querySelector('[data-role="admin-search-close"]');
  if (!form || !input || !list || !toggle || !closer) return;

  const DELAY_MS = 150;
  const MIN_QUERY = 2;
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const kbd = form.querySelector('[data-role="admin-search-kbd"]');
  if (kbd) {
    kbd.textContent = isMac ? "⌘K" : "Ctrl K";
    kbd.hidden = false;
  }
  toggle.title = `Поиск (${isMac ? "⌘K" : "Ctrl K"})`;

  let timer = 0;
  let controller = null;
  let options = [];
  let active = -1;

  const setActive = (index) => {
    options.forEach((option, i) => option.setAttribute("aria-selected", String(i === index)));
    active = index;
    if (index >= 0) {
      input.setAttribute("aria-activedescendant", options[index].id);
      options[index].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  };

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    options = [];
    setActive(-1);
  };

  const element = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  };

  // Built with textContent only - a nickname or comment is never parsed as HTML.
  const render = (data) => {
    list.replaceChildren();
    options = [];
    if (!data.groups.length) {
      list.append(element("p", "wn-admin-search__empty", "Ничего не найдено"));
    }
    data.groups.forEach((group) => {
      const box = element("div", "wn-admin-search__group");
      box.setAttribute("role", "group");
      const title = element("p", "wn-admin-search__group-title", group.label);
      title.id = `admin-search-group-${group.key}`;
      title.setAttribute("aria-hidden", "true");
      box.setAttribute("aria-labelledby", title.id);
      box.append(title);
      group.hits.forEach((hit) => {
        const option = element("a", "wn-admin-search__option");
        option.href = hit.href;
        option.id = `admin-search-option-${options.length}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.tabIndex = -1;
        option.append(element("span", "wn-admin-search__label", hit.label));
        if (hit.sub) option.append(element("span", "wn-admin-search__sub", hit.sub));
        box.append(option);
        options.push(option);
      });
      list.append(box);
    });
    const all = element("a", "wn-admin-search__option wn-admin-search__all", "Все результаты");
    all.href = data.all;
    all.id = `admin-search-option-${options.length}`;
    all.setAttribute("role", "option");
    all.setAttribute("aria-selected", "false");
    all.tabIndex = -1;
    list.append(all);
    options.push(all);
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(-1);
  };

  const suggest = async () => {
    const query = input.value.trim().replace(/\s+/g, " ");
    if (controller) controller.abort();
    if (query.length < MIN_QUERY && !/^\d+$/.test(query)) {
      close();
      return;
    }
    controller = new AbortController();
    try {
      const response = await fetch(`/admin/search/suggest?${new URLSearchParams({ q: query })}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        signal: controller.signal,
      });
      // Logged out (404) or the database timed out (503): the form still submits.
      if (!response.ok) {
        close();
        return;
      }
      const data = await response.json();
      if (input.value.trim().replace(/\s+/g, " ") === data.query) render(data);
    } catch (error) {
      if (error.name !== "AbortError") close();
    }
  };

  // On a phone the icon opens the box over the top bar instead of leaving the page.
  const setPhoneOpen = (open) => {
    slot.toggleAttribute("data-open", open);
    if (open) {
      input.focus();
    } else {
      close();
      toggle.focus();
    }
  };

  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(suggest, DELAY_MS);
  });

  input.addEventListener("focus", () => {
    if (input.value.trim() && list.hidden) suggest();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (list.hidden || !options.length) return;
      event.preventDefault();
      // Past either end the box itself is "active" again (-1), as in a native combobox.
      let next = active + (event.key === "ArrowDown" ? 1 : -1);
      if (next >= options.length) next = -1;
      if (next < -1) next = options.length - 1;
      setActive(next);
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      window.location.assign(options[active].href);
    } else if (event.key === "Escape") {
      if (!list.hidden) {
        event.preventDefault();
        close();
      } else if (slot.hasAttribute("data-open")) {
        setPhoneOpen(false);
      } else {
        input.blur();
      }
    }
  });

  // A click on a hit is the link itself; keep focus in the box until then.
  list.addEventListener("mousedown", (event) => event.preventDefault());

  form.addEventListener("focusout", (event) => {
    if (form.contains(event.relatedTarget)) return;
    close();
    slot.removeAttribute("data-open");
  });

  toggle.addEventListener("click", (event) => {
    event.preventDefault();
    setPhoneOpen(true);
  });
  closer.addEventListener("click", () => setPhoneOpen(false));

  // ⌘K / Ctrl-K from anywhere in the panel.
  document.addEventListener("keydown", (event) => {
    if ((event.key || "").toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey) || event.altKey) return;
    event.preventDefault();
    if (toggle.offsetParent !== null && !slot.hasAttribute("data-open")) {
      setPhoneOpen(true);
    } else {
      input.focus();
    }
    input.select();
  });
})();
