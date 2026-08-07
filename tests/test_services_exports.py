from unittest.mock import patch

from app.services.exports import available_export_formats


def test_available_export_formats_reads_exporters_registry() -> None:
    fake_exporters = {"epub": object(), "txt": object(), "fb2": object()}
    with patch("app.services.exports.EXPORTERS", fake_exporters):
        assert available_export_formats() == ["epub", "fb2", "txt"]


def test_available_export_formats_omits_unregistered_pdf() -> None:
    fake_exporters = {"epub": object(), "txt": object()}
    with patch("app.services.exports.EXPORTERS", fake_exporters):
        assert "pdf" not in available_export_formats()
