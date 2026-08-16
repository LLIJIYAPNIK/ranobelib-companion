"""Magic-byte sniffing for uploaded images, shared by anything that accepts a JPG/PNG/WEBP
upload (avatars, PR 96; comment attachments, PR 151) - sniffs the actual bytes rather than
trusting the browser-supplied content-type header alone, which a renamed/relabeled file
(e.g. an .exe served as "image/png") would otherwise sail through.
"""

from __future__ import annotations


def looks_like_jpeg(contents: bytes) -> bool:
    return contents.startswith(b"\xff\xd8\xff")


def looks_like_png(contents: bytes) -> bool:
    return contents.startswith(b"\x89PNG\r\n\x1a\n")


def looks_like_webp(contents: bytes) -> bool:
    return contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
