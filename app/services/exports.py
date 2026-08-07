"""Which export formats are on offer, taken from the SDK's own registry."""

from ranobelib.exporters import EXPORTERS


def available_export_formats() -> list[str]:
    """Formats this deployment can export to, e.g. `["epub", "fb2", "txt"]`.

    Sourced from `EXPORTERS` rather than a hardcoded list, so a format that isn't
    registered (e.g. "pdf" when WeasyPrint isn't installed) simply doesn't appear.
    """
    return sorted(EXPORTERS)
