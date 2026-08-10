"""Pruebas del corrector de fechas por libro (bitácoras secuenciales).

Se construyen casos sintéticos que simulan las reglas del dominio:
entradas consecutivas, varias el mismo día, y días sin entrada.
"""

from __future__ import annotations

import unittest

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.date_corrector import correct_dates_by_book


def _field(pn: int, fid: str, value, status=Status.OK, conf=0.9) -> FieldResult:
    return FieldResult(page_number=pn, field_id=fid, field_type="ocr",
                       value=value, status=status, confidence=conf)


def _page(pn: int, log: str, day=None, month=None, year=None) -> PageResult:
    page = PageResult(page_number=pn)
    page.add_field(_field(pn, "log_number", log))
    page.add_field(_field(pn, "day", day))
    page.add_field(_field(pn, "month", month))
    page.add_field(_field(pn, "year", year))
    return page


def _combine(pn: int, text: str) -> PageResult:
    """Página con fecha combinada (page.date) y año de 2 dígitos."""
    month = f"{int(text[5:7]):02d}"
    page = PageResult(page_number=pn, date=text)
    page.add_field(_field(pn, "log_number", f"22417{100 + pn:03d}"))
    page.add_field(_field(pn, "day", text[8:10]))
    page.add_field(_field(pn, "month", month))
    page.add_field(_field(pn, "year", text[2:4]))
    return page


def _report(*pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path="fixture.pdf", template_name="fixture",
                           pages=list(pages))


def _year(page: PageResult) -> FieldResult:
    return next(f for f in page.fields if f.field_id == "year")


class TestYearNormalization(unittest.TestCase):
    def test_three_digit_year_corrected_to_winner(self):
        pages = [_page(1, "100001", "16", "JUL", "26"),
                 _page(2, "100002", "16", "JUL", "26"),
                 _page(3, "100003", "16", "JUL", "216")]
        _year(pages[2]).status = Status.ERROR
        correct_dates_by_book([_report(*pages)])
        year = _year(pages[2])
        self.assertEqual(year.value, "26")
        self.assertEqual(pages[2].date, "2026/07/16")
        self.assertIs(year.status, Status.OK)
        self.assertIn("Inferred from book readings: 26", year.comment)

    def test_mismatch_year_flagged_not_overwritten(self):
        pages = [_page(1, "100001", "16", "JUL", "26"),
                 _page(2, "100002", "16", "JUL", "26"),
                 _page(3, "100003", "16", "JUL", "26"),
                 _page(4, "100004", "16", "JUL", "24")]
        correct_dates_by_book([_report(*pages)])
        year = _year(pages[3])
        self.assertEqual(year.value, "24")
        self.assertIs(year.status, Status.WARNING)
        self.assertIn("differs from book majority: 26", year.comment)


class TestRunInference(unittest.TestCase):
    def test_missing_date_inferred_within_solid_run(self):
        pages = [_combine(1, "2026/07/16"),
                 _combine(2, "2026/07/16"),
                 _combine(3, "2026/07/16"),
                 _page(4, "100004", "16", "JUL", "26")]
        _year(pages[3]).status = Status.ERROR
        correct_dates_by_book([_report(*pages)])
        year = _year(pages[3])
        self.assertEqual(year.value, "26")
        self.assertEqual(pages[3].date, "2026/07/16")
        self.assertIs(year.status, Status.OK)
        self.assertIn("Inferred from book dates: 2026/07/16", year.comment)

    def test_single_vote_run_not_inferred(self):
        pages = [_combine(1, "2026/07/16"),
                 _page(2, "100002", "16", "JUL", "26"),
                 _page(3, "100003", "16", "JUL", "26")]
        _year(pages[1]).status = Status.ERROR
        _year(pages[2]).status = Status.ERROR
        correct_dates_by_book([_report(*pages)])
        self.assertEqual(_year(pages[1]).value, "26")
        self.assertEqual(_year(pages[2]).value, "26")

    def test_same_day_multiple_entries_preserved(self):
        pages = [_combine(1, "2026/07/16"),
                 _combine(2, "2026/07/16"),
                 _combine(3, "2026/07/16"),
                 _combine(4, "2026/07/17"),
                 _combine(5, "2026/07/17")]
        correct_dates_by_book([_report(*pages)])
        for page in pages:
            self.assertIs(_year(page).status, Status.OK)

    def test_isolated_different_day_overridden_with_warning(self):
        # 4 votos a 16 vs 1 voto a 17: mayoría fuerte, ruido <= 1 → sobrescribe.
        pages = [
            _combine(1, "2026/07/16"),
            _combine(2, "2026/07/16"),
            _combine(3, "2026/07/16"),
            _combine(4, "2026/07/16"),
            _combine(5, "2026/07/17"),
        ]
        correct_dates_by_book([_report(*pages)])
        day5 = next(f for f in pages[4].fields if f.field_id == "day")
        self.assertEqual(day5.value, "16")
        self.assertIs(day5.status, Status.WARNING)
        self.assertIn("Overridden", day5.comment)
        for i in (0, 1, 2, 3):
            self.assertEqual(pages[i].date, "2026/07/16")


