"""EPUB 2/3 validation, metadata/TOC/chapter extraction, and strict HTML sanitization.

Isolated from ``ranobelib`` SDK (issue 432): user-uploaded books are private and do not
depend on ranobelib.me. Uses Python stdlib (``zipfile``, ``xml.etree.ElementTree``,
``html.parser``) plus ``nh3`` for defense-in-depth HTML sanitization.
"""

from __future__ import annotations

import html as html_lib
import posixpath
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import nh3

from app.image_sniff import sniff_raster_image

# Archive & extraction safety limits (ZIP-bomb / DoS protection).
MAX_ZIP_ENTRIES = 5000
MAX_SINGLE_ENTRY_BYTES = 25 * 1024 * 1024  # 25 MB uncompressed per entry
MAX_TOTAL_EXTRACTED_BYTES = 200 * 1024 * 1024  # 200 MB uncompressed total
MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ASSET_BYTES = 10 * 1024 * 1024  # 10 MB per image
MAX_TOTAL_ASSETS_BYTES = 100 * 1024 * 1024  # 100 MB across all chapter images
MAX_CHAPTERS = 5000

MAX_TITLE_LENGTH = 300
MAX_AUTHOR_LENGTH = 200
MAX_AUTHORS_COUNT = 20
MAX_DESCRIPTION_LENGTH = 10_000
MAX_CHAPTER_TITLE_LENGTH = 300

_EPUB_MIMETYPE = b"application/epub+zip"
_ENTITY_DECL_RE = re.compile(rb"<!\s*ENTITY\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

_HTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html"}

# Tags whose entire subtree (opening tag through matching closing tag) is discarded.
_DROP_SUBTREE_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "embed",
        "applet",
        "math",
        "form",
        "input",
        "button",
        "select",
        "textarea",
        "link",
        "meta",
        "base",
        "template",
        "head",
    }
)

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)

_ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "del",
    "sub",
    "sup",
    "blockquote",
    "pre",
    "code",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "figure",
    "figcaption",
    "img",
    "a",
    "span",
    "div",
    "section",
    "aside",
    "small",
    "mark",
    "ruby",
    "rt",
    "rp",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "img": {"src", "alt", "title"},
    "a": {"href", "title"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}

_ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}


class EpubValidationError(Exception):
    """Raised when an uploaded file is not a valid or safe EPUB. Message is user-facing."""


@dataclass(frozen=True)
class ExtractedCover:
    data: bytes
    media_type: str
    extension: str


@dataclass(frozen=True)
class ExtractedAsset:
    asset_id: uuid.UUID
    source_href: str
    data: bytes
    media_type: str
    extension: str


@dataclass(frozen=True)
class ExtractedChapter:
    position: int
    title: str
    content: str
    source_href: str


@dataclass(frozen=True)
class ParsedEpub:
    title: str
    authors: list[str]
    description: str | None
    cover: ExtractedCover | None
    chapters: list[ExtractedChapter]
    assets: list[ExtractedAsset]


@dataclass(frozen=True)
class _ManifestItem:
    item_id: str
    href: str  # normalized archive path relative to ZIP root
    media_type: str
    properties: frozenset[str]


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _clean_text(value: str | None, *, max_length: int) -> str:
    if not value:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", value).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned


def _strip_html_to_text(raw: str) -> str:
    """Strips HTML markup from EPUB dc:description into clean plain text."""
    text = nh3.clean(raw, tags=set())
    return html_lib.unescape(text)


def _normalize_archive_path(path: str) -> str | None:
    """Normalizes a POSIX path inside a ZIP archive; returns None if it escapes root."""
    unquoted = unquote(path).replace("\\", "/").strip()
    if not unquoted or unquoted.startswith("/"):
        return None
    normalized = posixpath.normpath(unquoted)
    if normalized in ("", ".", "..") or normalized.startswith("../") or normalized.startswith("/"):
        return None
    return normalized


