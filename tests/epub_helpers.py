"""Helpers for building minimal valid/custom EPUB 2 & EPUB 3 test archives."""

from __future__ import annotations

import base64
import io
import zipfile
from dataclasses import dataclass

# Minimal valid raster images (real magic bytes, tiny payloads).
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
TINY_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
TINY_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


@dataclass(frozen=True)
class ChapterSpec:
    href: str
    title: str
    body_html: str
    linear: bool = True
    in_toc: bool = True


def build_epub_bytes(
    *,
    title: str = "Тестовая книга",
    authors: list[str] | None = None,
    description: str | None = "Описание тестовой книги.",
    chapters: list[ChapterSpec] | None = None,
    cover: tuple[str, str, bytes] | None = None,  # (href, media_type, bytes)
    cover_mode: str = "epub3",  # "epub3" | "epub2" | "fallback"
    toc_mode: str = "epub3",  # "epub3" | "ncx" | "none"
    extra_files: dict[str, tuple[str, bytes]] | None = None,  # href -> (media_type, bytes)
    mimetype: bytes | None = b"application/epub+zip",
    opf_path: str = "OEBPS/content.opf",
) -> bytes:
    """Builds an in-memory EPUB archive suitable for parser, service, and route tests."""
    if authors is None:
        authors = ["Иван Иванов"]
    if chapters is None:
        chapters = [
            ChapterSpec("ch1.xhtml", "Пролог", "<p>Первый абзац пролога.</p>"),
            ChapterSpec("ch2.xhtml", "Глава 1", "<p>Текст первой главы.</p>"),
        ]
    extra_files = extra_files or {}
    opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""

    def _full(rel: str) -> str:
        return f"{opf_dir}/{rel}" if opf_dir else rel

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if mimetype is not None:
            zf.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)

        container_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            f'    <rootfile full-path="{opf_path}" '
            'media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>\n"
        )
        zf.writestr("META-INF/container.xml", container_xml)

        manifest_items: list[str] = []
        spine_items: list[str] = []
        meta_extra: list[str] = []

        for idx, ch in enumerate(chapters, start=1):
            item_id = f"ch{idx}"
            manifest_items.append(
                f'    <item id="{item_id}" href="{ch.href}" media-type="application/xhtml+xml"/>'
            )
            linear_attr = "" if ch.linear else ' linear="no"'
            spine_items.append(f'    <itemref idref="{item_id}"{linear_attr}/>')
            xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                f"<head><title>{ch.title}</title></head>\n"
                f"<body>{ch.body_html}</body>\n"
                "</html>\n"
            )
            zf.writestr(_full(ch.href), xhtml)

        if cover is not None:
            cov_href, cov_mt, cov_bytes = cover
            zf.writestr(_full(cov_href), cov_bytes)
            if cover_mode == "epub3":
                manifest_items.append(
                    f'    <item id="cov" href="{cov_href}" media-type="{cov_mt}" '
                    'properties="cover-image"/>'
                )
            elif cover_mode == "epub2":
                manifest_items.append(
                    f'    <item id="cover-img" href="{cov_href}" media-type="{cov_mt}"/>'
                )
                meta_extra.append('    <meta name="cover" content="cover-img"/>')
            else:
                manifest_items.append(
                    f'    <item id="book_cover" href="{cov_href}" media-type="{cov_mt}"/>'
                )

        for idx, (rel_href, (mt, data)) in enumerate(extra_files.items(), start=1):
            zf.writestr(_full(rel_href), data)
            manifest_items.append(
                f'    <item id="extra{idx}" href="{rel_href}" media-type="{mt}"/>'
            )

        spine_toc_attr = ""
        if toc_mode == "epub3":
            manifest_items.append(
                '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                'properties="nav"/>'
            )
            toc_lis = "\n".join(
                f'      <li><a href="{ch.href}">{ch.title}</a></li>'
                for ch in chapters
                if ch.in_toc
            )
            nav_xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops">\n'
                "<body>\n"
                '  <nav epub:type="toc"><ol>\n'
                f"{toc_lis}\n"
                "  </ol></nav>\n"
                "</body></html>\n"
            )
            zf.writestr(_full("nav.xhtml"), nav_xhtml)
        elif toc_mode == "ncx":
            spine_toc_attr = ' toc="ncx"'
            manifest_items.append(
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            )
            nav_points = "\n".join(
                f'    <navPoint id="np{i}" playOrder="{i}">'
                f"<navLabel><text>{ch.title}</text></navLabel>"
                f'<content src="{ch.href}"/></navPoint>'
                for i, ch in enumerate(chapters, start=1)
                if ch.in_toc
            )
            ncx_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                "  <navMap>\n"
                f"{nav_points}\n"
                "  </navMap>\n"
                "</ncx>\n"
            )
            zf.writestr(_full("toc.ncx"), ncx_xml)

        author_xml = "\n".join(f"    <dc:creator>{a}</dc:creator>" for a in authors)
        desc_xml = f"    <dc:description>{description}</dc:description>" if description else ""
        title_xml = f"    <dc:title>{title}</dc:title>" if title else ""
        opf_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f"{title_xml}\n"
            f"{author_xml}\n"
            f"{desc_xml}\n"
            + "\n".join(meta_extra)
            + "\n  </metadata>\n"
            "  <manifest>\n"
            + "\n".join(manifest_items)
            + "\n  </manifest>\n"
            f"  <spine{spine_toc_attr}>\n"
            + "\n".join(spine_items)
            + "\n  </spine>\n"
            "</package>\n"
        )
        zf.writestr(opf_path, opf_xml)
    return buf.getvalue()
