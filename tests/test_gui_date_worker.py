"""Integración del corrector de fechas en el worker usado por la GUI."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.config import AppConfig
from app.gui.worker import PipelineWorker
from app.models.schemas import ValidationReport


def test_gui_worker_applies_book_sequence_after_all_files():
    reports = [
        ValidationReport(pdf_path="a.pdf", template_name="fixture"),
        ValidationReport(pdf_path="b.pdf", template_name="fixture"),
    ]
    pipeline = MagicMock()
    pipeline.process.side_effect = reports
    pipeline.vlm_stats = {"enabled": False}
    worker = PipelineWorker(
        [Path("a.pdf"), Path("b.pdf")],
        Path("template/aircraft_log.json"),
        AppConfig(align=False, vlm_enabled=False),
    )

    with patch("app.core.pipeline.Pipeline", return_value=pipeline), patch(
        "app.ocr.engine.create_engine", return_value=MagicMock()
    ), patch(
        "app.gui.worker.correct_matricula_by_book"
    ), patch(
        "app.gui.worker.correct_dates_by_book"
    ) as date_corrector:
        worker.run()

    date_corrector.assert_called_once_with(
        reports, worker.config.book_fechas_file
    )
    assert worker.reports == reports
    assert all(item["enabled"] is False for item in worker.vlm_stats)
