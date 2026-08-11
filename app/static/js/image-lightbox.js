// Fullscreen image viewer for chapter images (PR 66): .reader-content img (PR 6) opens
// only inline in the text flow by default - clicking one instead opens a fullscreen
// overlay with zoom, a download link, and prev/next arrows cycling through every image
// in the chapter without closing the viewer.
//
// Disabled entirely when tap-to-read (PR 62-65) is on: tapping anywhere in the reading
// area there already means "reveal the next paragraph" (tap-to-read.js's own click
// listener on .reader-content) - opening a lightbox on the same tap would conflict with
// that mental model, so images just stay plain inline pictures in that mode, exactly as
// the roadmap calls for.
(() => {
  function isTapToReadEnabled() {
    try {
      return JSON.parse(localStorage.getItem("readerSettings") || "{}").tapToRead === true;
    } catch {
      return false;
    }
  }

  if (isTapToReadEnabled()) return;

  const images = [...document.querySelectorAll(".reader-content img")];
  if (images.length === 0) return;

  const MIN_ZOOM = 1;
  const MAX_ZOOM = 3;
  const ZOOM_STEP = 0.5;

  let currentIndex = 0;
  let zoom = MIN_ZOOM;

  const overlay = document.createElement("div");
  overlay.className = "image-lightbox";
  overlay.innerHTML = `
    <button type="button" class="image-lightbox__close" aria-label="Закрыть">&times;</button>
    <button type="button" class="image-lightbox__nav image-lightbox__nav--prev" aria-label="Предыдущее изображение">&#8249;</button>
    <div class="image-lightbox__viewport">
      <img class="image-lightbox__image" alt="">
    </div>
    <button type="button" class="image-lightbox__nav image-lightbox__nav--next" aria-label="Следующее изображение">&#8250;</button>
    <div class="image-lightbox__toolbar">
      <button type="button" class="image-lightbox__zoom-out" aria-label="Уменьшить">&minus;</button>
      <span class="image-lightbox__counter"></span>
      <button type="button" class="image-lightbox__zoom-in" aria-label="Увеличить">+</button>
      <a class="image-lightbox__download" download aria-label="Скачать изображение">&#8595;</a>
    </div>
  `;
  document.body.appendChild(overlay);

  const imageEl = overlay.querySelector(".image-lightbox__image");
  const counterEl = overlay.querySelector(".image-lightbox__counter");
  const downloadEl = overlay.querySelector(".image-lightbox__download");
  const prevBtn = overlay.querySelector(".image-lightbox__nav--prev");
  const nextBtn = overlay.querySelector(".image-lightbox__nav--next");
  const closeBtn = overlay.querySelector(".image-lightbox__close");
  const zoomInBtn = overlay.querySelector(".image-lightbox__zoom-in");
  const zoomOutBtn = overlay.querySelector(".image-lightbox__zoom-out");

  function isOpen() {
    return overlay.classList.contains("image-lightbox--open");
  }

  function render() {
    const src = images[currentIndex].currentSrc || images[currentIndex].src;
    imageEl.src = src;
    imageEl.alt = images[currentIndex].alt || "";
    imageEl.style.transform = `scale(${zoom})`;
    downloadEl.href = src;
    // A single image doesn't need "1 / 1" or arrows to get anywhere.
    const multiple = images.length > 1;
    counterEl.textContent = multiple ? `${currentIndex + 1} / ${images.length}` : "";
    prevBtn.hidden = !multiple;
    nextBtn.hidden = !multiple;
  }

  function open(index) {
    currentIndex = index;
    zoom = MIN_ZOOM;
    render();
    overlay.classList.add("image-lightbox--open");
  }

  function close() {
    overlay.classList.remove("image-lightbox--open");
  }

  function step(delta) {
    if (images.length < 2) return;
    currentIndex = (currentIndex + delta + images.length) % images.length;
    zoom = MIN_ZOOM;
    render();
  }

  function zoomBy(delta) {
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom + delta));
    render();
  }

  images.forEach((img, index) => {
    img.style.cursor = "zoom-in";
    img.addEventListener("click", () => open(index));
  });

  closeBtn.addEventListener("click", close);
  // Clicking the backdrop itself (not any child control) also closes it.
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  prevBtn.addEventListener("click", () => step(-1));
  nextBtn.addEventListener("click", () => step(1));
  zoomInBtn.addEventListener("click", () => zoomBy(ZOOM_STEP));
  zoomOutBtn.addEventListener("click", () => zoomBy(-ZOOM_STEP));

  document.addEventListener("keydown", (event) => {
    if (!isOpen()) return;
    if (event.key === "Escape") close();
    else if (event.key === "ArrowLeft") step(-1);
    else if (event.key === "ArrowRight") step(1);
  });
})();
