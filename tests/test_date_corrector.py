"""Pruebas de inferencia de mes y ano guiada por ``log_number``."""

from __future__ import annotations

import unittest

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.date_corrector import correct_dates_by_book


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


def _page(
    pn: int,
    log: str | None,
    day=None,
    month=None,
    year=None,
    month_status=Status.OK,
    year_status=Status.OK,
) -> PageResult:
    page = PageResult(page_number=pn)
    page.add_field(_field(pn, "log_number", log))
    page.add_field(_field(pn, "day", day))
    page.add_field(_field(pn, "month", month, status=month_status))
    page.add_field(_field(pn, "year", year, status=year_status))
    return page


def _report(*pages: PageResult, name="fixture.pdf") -> ValidationReport:
    return ValidationReport(
        pdf_path=name,
        template_name="fixture",
        pages=list(pages),
    )


def _field_of(page: PageResult, field_id: str) -> FieldResult:
    return next(field for field in page.fields if field.field_id == field_id)


class TestLogNumberOrder(unittest.TestCase):
    def test_uses_log_number_not_pdf_order(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        middle = _page(2, "2147302", "20", None, None)
        last = _page(3, "2147303", "20", "JUL", "26")

        # El PDF llega en el orden 3, 1, 2. La inferencia debe seguir 1, 2, 3.
        correct_dates_by_book([_report(last, first, middle)])

        month = _field_of(middle, "month")
        year = _field_of(middle, "year")
        self.assertEqual(month.value, "JUL")
        self.assertEqual(year.value, "26")
        self.assertEqual(month.source, "inferred")
        self.assertEqual(year.source, "inferred")
        self.assertEqual(middle.date, "2026/07/20")


class TestMonthAndYearInference(unittest.TestCase):
    def test_infers_between_equal_anchors(self):
        before = _page(1, "2147301", "20", "JUL", "26")
        missing = _page(2, "2147302", "21", None, None)
        after = _page(3, "2147303", "22", "JUL", "26")

        stats = correct_dates_by_book([_report(before, missing, after)])

        self.assertEqual(_field_of(missing, "month").value, "JUL")
        self.assertEqual(_field_of(missing, "year").value, "26")
        self.assertEqual(missing.date, "2026/07/21")
        self.assertEqual(stats["months_filled"], 1)
        self.assertEqual(stats["years_filled"], 1)

    def test_does_not_infer_across_conflicting_month_anchors(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        middle = _page(2, "2147302", "21", None, None)
        conflicting = _page(3, "2147303", "22", "AUG", "26")
        last = _page(4, "2147304", "23", "JUL", "26")

        correct_dates_by_book([_report(first, middle, conflicting, last)])

        self.assertIsNone(_field_of(middle, "month").value)
        # El ano es independiente del conflicto de mes y si puede
        # inferirse entre las anclas 26.
        self.assertEqual(_field_of(middle, "year").value, "26")
        self.assertIsNone(middle.date)

    def test_short_edge_is_inferred_from_two_local_anchors(self):
        missing = _page(1, "2147301", "20", None, None)
        second = _page(2, "2147302", "20", "JUL", "26")
        third = _page(3, "2147303", "21", "JUL", "26")

        correct_dates_by_book([_report(missing, second, third)])

        self.assertEqual(_field_of(missing, "month").value, "JUL")
        self.assertEqual(_field_of(missing, "year").value, "26")
        self.assertEqual(missing.date, "2026/07/20")

    def test_invalid_three_digit_year_can_be_recovered(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        invalid = _page(
            2,
            "2147302",
            "20",
            "JUL",
            "216",
            year_status=Status.ERROR,
        )
        last = _page(3, "2147303", "20", "JUL", "26")

        correct_dates_by_book([_report(first, invalid, last)])

        year = _field_of(invalid, "year")
        self.assertEqual(year.value, "26")
        self.assertEqual(year.source, "inferred")
        self.assertEqual(invalid.date, "2026/07/20")

    def test_valid_conflicting_reading_is_preserved_and_flagged(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        conflicting = _page(2, "2147302", "21", "AUG", "26")
        last = _page(3, "2147303", "22", "JUL", "26")

        stats = correct_dates_by_book([_report(first, conflicting, last)])

        month = _field_of(conflicting, "month")
        self.assertEqual(month.value, "AUG")
        self.assertIs(month.status, Status.WARNING)
        self.assertEqual(month.source, "direct")
        self.assertGreater(stats["flagged"], 0)

    def test_warning_reading_does_not_become_an_anchor(self):
        doubtful = _page(
            1,
            "2147301",
            "20",
            "JUL",
            "26",
            month_status=Status.WARNING,
        )
        missing = _page(2, "2147302", "21", None, None)
        reliable = _page(3, "2147303", "22", "JUL", "26")

        correct_dates_by_book([_report(doubtful, missing, reliable)])

        self.assertIsNone(_field_of(missing, "month").value)
        self.assertEqual(_field_of(missing, "year").value, "26")

    def test_fuzzy_month_does_not_become_an_anchor(self):
        doubtful = _page(1, "2147301", "20", "DIC", "26")
        fuzzy = _field_of(doubtful, "month")
        fuzzy.comment = "month fuzzy: 50c"
        missing = _page(2, "2147302", "21", None, None)
        reliable = _page(3, "2147303", "22", "DIC", "26")

        correct_dates_by_book([_report(doubtful, missing, reliable)])

        self.assertIsNone(_field_of(missing, "month").value)

    def test_inferred_reading_does_not_feed_a_second_inference(self):
        inferred = _page(1, "2147301", "20", "JUL", "26")
        _field_of(inferred, "month").source = "inferred"
        _field_of(inferred, "year").source = "inferred"
        missing = _page(2, "2147302", "21", None, None)
        reliable = _page(3, "2147303", "22", "JUL", "26")

        correct_dates_by_book([_report(inferred, missing, reliable)])

        self.assertIsNone(_field_of(missing, "month").value)
        self.assertIsNone(_field_of(missing, "year").value)


class TestDayPolicy(unittest.TestCase):
    def test_day_is_never_inferred(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        missing_day = _page(2, "2147302", None, "JUL", "26")
        last = _page(3, "2147303", "20", "JUL", "26")

        stats = correct_dates_by_book([_report(first, missing_day, last)])

        day = _field_of(missing_day, "day")
        self.assertIsNone(day.value)
        self.assertIs(day.status, Status.WARNING)
        self.assertIsNone(missing_day.date)
        self.assertEqual(stats["days_filled"], 0)

    def test_missing_day_does_not_block_inferred_month_and_year(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        missing_day = _page(2, "2147302", None, None, None)
        last = _page(3, "2147303", "20", "JUL", "26")

        correct_dates_by_book([_report(first, missing_day, last)])

        self.assertEqual(_field_of(missing_day, "month").value, "JUL")
        self.assertEqual(_field_of(missing_day, "year").value, "26")
        self.assertIsNone(missing_day.date)


class TestSafetyBoundaries(unittest.TestCase):
    def test_unreadable_log_number_is_not_positionally_inferred(self):
        first = _page(1, "2147301", "20", "JUL", "26")
        unknown = _page(2, None, "20", None, None)
        last = _page(3, "2147303", "20", "JUL", "26")

        correct_dates_by_book([_report(first, unknown, last)])

        self.assertIsNone(_field_of(unknown, "month").value)
        self.assertIsNone(_field_of(unknown, "year").value)
        self.assertIsNone(unknown.date)

    def test_year_change_is_not_filled_across_conflicting_anchors(self):
        first = _page(1, "2147301", "31", "DIC", "25")
        missing = _page(2, "2147302", "01", "ENE", None)
        last = _page(3, "2147303", "02", "ENE", "26")

        correct_dates_by_book([_report(first, missing, last)])

        year = _field_of(missing, "year")
        self.assertIsNone(year.value)
        self.assertIs(year.status, Status.ERROR)

    def test_unresolved_date_is_explicit(self):
        page = _page(1, "2147301", None, None, None)

        correct_dates_by_book([_report(page)])

        self.assertIsNone(page.date)
        self.assertIn("unresolved", _field_of(page, "year").comment)


if __name__ == "__main__":
    unittest.main()