def _resolve_relative_path(base_file: str, rel_href: str) -> str | None:
    """Resolves ``rel_href`` relative to ``base_file`` inside the EPUB ZIP archive."""
    href_no_frag = urlsplit(rel_href).path
    if not href_no_frag:
        return None
    unquoted = unquote(href_no_frag).replace("\\", "/").strip()
    if not unquoted or unquoted.startswith("/"):
        return None
    base_dir = posixpath.dirname(base_file)
    joined = posixpath.join(base_dir, unquoted) if base_dir else unquoted
    normalized = posixpath.normpath(joined)
    if normalized in ("", ".", "..") or normalized.startswith("../") or normalized.startswith("/"):
        return None
    return normalized


def _parse_xml_safe(data: bytes, label: str) -> ET.Element:
    """Parses XML bytes while rejecting entity declarations (billion-laughs / XXE defense)."""
    if _ENTITY_DECL_RE.search(data):
        raise EpubValidationError(f"Недопустимые XML-конструкции в файле EPUB ({label})")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise EpubValidationError(f"Повреждённая XML-структура EPUB ({label})") from exc


class _SafeArchive:
    """Wrapper around ``ZipFile`` enforcing encryption, path, and decompression budgets."""

    def __init__(self, zf: zipfile.ZipFile) -> None:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise EpubValidationError("Архив EPUB содержит слишком много файлов")
        self._zf = zf
        self._by_path: dict[str, zipfile.ZipInfo] = {}
        self._total_read = 0
        for info in infos:
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise EpubValidationError("Зашифрованные (DRM) файлы EPUB не поддерживаются")
            normalized = _normalize_archive_path(info.filename)
            if normalized is None:
                raise EpubValidationError("Некорректные пути файлов внутри архива EPUB")
            if info.file_size > MAX_SINGLE_ENTRY_BYTES:
                raise EpubValidationError("Слишком большой распакованный файл внутри архива EPUB")
            self._by_path[normalized] = info

    def exists(self, path: str) -> bool:
        return path in self._by_path

    def read(self, path: str, *, max_bytes: int = MAX_SINGLE_ENTRY_BYTES) -> bytes:
        info = self._by_path.get(path)
        if info is None:
            raise KeyError(path)
        if info.file_size > max_bytes:
            raise EpubValidationError("Слишком большой файл внутри архива EPUB")
        chunks: list[bytes] = []
        read_so_far = 0
        with self._zf.open(info, "r") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                read_so_far += len(chunk)
                self._total_read += len(chunk)
                if read_so_far > max_bytes or self._total_read > MAX_TOTAL_EXTRACTED_BYTES:
                    raise EpubValidationError("Превышен лимит распакованного размера EPUB")
                chunks.append(chunk)
        return b"".join(chunks)


def _find_opf_path(archive: _SafeArchive) -> str:
    if not archive.exists("META-INF/container.xml"):
        raise EpubValidationError("Файл не является корректным EPUB (нет META-INF/container.xml)")
    root = _parse_xml_safe(archive.read("META-INF/container.xml"), "container.xml")
    candidates: list[str] = []
    for elem in root.iter():
        if _local_name(elem.tag) == "rootfile":
            full_path = elem.attrib.get("full-path", "").strip()
            media_type = elem.attrib.get("media-type", "").strip().lower()
            if full_path:
                if media_type == "application/oebps-package+xml":
                    candidates.insert(0, full_path)
                else:
                    candidates.append(full_path)
    for raw_path in candidates:
        normalized = _normalize_archive_path(raw_path)
        if normalized and archive.exists(normalized):
            return normalized
    raise EpubValidationError("Файл не является корректным EPUB (не найден OPF-пакет)")


