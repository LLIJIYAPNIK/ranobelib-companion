// Custom listbox dropdown (PR 54) - no browser lets CSS restyle a native <select>'s open
// option list, so this replaces it with a fully custom `role="listbox"` popup (the
// "Collapsible Dropdown Listbox" pattern from the WAI-ARIA Authoring Practices) driven by
// a `<button>` trigger. The original <select> stays in the DOM, hidden but fully live -
// its .value/.selectedIndex is what this script updates and reads, and it's still what a
// <form> submits - so every existing behavior that reads that value (form submission) or
// listens for "input"/"change" on it (app/static/js/reader-settings.js) keeps working
// completely unmodified. Without this script, the plain <select> stays visible and fully
// functional - the safe no-JS fallback used throughout this app.
(() => {
  let counter = 0;

  function accessibleName(select) {
    const ariaLabel = select.getAttribute("aria-label");
    if (ariaLabel) return ariaLabel;
    const label = select.closest("label");
    if (!label) return "";
    // Only the text *before* the select in the label - the label also wraps the select
    // itself (and its <option> text), which would otherwise leak into the name.
    let text = "";
    for (const node of label.childNodes) {
      if (node === select) break;
      text += node.textContent || "";
    }
    return text.trim();
  }

  function enhance(select) {
    const id = `dropdown-${++counter}`;
    const name = accessibleName(select);

    const wrapper = document.createElement("div");
    wrapper.className = "dropdown";
    select.insertAdjacentElement("beforebegin", wrapper);
    wrapper.appendChild(select);
    select.classList.add("dropdown__native");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "toc__export-format dropdown__trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    if (name) trigger.setAttribute("aria-label", name);
    wrapper.appendChild(trigger);

    const listbox = document.createElement("ul");
    listbox.className = "dropdown__listbox";
    listbox.setAttribute("role", "listbox");
    listbox.tabIndex = -1;
    if (name) listbox.setAttribute("aria-label", name);
    wrapper.appendChild(listbox);

    const items = [...select.options].map((option, index) => {
      const li = document.createElement("li");
      li.className = "dropdown__option";
      li.setAttribute("role", "option");
      li.id = `${id}-option-${index}`;
      li.textContent = option.textContent;
      li.addEventListener("mouseenter", () => setActive(index));
      li.addEventListener("click", () => {
        choose(index);
        close();
      });
      listbox.appendChild(li);
      return li;
    });

    let activeIndex = Math.max(0, select.selectedIndex);

    function syncTrigger() {
      trigger.textContent = select.options[select.selectedIndex]?.textContent ?? "";
    }

    function syncSelected() {
      items.forEach((li, index) => {
        const selected = index === select.selectedIndex;
        li.setAttribute("aria-selected", String(selected));
        li.classList.toggle("dropdown__option--selected", selected);
      });
    }

    function setActive(index) {
      activeIndex = Math.max(0, Math.min(items.length - 1, index));
      items.forEach((li, i) => li.classList.toggle("dropdown__option--active", i === activeIndex));
      listbox.setAttribute("aria-activedescendant", items[activeIndex].id);
      items[activeIndex].scrollIntoView({ block: "nearest" });
    }

    function open() {
      wrapper.classList.add("dropdown--open");
      trigger.setAttribute("aria-expanded", "true");
      setActive(select.selectedIndex >= 0 ? select.selectedIndex : 0);
      listbox.focus();
    }

    function close(refocusTrigger = true) {
      wrapper.classList.remove("dropdown--open");
      trigger.setAttribute("aria-expanded", "false");
      if (refocusTrigger) trigger.focus();
    }

    function isOpen() {
      return wrapper.classList.contains("dropdown--open");
    }

    function choose(index) {
      if (select.selectedIndex !== index) {
        select.selectedIndex = index;
        select.dispatchEvent(new Event("input", { bubbles: true }));
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      syncTrigger();
      syncSelected();
    }

    trigger.addEventListener("click", () => (isOpen() ? close() : open()));

    trigger.addEventListener("keydown", (event) => {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
        event.preventDefault();
        open();
      }
    });

    let typeahead = "";
    let typeaheadTimer;

    listbox.addEventListener("keydown", (event) => {
      switch (event.key) {
        case "ArrowDown":
          event.preventDefault();
          setActive(activeIndex + 1);
          break;
        case "ArrowUp":
          event.preventDefault();
          setActive(activeIndex - 1);
          break;
        case "Home":
          event.preventDefault();
          setActive(0);
          break;
        case "End":
          event.preventDefault();
          setActive(items.length - 1);
          break;
        case "Enter":
        case " ":
          event.preventDefault();
          choose(activeIndex);
          close();
          break;
        case "Escape":
          event.preventDefault();
          close();
          break;
        case "Tab":
          close(false);
          break;
        default:
          if (event.key.length === 1 && /\S/.test(event.key)) {
            typeahead += event.key.toLowerCase();
            clearTimeout(typeaheadTimer);
            typeaheadTimer = setTimeout(() => (typeahead = ""), 500);
            const startingFrom = items.findIndex(
              (li, i) => i > activeIndex && li.textContent.toLowerCase().startsWith(typeahead)
            );
            const fromStart = items.findIndex((li) =>
              li.textContent.toLowerCase().startsWith(typeahead)
            );
            const target = startingFrom !== -1 ? startingFrom : fromStart;
            if (target !== -1) setActive(target);
          }
      }
    });

    document.addEventListener("click", (event) => {
      if (isOpen() && !wrapper.contains(event.target)) close(false);
    });

    // Keeps the trigger in sync if something else changes select.value programmatically.
    select.addEventListener("change", () => {
      syncTrigger();
      syncSelected();
    });

    syncTrigger();
    syncSelected();
  }

  document.querySelectorAll("select.toc__export-format").forEach(enhance);
})();