class TestMonthFill(unittest.TestCase):
    def test_empty_month_filled_from_majority(self):
        # día+año resueltos pero mes vacío -> se rellena con el mes
        # mayoritario del libro (3+ votos, 60% de los meses legibles).
        pages = [
            _combine(1, "2026/07/16"),
            _combine(2, "2026/07/16"),
            _combine(3, "2026/07/16"),
            _page(4, "100004", "17", None, "26"),
            _page(5, "100005", "20", None, "26"),
        ]
        correct_dates_by_book([_report(*pages)])
        self.assertEqual(pages[3].date, "2026/07/17")
        self.assertEqual(pages[4].date, "2026/07/20")

    def test_valid_month_kept(self):
        pages = [
            _combine(1, "2026/07/16"),
            _combine(2, "2026/07/16"),
            _combine(3, "2026/07/16"),
            _page(4, "100004", "15", "DIC", "26"),
        ]
        correct_dates_by_book([_report(*pages)])
        # Mes DIC = 1 voto (frente a 3 de JUL); se queda como está.
        # Day 15 también se queda: la mayoría (16) exige 3 votos y
        # aquí los hay, pero solo es 1 vs 3 (1/4=25% < 60%): no
        # sobrescribe. La fecha queda con día 15 y mes DIC.
        self.assertEqual(pages[3].date, "2026/12/15")

    def test_month_majority_weak_not_filled(self):
        # Meses sin mayoría clara -> no se rellena.
        pages = [
            _combine(1, "2026/07/02"),
            _combine(2, "2026/08/09"),
            _combine(3, "2026/09/16"),
            _page(4, "100004", "15", None, "26"),
        ]
        correct_dates_by_book([_report(*pages)])
        self.assertIsNone(pages[3].date)


class TestRegression(unittest.TestCase):
    def test_backwards_date_flagged(self):
        pages = [_combine(1, "2026/07/16"),
                 _combine(2, "2026/07/15")]
        correct_dates_by_book([_report(*pages)])
        self.assertIs(_year(pages[1]).status, Status.WARNING)
        self.assertIn("regression", _year(pages[1]).comment)

    def test_year_rollover_not_flagged(self):
        pages = [_combine(1, "2025/12/31"),
                 _combine(2, "2026/01/01")]
        correct_dates_by_book([_report(*pages)])
        self.assertIs(_year(pages[1]).status, Status.OK)

    def test_non_decreasing_same_day_ok(self):
        pages = [_combine(1, "2026/07/16"),
                 _combine(2, "2026/07/16"),
                 _combine(3, "2026-07-18")]
        correct_dates_by_book([_report(*pages)])
        for page in pages:
            self.assertIs(_year(page).status, Status.OK)


