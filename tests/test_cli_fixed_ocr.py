from __future__ import annotations

from pathlib import Path


CLI_SOURCE = (Path(__file__).resolve().parents[1] / "run_cli.py").read_text(
    encoding="utf-8"
)


def test_cli_no_longer_exposes_ocr_engine_or_model_choices():
    for option in (
        '"--engine"',
        '"--date-engine"',
        '"--rec-model"',
        '"--det-model"',
        '"--no-date-ocr-fallback"',
        '"--no-date-slot-ocr"',
    ):
        assert option not in CLI_SOURCE


def test_cli_uses_validated_fixed_ocr_configuration():
    assert 'ocr_engine="paddle"' in CLI_SOURCE
    assert 'ocr_rec_model="PP-OCRv5_mobile_rec"' in CLI_SOURCE
    assert 'ocr_det_model="PP-OCRv6_medium_det"' in CLI_SOURCE
    assert "date_ocr_fallback=False" in CLI_SOURCE
    assert "date_slot_ocr=False" in CLI_SOURCE
    assert "complete_csv_path(csv_path)" in CLI_SOURCE


def test_cli_writes_its_outputs_with_the_same_function_as_the_window():
    """El CSV doble, los PDFs y las stats salen de ``write_outputs``.

    Escribir las salidas por separado en cada superficie era lo que hacía
    que una ejecución de línea de comandos y una de la interfaz entregaran
    carpetas distintas. Si vuelve a aparecer aquí una escritura propia, la
    divergencia vuelve con ella.
    """
    assert "write_outputs(" in CLI_SOURCE
    for propio in (
        "CsvReporter().write(",
        "write_minimal_csv(",
        "JsonReporter().write_consolidated(",
        "escribir_stats(",
        "escribir_pdf_unico(",
        "generar_pdfs(",
        "write_debug_pdf(",
    ):
        assert propio not in CLI_SOURCE


def test_application_engine_has_no_alternative_model_fallback_names():
    root = Path(__file__).resolve().parents[1]
    application_source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in ("app/ocr/engine.py", "app/core/config.py")
    )
    assert "PP-OCRv6_medium_rec" not in application_source
    assert "PP-OCRv6_tiny_det" not in application_source