def _parse_opf(
    archive: _SafeArchive, opf_path: str, fallback_title: str
) -> tuple[
    str,
    list[str],
    str | None,
    dict[str, _ManifestItem],
    list[tuple[str, bool]],
    str | None,
    str | None,
]:
    root = _parse_xml_safe(archive.read(opf_path), "OPF")
    titles: list[str] = []
    authors: list[str] = []
    descriptions: list[str] = []
    meta_cover_id: str | None = None

    manifest: dict[str, _ManifestItem] = {}
    spine_refs: list[tuple[str, bool]] = []
    spine_toc_id: str | None = None

    for child in root:
        name = _local_name(child.tag)
        if name == "metadata":
            for elem in child.iter():
                ename = _local_name(elem.tag)
                text = "".join(elem.itertext()).strip()
                if ename == "title" and text:
                    titles.append(text)
                elif ename == "creator" and text:
                    authors.append(text)
                elif ename == "description" and text:
                    descriptions.append(text)
                elif ename == "meta":
                    if elem.attrib.get("name", "").lower() == "cover":
                        content = elem.attrib.get("content", "").strip()
                        if content:
                            meta_cover_id = content
        elif name == "manifest":
            for elem in child:
                if _local_name(elem.tag) != "item":
                    continue
                item_id = elem.attrib.get("id", "").strip()
                href_raw = elem.attrib.get("href", "").strip()
                media_type = elem.attrib.get("media-type", "").strip().lower()
                props = frozenset(elem.attrib.get("properties", "").split())
                resolved = _resolve_relative_path(opf_path, href_raw)
                if item_id and resolved:
                    manifest[item_id] = _ManifestItem(
                        item_id=item_id,
                        href=resolved,
                        media_type=media_type,
                        properties=props,
                    )
        elif name == "spine":
            spine_toc_id = elem_toc if (elem_toc := child.attrib.get("toc", "").strip()) else None
            for elem in child:
                if _local_name(elem.tag) != "itemref":
                    continue
                idref = elem.attrib.get("idref", "").strip()
                linear = elem.attrib.get("linear", "yes").strip().lower() != "no"
                if idref:
                    spine_refs.append((idref, linear))

    if not manifest or not spine_refs:
        raise EpubValidationError("В EPUB отсутствуют manifest или spine")

    book_title = _clean_text(titles[0], max_length=MAX_TITLE_LENGTH) if titles else ""
    if not book_title:
        book_title = _clean_text(fallback_title, max_length=MAX_TITLE_LENGTH) or "Без названия"

    deduped_authors: list[str] = []
    seen_authors: set[str] = set()
    for raw_author in authors:
        cleaned = _clean_text(raw_author, max_length=MAX_AUTHOR_LENGTH)
        if cleaned and cleaned.lower() not in seen_authors:
            seen_authors.add(cleaned.lower())
            deduped_authors.append(cleaned)
            if len(deduped_authors) >= MAX_AUTHORS_COUNT:
                break

    description: str | None = None
    if descriptions:
        raw_desc = "\n\n".join(_strip_html_to_text(d).strip() for d in descriptions if d.strip())
        cleaned_desc = raw_desc.strip()[:MAX_DESCRIPTION_LENGTH].rstrip()
        if cleaned_desc:
            description = cleaned_desc

    return (
        book_title,
        deduped_authors,
        description,
        manifest,
        spine_refs,
        meta_cover_id,
        spine_toc_id,
    )


def _extract_cover(
    archive: _SafeArchive,
    manifest: dict[str, _ManifestItem],
    meta_cover_id: str | None,
) -> ExtractedCover | None:
    candidates: list[_ManifestItem] = []

    # 1. EPUB 3 cover-image property
    for item in manifest.values():
        if "cover-image" in item.properties:
            candidates.append(item)

    # 2. EPUB 2 <meta name="cover" content="..."/>
    if meta_cover_id:
        if meta_cover_id in manifest:
            candidates.append(manifest[meta_cover_id])
        else:
            for item in manifest.values():
                if item.href.endswith(f"/{meta_cover_id}") or item.href == meta_cover_id:
                    candidates.append(item)

    # 3. Fallback: image manifest item mentioning "cover" in id or basename
    for item in manifest.values():
        if item.media_type.startswith("image/") and item.media_type != "image/svg+xml":
            basename = posixpath.basename(item.href).lower()
            if "cover" in item.item_id.lower() or "cover" in basename:
                candidates.append(item)

    seen_hrefs: set[str] = set()
    for item in candidates:
        if item.href in seen_hrefs or not archive.exists(item.href):
            continue
        seen_hrefs.add(item.href)
        try:
            raw = archive.read(item.href, max_bytes=MAX_COVER_BYTES)
        except EpubValidationError:
            continue
        sniffed = sniff_raster_image(raw)
        if sniffed is not None:
            media_type, ext = sniffed
            return ExtractedCover(data=raw, media_type=media_type, extension=ext)
    return None


