// Issue 394: titles under «Найти тайтл по названию» (search_form(), home/_macros.html) as
// the visitor types - an ARIA combobox over GET /catalog/suggest. The field keeps focus;
// ↑/↓ move through the list (aria-activedescendant), Enter opens the one picked or, with
// nothing picked, submits the form as before (/catalog?query=…), Esc closes the list. A
// request waits 250ms after the last key and the one before it is aborted. Without this
// script the form is the plain catalog search it always was.
(() => {
  const MIN_QUERY = 2;
  const DEBOUNCE_MS = 250;
  const SKELETON_ROWS = 3;

  document.querySelectorAll('[data-role="search-suggest"]').forEach((form, index) => {
    const input = form.querySelector('input[name="query"]');
    if (!input) return;

    const listId = `search-suggest-${index}`;
    const list = document.createElement("div");
    list.className = "hm-suggest";
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Подходящие тайтлы");
    list.hidden = true;
    form.append(list);

    // What a screen reader hears when the list changes: «Ищем…», N тайтлов, nothing.
    const status = document.createElement("span");
    status.className = "hm-suggest__status";
    status.setAttribute("role", "status");
    form.append(status);

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("autocomplete", "off");

    let timer = 0;
    let controller = null;
    let options = [];
    let active = -1;
    let shownQuery = "";

    function setOpen(open) {
      list.hidden = !open;
      form.classList.toggle("is-suggesting", open);
      input.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) fitToViewport();
      else setActive(-1);
    }

    function setActive(next) {
      if (options[active]) options[active].classList.remove("is-at");
      active = next;
      const option = options[active];
      if (option) {
        option.classList.add("is-at");
        input.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    // The list never runs under the on-screen keyboard or the phone's bottom navigation:
    // it ends above the visual viewport's bottom edge and scrolls inside itself. The CSS
    // takes off the navigation (--shell-bottom, 0 while mobile-keyboard.js has the
    // keyboard open); this only says where the field ends and the viewport does.
    function fitToViewport() {
      const viewport = window.visualViewport;
      const bottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
      list.style.setProperty("--suggest-from", `${form.getBoundingClientRect().bottom}px`);
      list.style.setProperty("--suggest-to", `${bottom}px`);
    }

    // The name with the typed query marked, built as text nodes - the names come from
    // the catalog, never as HTML.
    function marked(text, query) {
      const fragment = document.createDocumentFragment();
      const at = text.toLowerCase().indexOf(query.toLowerCase());
      if (at === -1) {
        fragment.append(text);
        return fragment;
      }
      const mark = document.createElement("mark");
      mark.textContent = text.slice(at, at + query.length);
      fragment.append(text.slice(0, at), mark, text.slice(at + query.length));
      return fragment;
    }

    function optionFor(item, query, i) {
      const link = document.createElement("a");
      link.className = "hm-suggest__item";
      link.id = `${listId}-${i}`;
      link.href = item.url;
      link.tabIndex = -1;
      link.setAttribute("role", "option");

      const cover = document.createElement("span");
      cover.className = "hm-suggest__cover";
      if (item.cover) {
        const img = document.createElement("img");
        img.src = item.cover;
        img.alt = "";
        img.loading = "lazy";
        cover.append(img);
      }

      const text = document.createElement("span");
      text.className = "hm-suggest__text";
      const name = document.createElement("span");
      name.className = "hm-suggest__name";
      name.append(marked(item.name, query));
      text.append(name);

      // When the query matched another of its names, that name says why it's here.
      const lower = query.toLowerCase();
      const alt = item.name.toLowerCase().includes(lower)
        ? null
        : item.alt_names.find((n) => n.toLowerCase().includes(lower));
      if (alt || item.meta) {
        const meta = document.createElement("span");
        meta.className = "hm-suggest__meta";
        if (alt) {
          const altName = document.createElement("span");
          altName.className = "hm-suggest__alt";
          altName.append(marked(alt, query));
          meta.append(altName);
        }
        if (alt && item.meta) meta.append(" · ");
        if (item.meta) meta.append(item.meta);
        text.append(meta);
      }

      link.append(cover, text);
      return link;
    }

    function allLink(url, i) {
      const link = document.createElement("a");
      link.className = "hm-suggest__all";
      link.id = `${listId}-${i}`;
      link.href = url;
      link.tabIndex = -1;
      link.setAttribute("role", "option");
      link.textContent = "Все результаты в каталоге →";
      return link;
    }

    function note(text) {
      const p = document.createElement("p");
      p.className = "hm-suggest__note";
      p.textContent = text;
      return p;
    }

    function showLoading() {
      options = [];
      active = -1;
      const rows = Array.from({ length: SKELETON_ROWS }, () => {
        const row = document.createElement("span");
        row.className = "hm-suggest__skeleton";
        row.setAttribute("aria-hidden", "true");
        row.innerHTML = '<span class="hm-suggest__cover wn-skeleton"></span><span class="hm-suggest__text"><span class="wn-skeleton"></span><span class="wn-skeleton"></span></span>';
        return row;
      });
      list.setAttribute("aria-busy", "true");
      list.replaceChildren(...rows);
      status.textContent = "Ищем…";
      setOpen(true);
    }

    function showResults(data) {
      list.removeAttribute("aria-busy");
      shownQuery = data.query;
      options = data.items.map((item, i) => optionFor(item, data.query, i));
      const nodes = [...options];
      if (options.length) {
        options.push(allLink(data.all_url, options.length));
        nodes.push(options[options.length - 1]);
        status.textContent = `Найдено тайтлов: ${data.items.length}. Стрелки вверх и вниз — выбор.`;
      } else {
        nodes.push(note("Ничего не нашлось"));
        status.textContent = "Ничего не нашлось";
      }
      active = -1;
      input.removeAttribute("aria-activedescendant");
      list.replaceChildren(...nodes);
      setOpen(true);
    }

    function showError() {
      list.removeAttribute("aria-busy");
      options = [];
      list.replaceChildren(note("Не удалось загрузить подсказки. Нажмите «Найти», чтобы открыть каталог."));
      status.textContent = "Не удалось загрузить подсказки";
      setOpen(true);
    }

    async function load(query) {
      if (controller) controller.abort();
      controller = new AbortController();
      const params = new URLSearchParams({ query, limit: "8" });
      try {
        const response = await fetch(`/catalog/suggest?${params}`, {
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        // A slower, older answer never replaces the one for what's in the field now.
        if (data.query !== input.value.trim()) return;
        showResults(data);
      } catch (error) {
        if (error.name !== "AbortError") showError();
      }
    }

    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      const query = input.value.trim();
      if (query.length < MIN_QUERY) {
        if (controller) controller.abort();
        shownQuery = "";
        status.textContent = "";
        setOpen(false);
        return;
      }
      if (query === shownQuery && !list.hidden) return;
      // The skeleton only when there's nothing to look at yet; titles already shown stay
      // until the next answer replaces them, so the list doesn't flicker with every key.
      if (list.hidden || !options.length) showLoading();
      else list.setAttribute("aria-busy", "true");
      timer = window.setTimeout(() => load(query), DEBOUNCE_MS);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (list.hidden) return;
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (list.hidden) {
          if (shownQuery && shownQuery === input.value.trim()) setOpen(true);
          return;
        }
        if (!options.length) return;
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        // Past either end the pick goes back to the field, as in a native list.
        let next = active + step;
        if (next >= options.length) next = -1;
        else if (next < -1) next = options.length - 1;
        setActive(next);
        return;
      }
      if (event.key === "Enter" && !list.hidden && options[active]) {
        event.preventDefault();
        window.location.assign(options[active].href);
      }
    });

    // Reopen what was there when the visitor comes back to the field.
    input.addEventListener("focus", () => {
      if (shownQuery && shownQuery === input.value.trim() && list.childElementCount) setOpen(true);
    });

    // A press inside the list must not blur the field first, or the list would close
    // before the click lands.
    list.addEventListener("pointerdown", (event) => event.preventDefault());
    list.addEventListener("pointermove", (event) => {
      const option = event.target.closest('[role="option"]');
      const at = options.indexOf(option);
      if (at !== -1 && at !== active) setActive(at);
    });

    document.addEventListener("pointerdown", (event) => {
      if (!form.contains(event.target)) setOpen(false);
    });
    form.addEventListener("focusout", (event) => {
      if (!form.contains(event.relatedTarget)) setOpen(false);
    });
    form.addEventListener("submit", () => setOpen(false));

    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", () => {
        if (!list.hidden) fitToViewport();
      });
    }
    window.addEventListener("resize", () => {
      if (!list.hidden) fitToViewport();
    });
  });
})();