class TestLeadingCoverage(unittest.TestCase):
    def test_leading_empty_pages_inferred_from_first_solid_run(self):
        pages = [
            _page(1, "100001", "20", "JUL", "26"),
            _page(2, "100002", "20", "JUL", "26"),
            _page(3, "100003", "20", "JUL", "26"),
            _combine(4, "2026/07/20"),
            _combine(5, "2026/07/20"),
            _combine(6, "2026/07/20"),
        ]
        for idx in (0, 1, 2):
            _year(pages[idx]).status = Status.ERROR
        correct_dates_by_book([_report(*pages)])
        for idx in (0, 1, 2):
            self.assertEqual(pages[idx].date, "2026/07/20")
            self.assertIs(_year(pages[idx]).status, Status.OK)
            self.assertGreater(round(_year(pages[idx]).confidence, 2), 0.6)


class TestSandwich(unittest.TestCase):
    def test_isolated_empty_between_identical_dates_filled(self):
        pages = [
            _combine(1, "2026/07/20"),
            _page(2, "100002", None, None, None),
            _combine(3, "2026/07/20"),
        ]
        correct_dates_by_book([_report(*pages)])
        self.assertEqual(pages[1].date, "2026/07/20")


class TestDayFill(unittest.TestCase):
    def test_day_completed_from_book_mode(self):
        pages = [
            _page(1, "100001", "20", "JUL", "26"),
            _page(2, "100002", "20", "JUL", "26"),
            _page(3, "100003", "20", "JUL", "26"),
            _page(4, "100004", None, "JUL", "26"),
            _page(5, "100005", None, "JUL", "26"),
        ]
        correct_dates_by_book([_report(*pages)])
        self.assertEqual(pages[3].date, "2026/07/20")
        self.assertEqual(pages[4].date, "2026/07/20")

    def test_weak_day_majority_not_filled(self):
        pages = [
            _page(1, "100001", "20", "JUL", "26"),
            _page(2, "100002", "21", "JUL", "26"),
            _page(3, "100003", None, "JUL", "26"),
            _page(4, "100004", "22", "JUL", "26"),
            _page(5, "100005", None, "JUL", "26"),
        ]
        correct_dates_by_book([_report(*pages)])
        self.assertIsNone(pages[2].date)


class TestNeverEmpty(unittest.TestCase):
    def test_unresolved_flagged_error(self):
        pages = [
            _combine(1, "2026/07/20"),
            _page(2, "100002", None, None, None),
        ]
        correct_dates_by_book([_report(*pages)])
        year = _year(pages[1])
        self.assertIs(year.status, Status.ERROR)
        self.assertIn("sin resolver", year.comment)

    def test_inferred_confidence_bounded(self):
        pages = [
            _combine(1, "2026/07/20"),
            _combine(2, "2026/07/20"),
            _combine(3, "2026/07/20"),
            _page(4, "100004", None, None, None),
        ]
        correct_dates_by_book([_report(*pages)])
        year = _year(pages[3])
        self.assertEqual(pages[3].date, "2026/07/20")
        self.assertGreaterEqual(year.confidence, 0.6)
        self.assertLessEqual(year.confidence, 0.95)


class TestFillPieces(unittest.TestCase):
    def test_each_page_one_piece_filled_iteratively(self):
        """Una página tiene solo day, otra solo month, otra solo year.
        Tras iterar, todas quedan con las tres piezas y la fecha
        combinada por mayoría."""
        pages = [
            _page(1, "100001", "20", None, None),
            _page(2, "100002", None, "JUL", None),
            _page(3, "100003", None, None, "26"),
            _page(4, "100004", "20", None, None),
            _page(5, "100005", None, "JUL", None),
            _page(6, "100006", None, None, "26"),
        ]
        correct_dates_by_book([_report(*pages)])
        for p in pages:
            self.assertEqual(p.date, "2026/07/20", msg=f"page {p.page_number}")
        # Las piezas rellenadas llevan la nota de inferencia.
        day_field = next(f for f in pages[2].fields if f.field_id == "day")
        self.assertIn("Inferred", day_field.comment)


if __name__ == "__main__":
    unittest.main()