def _parse_nav_toc(archive: _SafeArchive, nav_path: str) -> dict[str, str]:
    """Extracts href -> title mapping from an EPUB 3 Navigation Document (nav.xhtml)."""
    toc: dict[str, str] = {}
    if not archive.exists(nav_path):
        return toc
    try:
        raw = archive.read(nav_path).decode("utf-8", errors="replace")
    except EpubValidationError:
        return toc

    class _NavParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.in_toc_nav = False
            self.nav_depth = 0
            self.current_href: str | None = None
            self.text_parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            t = tag.lower()
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            if t == "nav":
                if self.in_toc_nav:
                    self.nav_depth += 1
                else:
                    epub_type = attr_map.get("epub:type", "") or attr_map.get("role", "")
                    tokens = {tok.lower() for tok in epub_type.split()}
                    if "toc" in tokens or "doc-toc" in tokens:
                        self.in_toc_nav = True
                        self.nav_depth = 1
            elif self.in_toc_nav and t == "a":
                href = attr_map.get("href", "").strip()
                if href:
                    self.current_href = href
                    self.text_parts = []

        def handle_endtag(self, tag: str) -> None:
            t = tag.lower()
            if t == "nav" and self.in_toc_nav:
                self.nav_depth -= 1
                if self.nav_depth <= 0:
                    self.in_toc_nav = False
            elif t == "a" and self.current_href is not None:
                resolved = _resolve_relative_path(nav_path, self.current_href)
                label = _clean_text("".join(self.text_parts), max_length=MAX_CHAPTER_TITLE_LENGTH)
                if resolved and label and resolved not in toc:
                    toc[resolved] = label
                self.current_href = None
                self.text_parts = []

        def handle_data(self, data: str) -> None:
            if self.current_href is not None:
                self.text_parts.append(data)

    parser = _NavParser()
    parser.feed(raw)
    parser.close()
    return toc


def _parse_ncx_toc(archive: _SafeArchive, ncx_path: str) -> dict[str, str]:
    """Extracts href -> title mapping from an EPUB 2 NCX table of contents."""
    toc: dict[str, str] = {}
    if not archive.exists(ncx_path):
        return toc
    try:
        root = _parse_xml_safe(archive.read(ncx_path), "toc.ncx")
    except EpubValidationError:
        return toc

    for elem in root.iter():
        if _local_name(elem.tag) != "navPoint":
            continue
        label_text = ""
        src_href = ""
        for child in elem:
            cname = _local_name(child.tag)
            if cname == "navLabel":
                for sub in child:
                    if _local_name(sub.tag) == "text" and sub.text:
                        label_text = sub.text
                        break
            elif cname == "content":
                src_href = child.attrib.get("src", "").strip()
        if src_href and label_text:
            resolved = _resolve_relative_path(ncx_path, src_href)
            cleaned = _clean_text(label_text, max_length=MAX_CHAPTER_TITLE_LENGTH)
            if resolved and cleaned and resolved not in toc:
                toc[resolved] = cleaned
    return toc


