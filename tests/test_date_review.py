"""Las fechas fuera del periodo habitual siempre pasan a revisión."""

from datetime import date

import pytest

from app.models.schemas import FieldResult, PageResult, Status
from app.utils.date_window import is_usual, month_is_possible, usual_start
from app.validation.date_review import review_date_window
from app.validation.date_corrector import correct_dates_by_book
from app.validation.page_status import ready_for_auto_index
from app.reports.organize import por_revisar
from test_date_corrector import _page, _report, _field_of

TODAY = date(2026, 9, 6)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr("app.utils.date_window.date", FixedDate)
    monkeypatch.setattr("app.validation.date_corrector.date", FixedDate)


@pytest.mark.parametrize("value,review", [
    ("2026/09/06", False), ("2026/09/01", False), ("2026/08/01", False),
    ("2026/08/31", False), ("2026/07/31", True), ("2025/09/06", True),
    ("2020/07/25", True), ("2026/09/07", True), ("2026/10/01", True),
    ("2027/01/01", True),
])
def test_temporal_review_keeps_the_read_date(value, review):
    page = PageResult(page_number=1, date=value)
    assert review_date_window(page) is review
    assert page.date_review is review
    assert page.date == value
    if review:
        assert por_revisar(page)


def test_previous_december_is_normal_when_processing_in_january():
    reference = date(2026, 1, 2)
    assert usual_start(reference) == date(2025, 12, 1)
    assert is_usual(date(2025, 12, 1), reference)
    assert not is_usual(date(2025, 11, 30), reference)


def test_previous_month_is_complete_even_at_the_end_of_a_long_month():
    assert is_usual(date(2026, 2, 1), date(2026, 3, 31))
    assert is_usual(date(2024, 2, 29), date(2024, 3, 31))
    assert not month_is_possible(2026, 0, TODAY)
    assert not month_is_possible(2026, 13, TODAY)


def test_old_book_is_reviewed_even_when_all_its_pages_agree():
    pages = [_page(1, "2147301", "20", "AGO", "24"),
             _page(2, "2147302", "21", "AGO", "24")]
    correct_dates_by_book([_report(*pages)])
    assert all(page.date_review for page in pages)
    assert [page.date for page in pages] == ["2024/08/20", "2024/08/21"]


def test_tomorrow_cannot_become_an_anchor_or_an_invented_today():
    page = _page(1, "2147301", "07", "SEP", "26")
    stats = correct_dates_by_book([_report(page)])
    day = _field_of(page, "day")
    assert stats["after_the_run"] == 1
    assert day.value is None
    assert "07" in day.alternatives
    assert day.inference_method == "day_out_of_window"
    assert page.date is None
    assert page.date_review


def test_future_month_remains_pending_without_support():
    page = _page(1, "2147301", "01", "OCT", "26")
    correct_dates_by_book([_report(page)])
    assert _field_of(page, "month").value is None
    assert page.date_review


def test_unread_day_is_never_filled_after_today():
    page = _page(1, "2147301", None, "SEP", "26")
    correct_dates_by_book([_report(page)])
    assert page.date == "2026/09/06"
    assert not page.date_review


def test_month_end_policy_does_not_claim_a_future_handwritten_day():
    page = _page(1, "2147301", None, "SEP", "26")
    day = _field_of(page, "day")
    day.inference_method = "month_end_policy"
    day.source = "csv_date_policy"
    correct_dates_by_book([_report(page)])
    assert page.date == "2026/09/30"
    assert not page.date_review
    # Esa representación no permite un mes futuro.
    page.date = "2026/10/31"
    assert review_date_window(page)


def test_correcting_the_date_clears_only_the_temporal_warning():
    page = PageResult(page_number=1, date="2020/07/25", comment="Revisar matrícula")
    review_date_window(page)
    review_date_window(page)
    assert page.comment.count("Fecha fuera") == 1
    page.date = "2026/08/25"
    review_date_window(page)
    assert not page.date_review
    assert page.comment == "Revisar matrícula"


def test_other_review_reasons_survive_the_temporal_check():
    page = PageResult(page_number=1, date="2026/08/25", date_review=True)
    review_date_window(page)
    assert page.date_review


def test_automatic_indexing_is_blocked_for_old_dates():
    page = _page(1, "2147301", "25", "JUL", "20")
    page.add_field(FieldResult(page_number=1, field_id="matricula", field_type="ocr",
                              value="HP-1534CMP", confidence=.9, status=Status.OK))
    correct_dates_by_book([_report(page)])
    assert not ready_for_auto_index(page)
    assert por_revisar(page)


def test_future_year_rejected_by_ocr_is_still_identified_as_future():
    page = _page(1, "2147301", "01", "ENE", None)
    _field_of(page, "year").raw_value = "28"
    assert review_date_window(page)
    assert "Fecha futura" in page.comment


@pytest.mark.parametrize("day,month", [("07", "SEP"), ("01", "OCT")])
def test_future_dates_are_never_saved_as_book_anchors(day, month):
    from app.validation.date_corrector import _confirmed_date
    page = _page(1, "2147301", day, month, "26")
    assert _confirmed_date(page) is None
