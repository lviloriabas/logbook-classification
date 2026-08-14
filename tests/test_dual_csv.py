from __future__ import annotations

import csv
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.csv_reporter import CsvReporter
from app.reports.dual_csv import write_minimal_csv
from app.reports.outputs import complete_csv_path


def _report() -> ValidationReport:
    page = PageResult(
        page_number=1,
        fields=[
            FieldResult(
                page_number=1,
                field_id="log_number",
                field_type="ocr",
                value="1234500",
                confidence=0.9,
            ),
            FieldResult(
                page_number=1,
                field_id="matricula",
                field_type="ocr",
                value="HP-1717CMP",
                confidence=0.8,
            ),
        ],
    )
    return ValidationReport(pdf_path="book.pdf", template_name="test", pages=[page])


def _headers(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


def test_selected_csv_columns_do_not_change_complete_dataset(tmp_path: Path):
    report = _report()
    minimal = tmp_path / "run.CSV"
    complete = complete_csv_path(minimal)

    CsvReporter().write(report, complete)
    write_minimal_csv(complete, minimal, ["log_number", "matricula"])

    assert _headers(minimal) == ["file", "page", "log_number", "matricula"]
    complete_headers = _headers(complete)
    assert "log_number_conf" in complete_headers
    assert "matricula_comment" in complete_headers
    assert report.pages[0].fields[0].confidence == 0.9


def test_complete_csv_uses_distinct_stable_name():
    assert complete_csv_path(Path("BITS TEST.CSV")) == Path(
        "BITS TEST_completo.CSV"
    )
