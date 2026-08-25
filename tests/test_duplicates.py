"""Deteccion de log_number repetidos dentro de una ejecución."""

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.validation.duplicates import detect_duplicate_log_pages


def _page(page_number: int, value: str | None) -> PageResult:
    page = PageResult(page_number=page_number)
    if value is not None:
        page.fields.append(
            FieldResult(
                page_number=page_number,
                field_id="log_number",
                field_type="ocr",
                value=value,
            )
        )
    return page


def test_every_occurrence_of_a_repeated_log_is_marked():
    """Las dos bitácoras repetidas se marcan, no solo la posterior.

    Marcando solo la segunda había que buscar a mano con cuál chocaba.
    """
    reports = [
        ValidationReport(
            pdf_path="first.pdf",
            template_name="fixture",
            pages=[_page(1, "2147300"), _page(2, "2147301")],
        ),
        ValidationReport(
            pdf_path="second.pdf",
            template_name="fixture",
            pages=[_page(4, "2147300"), _page(5, "2147300")],
        ),
    ]

    detected = detect_duplicate_log_pages(reports)

    # 2147300 sale tres veces y las tres quedan marcadas; 2147301 una sola.
    assert [item.duplicate for item in detected] == [True, False, True, True]
    assert detected[2].log_number == 2147300
    assert detected[2].pdf_path == "second.pdf"
    assert detected[2].page_number == 4


def test_only_the_first_occurrence_is_kept_when_purging():
    """Estar repetida no dice cuál sobra; para borrar hace falta saberlo."""
    reports = [
        ValidationReport(
            pdf_path="first.pdf",
            template_name="fixture",
            pages=[_page(1, "2147300"), _page(2, "2147301")],
        ),
        ValidationReport(
            pdf_path="second.pdf",
            template_name="fixture",
            pages=[_page(4, "2147300"), _page(5, "2147300")],
        ),
    ]

    detected = detect_duplicate_log_pages(reports)

    assert [item.primera for item in detected] == [True, False, False, False]
    assert [item.sobrante for item in detected] == [False, False, True, True]


def test_a_log_that_appears_once_is_neither_duplicate_nor_first():
    report = ValidationReport(
        pdf_path="fixture.pdf",
        template_name="fixture",
        pages=[_page(1, "2147300")],
    )

    detected = detect_duplicate_log_pages([report])

    assert not detected[0].duplicate
    assert not detected[0].primera
    assert not detected[0].sobrante


def test_missing_or_invalid_log_numbers_are_never_duplicates():
    report = ValidationReport(
        pdf_path="fixture.pdf",
        template_name="fixture",
        pages=[_page(1, None), _page(2, "123"), _page(3, "123")],
    )

    detected = detect_duplicate_log_pages([report])

    assert [item.log_number for item in detected] == [None, None, None]
    assert not any(item.duplicate for item in detected)