def _extract_toc_titles(
    archive: _SafeArchive,
    manifest: dict[str, _ManifestItem],
    spine_toc_id: str | None,
) -> tuple[dict[str, str], set[str]]:
    toc: dict[str, str] = {}
    nav_hrefs: set[str] = set()

    # EPUB 3 nav document takes precedence.
    for item in manifest.values():
        if "nav" in item.properties:
            nav_hrefs.add(item.href)
            toc.update(_parse_nav_toc(archive, item.href))

    # EPUB 2 NCX fills any gaps.
    ncx_item: _ManifestItem | None = None
    if spine_toc_id and spine_toc_id in manifest:
        ncx_item = manifest[spine_toc_id]
    else:
        for item in manifest.values():
            if item.media_type == "application/x-dtbncx+xml":
                ncx_item = item
                break
    if ncx_item is not None:
        for href, label in _parse_ncx_toc(archive, ncx_item.href).items():
            toc.setdefault(href, label)

    return toc, nav_hrefs


class _AssetCollector:
    """Deduplicates and validates raster images referenced across an EPUB's chapters."""

    def __init__(self, archive: _SafeArchive, title_id: uuid.UUID) -> None:
        self._archive = archive
        self._title_id = title_id
        self._by_href: dict[str, ExtractedAsset] = {}
        self._total_bytes = 0

    @property
    def assets(self) -> list[ExtractedAsset]:
        return list(self._by_href.values())

    def collect(self, chapter_href: str, raw_src: str) -> str | None:
        """Resolves a local image reference, sniffs magic bytes, and returns its safe URL."""
        if not raw_src:
            return None
        parsed = urlsplit(raw_src.strip())
        # Reject external URLs, data: URLs, javascript:, etc.
        if parsed.scheme or parsed.netloc:
            return None
        resolved = _resolve_relative_path(chapter_href, raw_src)
        if not resolved or not self._archive.exists(resolved):
            return None
        existing = self._by_href.get(resolved)
        if existing is not None:
            return f"/my-titles/{self._title_id}/assets/{existing.asset_id}"
        try:
            data = self._archive.read(resolved, max_bytes=MAX_ASSET_BYTES)
        except EpubValidationError:
            return None
        if not data or self._total_bytes + len(data) > MAX_TOTAL_ASSETS_BYTES:
            return None
        sniffed = sniff_raster_image(data)
        if sniffed is None:
            return None
        media_type, ext = sniffed
        asset = ExtractedAsset(
            asset_id=uuid.uuid4(),
            source_href=resolved,
            data=data,
            media_type=media_type,
            extension=ext,
        )
        self._by_href[resolved] = asset
        self._total_bytes += len(data)
        return f"/my-titles/{self._title_id}/assets/{asset.asset_id}"


