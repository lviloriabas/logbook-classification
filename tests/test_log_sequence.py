"""El log_number que las páginas vecinas del PDF encierran sin hueco."""

from __future__ import annotations

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.organize import por_revisar
from app.validation.book_corrector import correct_matricula_by_book
from app.validation.log_sequence import (
    INFERENCE_METHOD,
    infer_log_numbers_from_pdf_order,
)


def _page(number: int, log: str | None, matricula: str = "HP-1534CMP",
          raw: str | None = None, blank: bool = False) -> PageResult:
    page = PageResult(page_number=number, blank=blank)
    page.add_field(FieldResult(
        page_number=number, field_id="log_number", field_type="ocr",
        value=log, raw_value=raw if raw is not None else log,
        confidence=0.9 if log else 0.2,
        status=Status.OK if log else Status.ERROR,
    ))
    page.add_field(FieldResult(
        page_number=number, field_id="matricula", field_type="ocr",
        value=matricula, raw_value=matricula, confidence=0.9,
    ))
    return page


def _report(*pages: PageResult, name: str = "lote.pdf") -> ValidationReport:
    return ValidationReport(pdf_path=name, template_name="t", pages=list(pages))


def _log(page: PageResult) -> FieldResult:
    return next(f for f in page.fields if f.field_id == "log_number")


def test_a_single_gap_between_neighbours_is_filled():
    pages = [_page(1, "2147310"), _page(2, None), _page(3, "2147312")]

    assert infer_log_numbers_from_pdf_order([_report(*pages)]) == 1

    field = _log(pages[1])
    assert field.value == "2147311"
    assert field.source == "inferred"
    assert field.inference_method == INFERENCE_METHOD
    assert field.status is Status.WARNING
    assert not por_revisar(pages[1])


def test_several_unread_pages_are_filled_when_the_gap_fits():
    pages = [_page(1, "2147310"), _page(2, None), _page(3, None),
             _page(4, "2147313")]

    infer_log_numbers_from_pdf_order([_report(*pages)])

    assert [_log(p).value for p in pages[1:3]] == ["2147311", "2147312"]


def test_a_gap_that_does_not_fit_is_left_alone():
    pages = [_page(1, "2147310"), _page(2, None), _page(3, "2147313")]

    assert infer_log_numbers_from_pdf_order([_report(*pages)]) == 0
    assert _log(pages[1]).value is None
    assert por_revisar(pages[1])


def test_neighbours_from_different_books_do_not_bracket():
    pages = [_page(1, "2147349"), _page(2, None), _page(3, "2147351")]

    infer_log_numbers_from_pdf_order([_report(*pages)])

    assert _log(pages[1]).value is None


def test_a_number_already_in_the_run_is_not_repeated():
    pages = [_page(1, "2147310"), _page(2, None), _page(3, "2147312")]
    elsewhere = _report(_page(1, "2147311"), name="otro.pdf")

    infer_log_numbers_from_pdf_order([_report(*pages), elsewhere])

    assert _log(pages[1]).value is None


def test_digits_the_ocr_did_read_must_not_contradict():
    contradicted = [_page(1, "2147310"), _page(2, None, raw="3310025"),
                    _page(3, "2147312")]
    partial = [_page(1, "2250020"), _page(2, "22500", raw="22500"),
               _page(3, "2250022")]

    infer_log_numbers_from_pdf_order([_report(*contradicted)])
    infer_log_numbers_from_pdf_order([_report(*partial, name="b.pdf")])

    assert _log(contradicted[1]).value is None
    assert _log(partial[1]).value == "2250021"
    assert "22500" in _log(partial[1]).alternatives


def test_blank_pages_between_neighbours_are_skipped():
    pages = [_page(1, "2147310"), _page(2, None, blank=True),
             _page(3, None), _page(4, "2147312")]

    infer_log_numbers_from_pdf_order([_report(*pages)])

    assert _log(pages[2]).value == "2147311"
    assert _log(pages[1]).value is None


def test_pages_outside_the_processed_range_are_not_neighbours():
    pages = [_page(1, "2147310"), _page(2, None), _page(5, "2147312")]

    infer_log_numbers_from_pdf_order([_report(*pages)])

    assert _log(pages[1]).value is None


def test_the_filled_page_joins_its_book_and_gets_its_registration():
    pages = [_page(1, "2147310"), _page(2, None, matricula=""),
             _page(3, "2147312")]

    infer_log_numbers_from_pdf_order([_report(*pages)])
    correct_matricula_by_book([_report(*pages)])

    matricula = next(f for f in pages[1].fields if f.field_id == "matricula")
    assert matricula.value == "HP-1534CMP"
    assert not por_revisar(pages[1])
