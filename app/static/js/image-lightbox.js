// Fullscreen image viewer for chapter images (PR 66): .reader-content img (PR 6) opens
// only inline in the text flow by default - clicking one instead opens a fullscreen
// overlay with zoom, a download link, and prev/next arrows cycling through every image
// in the chapter without closing the viewer.
//
// PR 74 (revisits PR 66): tap-to-read (PR 62-65) tapping anywhere in the reading area
// means "reveal the next paragraph" (tap-to-read.js's own click listener, widened to the
// whole reading area in PR 73), which conflicts with opening a lightbox on that same tap
// - PR 66 resolved that by disabling this script entirely whenever tap-to-read was on, so
// images just stayed plain inline pictures in that mode. That threw out image viewing
// altogether instead of just the conflict: a tap specifically on an image should still
// open the lightbox, same as normal reading - it's only tap-to-read.js's own handler that
// now excludes clicks on <img> (see the "a, button, img" check there), so the two no
// longer compete for the same tap.
//
// PR 142: also opens for a title's own cover (.title-hero__cover), on the title page
// itself (title.html) and inside the quick-view modal (_title_quickview.html, PR 117) -
// as a one-image "gallery" (`multiple` below is already just `activeImages.length > 1`,
// so a single cover naturally gets no counter/arrows without any extra branching). The
// reader-content gallery above is built once, eagerly, since chapter.html renders every
// image up front - a title cover can't be handled the same way, because the quick-view
// modal's own cover only exists once title-quickview.js has fetched and injected that
// fragment, well after this script's own querySelectorAll would have already run. A
// delegated document-level click listener sidesteps that: it matches whichever
// .title-hero__cover is under the click at the moment of the click, static or freshly
// injected alike, rather than a list snapshotted at load. `activeImages` tracks whichever
// gallery (the full reader-content list, or a synthetic one-element list for a cover) is
// currently open, so render()/step() don't need to know which case they're in.
//
// PR 154: also opens for a comment's own image attachment (img.paragraph-comment__attachment,
// paragraph-menu.js) - the same delegated-listener reasoning as the title cover above
// applies even more directly here, since comment threads load lazily via fetch() well
// after this script's own querySelectorAll has already run, and can keep loading more
// (replies, newly posted comments) for as long as the page stays open. Scoped to `img`
// specifically: video/gif attachments share the same class (PR 150/151) but open inline
// with their own controls instead, not this viewer.
(() => {
  const images = [...document.querySelectorAll(".reader-content img")];

  const MIN_ZOOM = 1;
  const MAX_ZOOM = 3;
  const ZOOM_STEP = 0.5;

  let activeImages = images;
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

  function isSameOrigin(src) {
    try {
      return new URL(src, window.location.href).origin === window.location.origin;
    } catch {
      return false;
    }
  }

  function render() {
    const src = activeImages[currentIndex].currentSrc || activeImages[currentIndex].src;
    imageEl.src = src;
    imageEl.alt = activeImages[currentIndex].alt || "";
    imageEl.style.transform = `scale(${zoom})`;
    // PR 143: routed through a same-origin proxy (GET /images/download, app/api/
    // images.py) rather than linking `src` directly - reader-content/cover images are
    // hotlinked straight from ranobelib.me/cdnlibs.org, and the download attribute only
    // works cross-origin if the target's own CORS headers happen to allow it, which is
    // outside this app's control.
    //
    // PR 154: a comment attachment (app/comment_attachment.py) is the opposite case -
    // already same-origin (served from this app's own /comment-attachments/ route, not
    // hotlinked), so `download` already works on it directly, and images.py's proxy would
    // actively reject it: `_is_allowed_image_url` only allows ranobelib.me/cdnlibs.org,
    // by design, to keep the endpoint from being usable as an open relay for arbitrary
    // URLs (see that file's own docstring).
    downloadEl.href = isSameOrigin(src)
      ? src
      : `/images/download?url=${encodeURIComponent(src)}`;
    // A single image doesn't need "1 / 1" or arrows to get anywhere - true for a lone
    // reader-content image same as for a title cover, which is always a one-element list.
    const multiple = activeImages.length > 1;
    counterEl.textContent = multiple ? `${currentIndex + 1} / ${activeImages.length}` : "";
    prevBtn.hidden = !multiple;
    nextBtn.hidden = !multiple;
  }

  function open(list, index) {
    activeImages = list;
    currentIndex = index;
    zoom = MIN_ZOOM;
    render();
    overlay.classList.add("image-lightbox--open");
  }

  function close() {
    overlay.classList.remove("image-lightbox--open");
  }

  function step(delta) {
    if (activeImages.length < 2) return;
    currentIndex = (currentIndex + delta + activeImages.length) % activeImages.length;
    zoom = MIN_ZOOM;
    render();
  }

  function zoomBy(delta) {
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom + delta));
    render();
  }

  images.forEach((img, index) => {
    img.style.cursor = "zoom-in";
    img.addEventListener("click", () => open(images, index));
  });

  // Delegated (not a per-element listener like the reader-content images above) because
  // neither a title cover nor a comment attachment is reliably in the DOM yet when this
  // script runs - see the file-top comment. cursor: zoom-in for both lives in app.css
  // rather than being set here for the same reason: this listener may fire against an
  // element injected long after load, so there's no single "attach time" to set an
  // inline style at.
  document.addEventListener("click", (event) => {
    const cover = event.target.closest(".title-hero__cover");
    if (cover) {
      open([cover], 0);
      return;
    }
    const attachment = event.target.closest("img.paragraph-comment__attachment");
    if (attachment) open([attachment], 0);
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
