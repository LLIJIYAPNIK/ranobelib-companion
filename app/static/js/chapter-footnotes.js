// PR 239: Chapter.content (ranobelib-python-sdk) has no structured field for footnotes -
// translator "↑ term explanation" notes arrive as plain paragraphs mixed into the regular
// flow, indistinguishable by markup from actual chapter text, and often cluster right
// before the first real paragraph rather than after it - reading as noise ahead of the
// actual chapter instead of a chapter-end reference. TODO(#58): drop this heuristic once
// https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/58 lands a structured
// footnotes field - use that directly instead of pattern-matching translator prose.
//
// Detected here by the same leading-arrow ("↑ …") convention translators use, and
// regrouped into one collapsible block appended at the end of the chapter. Runs before
// tap-to-read.js/paragraph-menu.js (see chapter.html's script order) so both only ever see
// this already-reduced .reader-content shape: tap-to-read.js's own paragraph-by-paragraph
// reveal (`[...content.children]`) then treats the whole group as a single step instead
// of one step per footnote line, and paragraph-menu.js's position-based paragraph_index is
// only ever computed against this same final shape.
(() => {
  const content = document.querySelector('[data-role="chapter"]');
  if (!content) return;

  // PR 242: older HTML-format chapters list footnotes as <ol><li>↑ …</li></ol>, and the
  // SDK's content sanitizer drops <ol>/<li> but keeps their text - leaving every footnote
  // as one run of bare text at the top level of .reader-content, outside any element.
  // content.children (below, and tap-to-read.js/paragraph-menu.js) never sees text nodes,
  // so without this the whole run rendered as one unbroken, ungrouped line. Split such a
  // run back into one <p> per "↑" first, so it takes the same path as per-<p> footnotes.
  // TODO(#59): drop this once
  // https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/59 makes the sanitizer
  // wrap dropped block tags' text in <p> itself.
  const INLINE_TAGS = new Set(["STRONG", "EM", "BR"]);
  const isLoose = (node) =>
    node.nodeType === Node.TEXT_NODE ||
    (node.nodeType === Node.ELEMENT_NODE && INLINE_TAGS.has(node.tagName));

  const runs = [];
  let run = [];
  for (const node of content.childNodes) {
    if (isLoose(node)) {
      run.push(node);
    } else if (run.length > 0) {
      runs.push(run);
      run = [];
    }
  }
  if (run.length > 0) runs.push(run);

  for (const looseRun of runs) {
    const text = looseRun.map((node) => node.textContent).join("");
    if (!text.trim().startsWith("↑")) continue;

    const next = looseRun[looseRun.length - 1].nextSibling;
    const fragment = document.createDocumentFragment();
    let paragraph = null;
    for (const node of looseRun) {
      if (node.nodeType === Node.TEXT_NODE) {
        // Lookahead split keeps each "↑" at the start of the piece it opens.
        for (const piece of node.data.split(/(?=↑)/)) {
          if (piece.startsWith("↑")) {
            paragraph = document.createElement("p");
            fragment.append(paragraph);
          }
          // Anything before the first "↑" is whitespace only (the run's trimmed text
          // starts with the arrow) - nothing to keep.
          if (paragraph) paragraph.append(piece);
        }
        node.remove();
      } else if (paragraph) {
        paragraph.append(node);
      } else {
        node.remove();
      }
    }
    content.insertBefore(fragment, next);
  }

  const isFootnote = (el) => el.textContent.trim().startsWith("↑");
  const footnotes = [...content.children].filter(isFootnote);
  if (footnotes.length === 0) return;

  const details = document.createElement("details");
  details.className = "reader-footnotes";
  details.dataset.role = "reader-footnotes";

  const summary = document.createElement("summary");
  summary.className = "reader-footnotes__summary";
  summary.textContent = "Сноски";
  details.append(summary);

  // append() moves each node from its original position in content.children - no
  // separate removal step needed.
  for (const footnote of footnotes) details.append(footnote);

  content.append(details);
})();
