// PR 300 (LibraryMobile.dc.html): the library's phone-only touches.
//
// The add-by-link field says just «Ссылка на тайтл» on phones - the desktop placeholder
// («… ranobelib.me») doesn't fit beside «Добавить» and would be cut off mid-word.
(() => {
  const phone = window.matchMedia("(max-width: 767px)");
  const field = document.querySelector("[data-placeholder-short]");
  if (field) {
    const full = field.placeholder;
    const sync = () => {
      field.placeholder = phone.matches ? field.dataset.placeholderShort : full;
    };
    sync();
    phone.addEventListener("change", sync);
  }
})();
