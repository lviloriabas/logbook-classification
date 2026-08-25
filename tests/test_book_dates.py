"""Pruebas del registro de fechas por libro entre ejecuciones."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.date_corrector import (
    correct_dates_by_book,
    learn_book_dates,
)


def _field(
    pn: int,
    fid: str,
    value,
    status=Status.OK,
    conf=0.9,
    source="direct",
) -> FieldResult:
    return FieldResult(
        page_number=pn,
        field_id=fid,
        field_type="ocr",
        value=value,
        status=status,
        confidence=conf,
        source=source,
    )


def _page(pn: int, log: str, day=None, month=None, year=None) -> PageResult:
    page = PageResult(page_number=pn)
    page.add_field(_field(pn, "log_number", log))
    page.add_field(_field(pn, "day", day))
    page.add_field(_field(pn, "month", month))
    page.add_field(_field(pn, "year", year))
    return page


def _report(*pages: PageResult) -> ValidationReport:
    return ValidationReport(
        pdf_path="fixture.pdf", template_name="fixture", pages=list(pages)
    )


def _field_of(page: PageResult, field_id: str) -> FieldResult:
    return next(field for field in page.fields if field.field_id == field_id)


class TestLearnBookDates(unittest.TestCase):
    def test_stores_only_the_extremes_of_the_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            learned = learn_book_dates([_report(
                _page(1, "2315902", "14", "MAY", "25"),
                _page(2, "2315920", "22", "MAY", "25"),
                _page(3, "2315940", "02", "JUN", "25"),
            )], path)

            self.assertEqual(learned, 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data, {"23159A": {"02": "2025-05-14", "40": "2025-06-02"}}
            )

    def test_file_stays_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            books = [
                _report(
                    _page(1, f"2{serie:04d}00", "01", "ENE", "25"),
                    _page(2, f"2{serie:04d}49", "28", "FEB", "25"),
                )
                for serie in range(100)
            ]
            learn_book_dates(books, path)

            # Cien libros no llegan a cinco kilobytes.
            self.assertLess(path.stat().st_size, 5000)

    def test_extends_a_previous_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            learn_book_dates(
                [_report(_page(1, "2315902", "14", "MAY", "25"))], path
            )
            learned = learn_book_dates(
                [_report(_page(1, "2315945", "02", "JUN", "25"))], path
            )

            self.assertEqual(learned, 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data, {"23159A": {"02": "2025-05-14", "45": "2025-06-02"}}
            )

    def test_one_reading_does_not_replace_a_contradicting_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            learn_book_dates(
                [_report(_page(1, "2315902", "14", "MAY", "25"))], path
            )
            learned = learn_book_dates(
                [_report(_page(1, "2315902", "19", "MAY", "25"))], path
            )

            self.assertEqual(learned, 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, {"23159A": {"02": "2025-05-14"}})

    def test_two_readings_correct_the_same_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            learn_book_dates([_report(
                _page(1, "2315902", "14", "MAY", "25"),
                _page(2, "2315940", "02", "JUN", "25"),
            )], path)
            # La página 02 se relee, y otra página del libro respalda que
            # aquella fecha guardada no era la de esa página.
            learned = learn_book_dates([_report(
                _page(1, "2315902", "19", "MAY", "25"),
                _page(2, "2315910", "23", "MAY", "25"),
            )], path)

            self.assertEqual(learned, 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data, {"23159A": {"02": "2025-05-19", "10": "2025-05-23"}}
            )

    def test_two_readings_correct_an_impossible_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            path.write_text(
                json.dumps({"23159A": {"02": "2025-11-14", "40": "2025-11-29"}}),
                encoding="utf-8",
            )
            # Ninguna página choca con las guardadas, pero las dos lecturas
            # obligarían al libro a retroceder de noviembre a mayo.
            learned = learn_book_dates([_report(
                _page(1, "2315920", "03", "MAY", "25"),
                _page(2, "2315930", "07", "MAY", "25"),
            )], path)

            self.assertEqual(learned, 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data, {"23159A": {"20": "2025-05-03", "30": "2025-05-07"}}
            )

    def test_a_corrected_entry_is_the_one_that_infers_next_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            path.write_text(
                json.dumps({"23159A": {"02": "2025-11-14", "40": "2025-11-29"}}),
                encoding="utf-8",
            )
            learn_book_dates([_report(
                _page(1, "2315902", "03", "MAY", "25"),
                _page(2, "2315940", "07", "MAY", "25"),
            )], path)

            page = _page(1, "2315920", "05", None, None)
            correct_dates_by_book([_report(page)], path)

            self.assertEqual(_field_of(page, "month").value, "MAY")
            self.assertEqual(_field_of(page, "year").value, "25")

    def test_a_conflict_without_support_leaves_the_entry_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            stored = {"23159A": {"02": "2025-05-14", "40": "2025-06-02"}}
            path.write_text(json.dumps(stored), encoding="utf-8")
            # Las dos lecturas se contradicen entre sí, así que el libro no
            # aporta una versión coherente que pueda ganarle a la guardada.
            learned = learn_book_dates([_report(
                _page(1, "2315910", "20", "AGO", "25"),
                _page(2, "2315920", "20", "ENE", "25"),
            )], path)

            self.assertEqual(learned, 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), stored)

    def test_ignores_readings_that_are_not_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            page = _page(1, "2315902", "14", "MAY", "25")
            _field_of(page, "month").source = "inferred"
            learned = learn_book_dates([_report(page)], path)

            self.assertEqual(learned, 0)
            self.assertFalse(path.exists())

    def test_does_not_learn_a_book_whose_dates_go_backwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            learned = learn_book_dates([_report(
                _page(1, "2315902", "14", "MAY", "25"),
                _page(2, "2315940", "02", "ABR", "25"),
            )], path)

            self.assertEqual(learned, 0)
            self.assertFalse(path.exists())


class TestRegistryHelpsInference(unittest.TestCase):
    def _registry(self, tmp: str, content: dict) -> Path:
        path = Path(tmp) / "book_fechas.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def test_fills_the_month_between_the_stored_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._registry(
                tmp, {"23159A": {"02": "2025-05-14", "40": "2025-05-29"}}
            )
            page = _page(1, "2315920", "22", None, None)

            correct_dates_by_book([_report(page)], path)

            month = _field_of(page, "month")
            year = _field_of(page, "year")
            self.assertEqual(month.value, "MAY")
            self.assertEqual(year.value, "25")
            self.assertIs(month.status, Status.WARNING)
            self.assertEqual(month.source, "inferred")
            self.assertEqual(month.inference_method, "book_dates_registry")

    def test_fills_only_the_year_when_the_month_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._registry(
                tmp, {"23159A": {"02": "2025-05-14", "40": "2025-06-02"}}
            )
            page = _page(1, "2315920", "22", None, None)

            correct_dates_by_book([_report(page)], path)

            self.assertIsNone(_field_of(page, "month").value)
            self.assertEqual(_field_of(page, "year").value, "25")

    def test_does_not_touch_pages_outside_the_stored_span(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._registry(
                tmp, {"23159A": {"02": "2025-05-14", "20": "2025-05-29"}}
            )
            page = _page(1, "2315945", "22", None, None)

            correct_dates_by_book([_report(page)], path)

            self.assertIsNone(_field_of(page, "month").value)
            self.assertIsNone(_field_of(page, "year").value)

    def test_a_direct_reading_wins_over_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._registry(
                tmp, {"23159A": {"02": "2025-05-14", "40": "2025-05-29"}}
            )
            page = _page(1, "2315920", "22", "MAY", "25")

            correct_dates_by_book([_report(page)], path)

            month = _field_of(page, "month")
            self.assertEqual(month.value, "MAY")
            self.assertEqual(month.source, "direct")
            self.assertIs(month.status, Status.OK)

    def test_a_contradiction_disables_the_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._registry(
                tmp, {"23159A": {"02": "2025-05-14", "40": "2025-05-29"}}
            )
            # Una lectura directa de julio dentro del tramo guardado: el
            # registro deja de usarse en todo el libro.
            read = _page(1, "2315910", "03", "JUL", "25")
            empty = _page(2, "2315920", "22", None, None)

            correct_dates_by_book([_report(read, empty)], path)

            self.assertIsNone(_field_of(empty, "month").value)
            self.assertIsNone(_field_of(empty, "year").value)

    def test_a_damaged_file_does_not_stop_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book_fechas.json"
            path.write_text("{esto no es json", encoding="utf-8")
            page = _page(1, "2315920", "22", None, None)

            stats = correct_dates_by_book([_report(page)], path)

            self.assertEqual(stats["registry_filled"], 0)

    def test_the_run_without_registry_keeps_working(self):
        page = _page(1, "2315920", "22", None, None)

        stats = correct_dates_by_book([_report(page)])

        self.assertEqual(stats["registry_filled"], 0)


if __name__ == "__main__":
    unittest.main()
