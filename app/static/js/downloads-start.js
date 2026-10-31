// «Скачать тайтл» on «Загрузки»: find a title (the catalog's own suggestions, GET
// /catalog/suggest - issue 394), or take one of the library's latest reads, or «Скачать
// снова» from the history; pick the format; «Скачать» starts the same background job the
// title page does (POST /titles/{slug}/download) and the page reloads with it under
// «Сейчас», where downloads-status.js follows its chapters. Without JS the field is the
// catalog's search and every pick is a link to its title page.
(() => {
  const root = document.querySelector('[data-role="download-start"]');
  if (!root) return;

  const searchForm = root.querySelector('[data-role="download-start-search"]');
  const query = root.querySelector('[data-role="download-start-query"]');
  const list = root.querySelector('[data-role="download-start-suggest"]');
  const form = root.querySelector('[data-role="download-start-form"]');
  const nameEl = form.querySelector('[data-role="download-start-name"]');
  const metaEl = form.querySelector('[data-role="download-start-meta"]');
  const coverEl = form.querySelector('[data-role="download-start-cover"]');
  const submit = form.querySelector('[data-role="download-start-submit"]');
  const error = form.querySelector('[data-role="download-start-error"]');
  const DEFAULT_META = metaEl.textContent;

  // --- the chosen title -----------------------------------------------------------------
  function choose({ slug, name, cover, meta, fmt }) {
    form.action = `/titles/${encodeURIComponent(slug)}/download`;
    nameEl.textContent = name;
    metaEl.textContent = meta ? `${meta} · все главы, в одном файле` : DEFAULT_META;
    coverEl.hidden = !cover;
    if (cover) coverEl.src = cover;
    if (fmt) {
      const radio = form.querySelector(`input[name="fmt"][value="${CSS.escape(fmt)}"]`);
      if (radio) radio.checked = true;
    }
    error.hidden = true;
    form.hidden = false;
    closeList();
    root.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    submit.focus({ preventScroll: true });
  }

  form.querySelector('[data-role="download-start-reset"]').addEventListener("click", () => {
    form.hidden = true;
    query.focus();
  });

  // Picks from the library and «Скачать снова» in the history choose instead of leaving.
  document.addEventListener("click", (event) => {
    const pick = event.target.closest('[data-role="download-pick"]');
    if (!pick || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    choose({ slug: pick.dataset.slug, name: pick.dataset.name, cover: pick.dataset.cover, fmt: pick.dataset.fmt });
  });

  // --- «Скачать» -------------------------------------------------------------------------
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    error.hidden = true;
    try {
      const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
      // A session that ran out lands on the login page - go there for real.
      if (response.redirected && new URL(response.url).pathname.startsWith("/login")) {
        location.href = response.url;
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      location.reload();
    } catch {
      error.textContent = "Не удалось начать загрузку. Попробуйте ещё раз.";
      error.hidden = false;
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
    }
  });

  // A job waiting for a translation: the choice resumes it in place (POST .../retry) and
  // the page comes back with it running.
  document.addEventListener("submit", async (event) => {
    const choice = event.target.closest('[data-role="download-translation-form"]');
    if (!choice) return;
    event.preventDefault();
    const button = choice.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch(choice.action, { method: "POST", body: new FormData(choice) });
      if (!response.ok) throw new Error(String(response.status));
    } catch {
      // The job may have moved on meanwhile - the reload shows where it is.
    }
    location.reload();
  });

  // --- the search ------------------------------------------------------------------------
  let items = [];
  let active = -1;
  let timer = null;
  let asked = "";

  function closeList() {
    list.hidden = true;
    query.setAttribute("aria-expanded", "false");
    query.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function highlight(index) {
    active = index;
    list.querySelectorAll('[role="option"]').forEach((option, i) => {
      option.setAttribute("aria-selected", String(i === index));
      if (i === index) {
        query.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });
  }

  function render(found) {
    items = found;
    list.replaceChildren();
    if (!found.length) {
      const empty = document.createElement("li");
      empty.className = "dl-suggest__empty";
      empty.textContent = "Ничего не нашлось";
      list.append(empty);
    }
    found.forEach((item, i) => {
      const option = document.createElement("li");
      option.id = `dl-suggest-${i}`;
      option.className = "dl-suggest__item";
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      const cover = document.createElement("span");
      cover.className = "dl-suggest__cover";
      if (item.cover) {
        const img = document.createElement("img");
        img.src = item.cover;
        img.alt = "";
        img.loading = "lazy";
        cover.append(img);
      }
      const text = document.createElement("span");
      text.className = "dl-suggest__text";
      const name = document.createElement("b");
      name.textContent = item.name;
      const meta = document.createElement("small");
      meta.textContent = item.meta || "";
      text.append(name, meta);
      option.append(cover, text);
      option.addEventListener("mousedown", (event) => event.preventDefault());
      option.addEventListener("click", () => pickItem(i));
      list.append(option);
    });
    list.hidden = false;
    query.setAttribute("aria-expanded", "true");
  }

  function pickItem(i) {
    const item = items[i];
    if (!item) return;
    choose({ slug: item.slug_url, name: item.name, cover: item.cover, meta: item.meta });
  }

  async function search(text) {
    asked = text;
    try {
      const response = await fetch(`/catalog/suggest?query=${encodeURIComponent(text)}&limit=6`);
      if (!response.ok) return;
      const data = await response.json();
      if (asked !== text) return; // a newer query is on its way
      render(data.items || []);
    } catch {
      // the search simply stays closed; Enter still goes to the catalog
    }
  }

  query.addEventListener("input", () => {
    clearTimeout(timer);
    const text = query.value.trim();
    if (text.length < 2) {
      asked = "";
      closeList();
      return;
    }
    timer = setTimeout(() => search(text), 220);
  });

  query.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      highlight((active + step + items.length) % items.length);
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      pickItem(active);
    } else if (event.key === "Escape") {
      closeList();
    }
  });

  query.addEventListener("blur", () => setTimeout(closeList, 120));
  query.addEventListener("focus", () => {
    if (items.length && query.value.trim().length >= 2) render(items);
  });
  searchForm.addEventListener("submit", (event) => {
    // Enter with nothing highlighted takes the first match, if there is one.
    if (items.length && !list.hidden) {
      event.preventDefault();
      pickItem(active >= 0 ? active : 0);
    }
  });
})();
