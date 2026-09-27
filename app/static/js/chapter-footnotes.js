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