class _ChapterHtmlRewriter(HTMLParser):
    """Extracts body HTML, strips active content/CSS/SVG, and rewrites local links/images."""

    def __init__(
        self,
        *,
        chapter_href: str,
        title_id: uuid.UUID,
        href_to_position: dict[str, int],
        assets: _AssetCollector,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self._chapter_href = chapter_href
        self._title_id = title_id
        self._href_to_position = href_to_position
        self._assets = assets

        self._has_body = False
        self._in_body = False
        self._in_title = False
        self._doc_title_parts: list[str] = []
        self._heading_tag: str | None = None
        self._first_heading_parts: list[str] = []
        self._captured_heading = False

        self._drop_stack: list[str] = []
        self._svg_depth = 0
        self._open_link_stack: list[bool] = []  # True if <a> was emitted, False if unwrapped
        self._out_all: list[str] = []
        self._out_body: list[str] = []

    def _emit(self, chunk: str) -> None:
        self._out_all.append(chunk)
        if self._in_body:
            self._out_body.append(chunk)

    @property
    def fallback_title(self) -> str:
        heading = _clean_text(
            html_lib.unescape("".join(self._first_heading_parts)),
            max_length=MAX_CHAPTER_TITLE_LENGTH,
        )
        if heading:
            return heading
        return _clean_text(
            html_lib.unescape("".join(self._doc_title_parts)),
            max_length=MAX_CHAPTER_TITLE_LENGTH,
        )

    @property
    def html(self) -> str:
        return "".join(self._out_body if self._has_body else self._out_all)

    def _rewrite_href(self, raw_href: str) -> str | None:
        href = raw_href.strip()
        if not href or href.startswith("#"):
            return None
        parsed = urlsplit(href)
        scheme = parsed.scheme.lower()
        if scheme:
            if scheme in _ALLOWED_URL_SCHEMES:
                return href
            return None
        if parsed.netloc:
            return None
        resolved = _resolve_relative_path(self._chapter_href, href)
        if resolved and resolved in self._href_to_position:
            pos = self._href_to_position[resolved]
            return f"/my-titles/{self._title_id}/chapters/{pos}"
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "title" and not self._in_body:
            self._in_title = True
            return
        if t == "body":
            self._has_body = True
            self._in_body = True
            return
        if t == "html":
            return
        if self._drop_stack:
            if t not in _VOID_TAGS:
                self._drop_stack.append(t)
            return
        if t in _DROP_SUBTREE_TAGS:
            if t not in _VOID_TAGS:
                self._drop_stack.append(t)
            return
        if t == "svg":
            self._svg_depth += 1
            return
        if self._svg_depth > 0:
            # Calibre/Pandoc EPUBs often wrap raster illustrations in <svg><image .../></svg>.
            # Drop the SVG itself (no active SVG content) but salvage valid raster images.
            if t == "image":
                attr_map = {k.lower(): (v or "") for k, v in attrs}
                raw_href = (
                    attr_map.get("xlink:href")
                    or attr_map.get("href")
                    or attr_map.get("{http://www.w3.org/1999/xlink}href")
                    or ""
                )
                safe_url = self._assets.collect(self._chapter_href, raw_href)
                if safe_url:
                    self._emit(f'<p><img src="{html_lib.escape(safe_url, quote=True)}" alt=""></p>')
            return

        if not self._captured_heading and t in ("h1", "h2", "h3") and self._heading_tag is None:
            self._heading_tag = t

        if t not in _ALLOWED_TAGS:
            return

        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t == "img":
            safe_url = self._assets.collect(self._chapter_href, attr_map.get("src", ""))
            if not safe_url:
                return
            alt = html_lib.escape(attr_map.get("alt", ""), quote=True)
            title_attr = attr_map.get("title", "")
            parts = [f'src="{html_lib.escape(safe_url, quote=True)}"', f'alt="{alt}"']
            if title_attr:
                parts.append(f'title="{html_lib.escape(title_attr, quote=True)}"')
            self._emit(f"<img {' '.join(parts)}>")
            return

        if t == "a":
            safe_href = self._rewrite_href(attr_map.get("href", ""))
            if not safe_href:
                self._open_link_stack.append(False)
                return
            self._open_link_stack.append(True)
            parts = [f'href="{html_lib.escape(safe_href, quote=True)}"']
            if title_attr := attr_map.get("title", ""):
                parts.append(f'title="{html_lib.escape(title_attr, quote=True)}"')
            self._emit(f"<a {' '.join(parts)}>")
            return

        allowed = _ALLOWED_ATTRIBUTES.get(t, set())
        emitted_attrs = [
            f'{k}="{html_lib.escape(v, quote=True)}"'
            for k, v in attr_map.items()
            if k in allowed and v
        ]
        if emitted_attrs:
            self._emit(f"<{t} {' '.join(emitted_attrs)}>")
        else:
            self._emit(f"<{t}>")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = False
            return
        if t == "body":
            self._in_body = False
            return
        if t == "html":
            return
        if self._drop_stack:
            if t in self._drop_stack:
                while self._drop_stack and self._drop_stack.pop() != t:
                    pass
            return
        if t == "svg":
            if self._svg_depth > 0:
                self._svg_depth -= 1
            return
        if self._svg_depth > 0:
            return
        if self._heading_tag == t:
            self._heading_tag = None
            if "".join(self._first_heading_parts).strip():
                self._captured_heading = True
        if t == "a":
            if self._open_link_stack and self._open_link_stack.pop():
                self._emit("</a>")
            return
        if t in _ALLOWED_TAGS and t not in _VOID_TAGS:
            self._emit(f"</{t}>")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._doc_title_parts.append(data)
            return
        if self._drop_stack or self._svg_depth > 0:
            return
        if self._heading_tag is not None and not self._captured_heading:
            self._first_heading_parts.append(data)
        self._emit(html_lib.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        ref = f"&{name};"
        if self._in_title:
            self._doc_title_parts.append(ref)
            return
        if self._drop_stack or self._svg_depth > 0:
            return
        if self._heading_tag is not None and not self._captured_heading:
            self._first_heading_parts.append(ref)
        self._emit(ref)

    def handle_charref(self, name: str) -> None:
        ref = f"&#{name};"
        if self._in_title:
            self._doc_title_parts.append(ref)
            return
        if self._drop_stack or self._svg_depth > 0:
            return
        if self._heading_tag is not None and not self._captured_heading:
            self._first_heading_parts.append(ref)
        self._emit(ref)


_OUTER_WRAPPER_RE = re.compile(
    r"^\s*<(div|section)\b[^>]*>(.*)</\1>\s*$", re.IGNORECASE | re.DOTALL
)


def _unwrap_single_container(cleaned_html: str) -> str:
    """Unwraps redundant single outer <div>/<section> wrappers so chapter paragraphs become
    direct children of ``.reader-content`` (needed by reader-progress.js & tap-to-read.js)."""
    current = cleaned_html.strip()
    for _ in range(4):
        match = _OUTER_WRAPPER_RE.match(current)
        if not match:
            break
        tag = match.group(1).lower()
        inner = match.group(2)
        # Only unwrap if there isn't another closing </tag> before the end that would mean
        # two sibling <div>...</div><div>...</div> blocks matched together.
        depth = 0
        balanced = True
        for token in re.finditer(rf"<(/?){tag}\b[^>]*>", inner, re.IGNORECASE):
            depth += -1 if token.group(1) else 1
            if depth < 0:
                balanced = False
                break
        if not balanced or depth != 0:
            break
        current = inner.strip()
    return current


def _has_visible_content(sanitized_html: str) -> bool:
    if "<img " in sanitized_html or "<img\t" in sanitized_html:
        return True
    text_only = nh3.clean(sanitized_html, tags=set()).strip()
    return bool(text_only)


def sanitize_chapter_html(
    raw_html: str,
    *,
    chapter_href: str,
    title_id: uuid.UUID,
    href_to_position: dict[str, int],
    assets: _AssetCollector,
) -> tuple[str, str]:
    """Rewrites local images/links, removes active content/CSS/SVG, and runs ``nh3.clean``."""
    rewriter = _ChapterHtmlRewriter(
        chapter_href=chapter_href,
        title_id=title_id,
        href_to_position=href_to_position,
        assets=assets,
    )
    rewriter.feed(raw_html)
    rewriter.close()

    # Defense-in-depth: second independent sanitization pass with ammonia/nh3.
    cleaned = nh3.clean(
        rewriter.html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="nofollow noopener noreferrer ugc",
        clean_content_tags={
            "script",
            "style",
            "noscript",
            "iframe",
            "object",
            "embed",
            "svg",
            "math",
        },
    )
    return _unwrap_single_container(cleaned), rewriter.fallback_title


def parse_epub(
    epub_path: Path,
    *,
    title_id: uuid.UUID,
    original_filename: str = "book.epub",
) -> ParsedEpub:
    """Validates an EPUB archive on disk and extracts metadata, cover, assets, and chapters.

    Raises ``EpubValidationError`` with a clear user-facing Russian message if the archive
    is malformed, encrypted, violates structural/security checks, or has no readable chapters.
    """
    if not zipfile.is_zipfile(epub_path):
        raise EpubValidationError(
            "Файл не является корректным EPUB (повреждённый или не ZIP-архив)"
        )

    try:
        zf = zipfile.ZipFile(epub_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise EpubValidationError(
            "Файл не является корректным EPUB (повреждённый ZIP-архив)"
        ) from exc

    with zf:
        archive = _SafeArchive(zf)

        if not archive.exists("mimetype"):
            raise EpubValidationError("Файл не является корректным EPUB (отсутствует mimetype)")
        mimetype_bytes = archive.read("mimetype", max_bytes=256).strip()
        if mimetype_bytes != _EPUB_MIMETYPE:
            raise EpubValidationError(
                "Файл не является корректным EPUB (неверный mimetype внутри архива)"
            )

        opf_path = _find_opf_path(archive)
        fallback_title = Path(original_filename).stem or "Без названия"
        (
            book_title,
            authors,
            description,
            manifest,
            spine_refs,
            meta_cover_id,
            spine_toc_id,
        ) = _parse_opf(archive, opf_path, fallback_title)

        cover = _extract_cover(archive, manifest, meta_cover_id)
        toc_titles, nav_hrefs = _extract_toc_titles(archive, manifest, spine_toc_id)

        # Resolve ordered XHTML spine items.
        linear_items: list[_ManifestItem] = []
        all_items: list[_ManifestItem] = []
        seen_spine_hrefs: set[str] = set()
        for idref, is_linear in spine_refs:
            item = manifest.get(idref)
            if item is None:
                continue
            if item.media_type and item.media_type not in _HTML_MEDIA_TYPES:
                continue
            if not archive.exists(item.href) or item.href in seen_spine_hrefs:
                continue
            seen_spine_hrefs.add(item.href)
            all_items.append(item)
            if is_linear:
                linear_items.append(item)

        chosen_items = linear_items if linear_items else all_items
        # Skip the EPUB 3 navigation document itself when real chapters exist alongside it.
        non_nav_items = [item for item in chosen_items if item.href not in nav_hrefs]
        if non_nav_items:
            chosen_items = non_nav_items

        if len(chosen_items) > MAX_CHAPTERS:
            raise EpubValidationError(f"В EPUB слишком много глав (максимум {MAX_CHAPTERS})")

        # Pass 1: sanitize each spine document (keep only non-empty ones), using a provisional
        # href->position map, then finalize internal chapter links if any empty docs were dropped.
        assets = _AssetCollector(archive, title_id)
        raw_docs: list[tuple[_ManifestItem, str]] = []
        for item in chosen_items:
            raw_bytes = archive.read(item.href)
            raw_html = raw_bytes.decode("utf-8", errors="replace")
            raw_docs.append((item, raw_html))

        # Pre-map candidate hrefs to 1-based positions for internal link rewriting.
        provisional_map = {item.href: idx for idx, (item, _) in enumerate(raw_docs, start=1)}
        non_empty_docs: list[tuple[_ManifestItem, str]] = []
        for item, raw_html in raw_docs:
            preview_html, _ = sanitize_chapter_html(
                raw_html,
                chapter_href=item.href,
                title_id=title_id,
                href_to_position=provisional_map,
                assets=assets,
            )
            if _has_visible_content(preview_html):
                non_empty_docs.append((item, raw_html))

        if not non_empty_docs:
            raise EpubValidationError("В EPUB не найдено ни одной читаемой главы")

        final_map = {item.href: idx for idx, (item, _) in enumerate(non_empty_docs, start=1)}
        chapters: list[ExtractedChapter] = []
        for position, (item, raw_html) in enumerate(non_empty_docs, start=1):
            sanitized_html, fallback_heading = sanitize_chapter_html(
                raw_html,
                chapter_href=item.href,
                title_id=title_id,
                href_to_position=final_map,
                assets=assets,
            )
            chapter_title = (
                toc_titles.get(item.href) or fallback_heading or f"Глава {position}"
            )
            chapters.append(
                ExtractedChapter(
                    position=position,
                    title=_clean_text(chapter_title, max_length=MAX_CHAPTER_TITLE_LENGTH)
                    or f"Глава {position}",
                    content=sanitized_html,
                    source_href=item.href,
                )
            )

        return ParsedEpub(
            title=book_title,
            authors=authors,
            description=description,
            cover=cover,
            chapters=chapters,
            assets=assets.assets,
        )
