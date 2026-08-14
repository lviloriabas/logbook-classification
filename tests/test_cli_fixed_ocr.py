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
    assert "write_minimal_csv(full_csv_path, csv_path)" in CLI_SOURCE


def test_application_engine_has_no_alternative_model_fallback_names():
    root = Path(__file__).resolve().parents[1]
    application_source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in ("app/ocr/engine.py", "app/core/config.py")
    )
    assert "PP-OCRv6_medium_rec" not in application_source
    assert "PP-OCRv6_tiny_det" not in application_source
