"""Pruebas de agrupación de libros (regla: serie + mitad 00-49/50-99)
y del corrector agresivo de matrículas (un avión por libro)."""

from __future__ import annotations

import unittest

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.book_corrector import correct_matricula_by_book
from app.validation.grouping import book_key, group_books


def _field(pn: int, fid: str, value, status=Status.OK, conf=0.9) -> FieldResult:
    return FieldResult(page_number=pn, field_id=fid, field_type="ocr",
                       value=value, status=status, confidence=conf)


def _page(pn: int, log: str, mat: str, status=Status.OK, conf=0.9) -> PageResult:
    page = PageResult(page_number=pn)
    page.add_field(_field(pn, "log_number", log))
    page.add_field(_field(pn, "matricula", mat, status=status, conf=conf))
    return page


def _report(*pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path="fixture.pdf", template_name="fixture",
                            pages=list(pages))


def _matricula(page: PageResult) -> FieldResult:
    return next(f for f in page.fields if f.field_id == "matricula")


class TestBookKey(unittest.TestCase):
    def test_half_a(self):
        self.assertEqual(book_key(_page(1, "2147349", "HP-1534CMP")),
                         ("21473", "A"))
        self.assertEqual(book_key(_page(1, "2147300", "HP-1534CMP")),
                         ("21473", "A"))

    def test_half_b(self):
        self.assertEqual(book_key(_page(1, "2271650", "HP-1538CMP")),
                         ("22716", "B"))
        self.assertEqual(book_key(_page(1, "2271699", "HP-1538CMP")),
                         ("22716", "B"))

    def test_unreadable_returns_none(self):
        self.assertIsNone(book_key(_page(1, "22716", "HP-1538CMP")))
        self.assertIsNone(book_key(_page(1, None, "HP-1538CMP")))


class TestGroupBooks(unittest.TestCase):
    def test_books_split_by_prefix_and_half(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),  # 21473-A
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2271665", "HP-1538CMP"),  # 22716-B
            _page(4, "2271666", "HP-1538CMP"),
            _page(5, "2147314", "HP-1534CMP"),  # 21473-A de nuevo (fuera de orden)
        ]
        books = group_books([_report(*pages)])
        self.assertEqual(len(books), 2)
        # El mismo libro se reúne aunque reaparezca después de otro y se
        # ordena por log_number, no por el orden del PDF.
        self.assertEqual([p.page_number for p in books[0]], [5, 1, 2])
        self.assertEqual([p.page_number for p in books[1]], [3, 4])

    def test_unreadable_logpage_joins_current_book(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, None, "HP-1534CMP"),
            _page(3, "2147339", "HP-1534CMP"),
        ]
        books = group_books([_report(*pages)])
        self.assertEqual(len(books), 1)
        # La página sin log_number se conserva, pero no se coloca
        # artificialmente en medio de la secuencia.
        self.assertEqual([p.page_number for p in books[0]], [1, 3, 2])


class TestAggressiveCorrection(unittest.TestCase):
    def test_repeated_4_7_ambiguity_uses_raw_matricula_evidence(self):
        canonical_1414 = _page(1, "2073652", "HP-1414CMP", conf=0.59)
        raw_1717 = _page(
            2, "2073653", "", status=Status.ERROR, conf=0.78
        )
        _matricula(raw_1717).raw_value = "HP-1F17CMP"

        stats = correct_matricula_by_book([
            _report(canonical_1414, raw_1717)
        ])

        self.assertEqual(_matricula(canonical_1414).value, "HP-1717CMP")
        self.assertEqual(_matricula(raw_1717).value, "HP-1717CMP")
        self.assertIn("4/7 ambiguity", _matricula(canonical_1414).comment)
        self.assertIn("HP-1414CMP", _matricula(canonical_1414).alternatives)
        self.assertEqual(stats["flagged"], 1)
        self.assertEqual(stats["corrected"], 1)

    def test_repeated_4_7_can_repair_a_previously_wrong_book_correction(self):
        canonical_1414 = _page(1, "2073652", "HP-1414CMP", conf=0.59)
        old_correction = _page(2, "2073653", "HP-1414CMP", conf=0.59)
        old_field = _matricula(old_correction)
        old_field.raw_value = "HP-1F17CMP"
        old_field.source = "book_correction"

        correct_matricula_by_book([
            _report(canonical_1414, old_correction)
        ])

        self.assertEqual(_matricula(canonical_1414).value, "HP-1717CMP")
        self.assertEqual(_matricula(old_correction).value, "HP-1717CMP")
        self.assertEqual(
            _matricula(old_correction).inference_method,
            "book_handwritten_4_7",
        )

    def test_single_4_7_difference_does_not_change_matricula(self):
        canonical = _page(1, "2147337", "HP-1534CMP", conf=0.9)
        raw_hint = _page(2, "2147338", "", status=Status.ERROR, conf=0.9)
        _matricula(raw_hint).raw_value = "HP-153FCMP"

        correct_matricula_by_book([_report(canonical, raw_hint)])

        self.assertEqual(_matricula(canonical).value, "HP-1534CMP")
        self.assertEqual(_matricula(raw_hint).value, "HP-1534CMP")

    def test_garbage_and_different_values_overwritten(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2147339", "HP-1534CMP"),
            _page(4, "2147340", "HP-1534CMP"),
            _page(5, "2147341", "", status=Status.ERROR, conf=0.3),
            _page(6, "2147342", "HP-1734CMP"),  # formato válido, distinta
        ]
        stats = correct_matricula_by_book([_report(*pages)])
        self.assertEqual(stats["corrected"], 1)
        self.assertEqual(stats["flagged"], 1)
        for page in pages:
            self.assertEqual(_matricula(page).value, "HP-1534CMP")
        self.assertIn("Corrected from 'HP-1734CMP'",
                      _matricula(pages[5]).comment)

    def test_empty_value_inferred(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2147339", ""),
        ]
        correct_matricula_by_book([_report(*pages)])
        self.assertEqual(_matricula(pages[2]).value, "HP-1534CMP")
        self.assertIn("Inferred from book readings", _matricula(pages[2]).comment)

    def test_no_valid_reading_no_winner(self):
        pages = [
            _page(1, "2147337", "", status=Status.ERROR, conf=0.0),
            _page(2, "2147338", "", status=Status.ERROR, conf=0.0),
        ]
        correct_matricula_by_book([_report(*pages)])
        self.assertEqual(_matricula(pages[0]).value, "")
        self.assertEqual(_matricula(pages[1]).value, "")

    def test_low_confidence_does_not_vote(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2147339", "HP-1634CMP", conf=0.3),
        ]
        correct_matricula_by_book([_report(*pages)])
        self.assertEqual(_matricula(pages[2]).value, "HP-1534CMP")

    def test_books_do_not_leak_between_aircraft(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2271665", "HP-1538CMP"),
            _page(4, "2271666", "HP-1538CMP"),
        ]
        correct_matricula_by_book([_report(*pages)])
        self.assertEqual(_matricula(pages[0]).value, "HP-1534CMP")
        self.assertEqual(_matricula(pages[2]).value, "HP-1538CMP")


if __name__ == "__main__":
    unittest.main()
