"""Pruebas de agrupación de libros (regla: serie + mitad 00-49/50-99)
y del corrector agresivo de matrículas (un avión por libro)."""

from __future__ import annotations

import unittest

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.organize import por_revisar
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
    def test_rejected_reading_still_provides_evidence(self):
        # La página descartada por el formato conserva dígitos legibles:
        # su texto crudo pesa tanto como el de una lectura aceptada.
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
        self.assertIn("book consensus", _matricula(canonical_1414).comment)
        self.assertIn("HP-1414CMP", _matricula(canonical_1414).alternatives)
        self.assertEqual(stats["flagged"], 1)
        self.assertEqual(stats["corrected"], 1)

    def test_consensus_can_repair_a_previously_wrong_book_correction(self):
        # Confianzas del libro real 20736-B, donde el 1414 es la lectura
        # equivocada y la reconstruida es la buena.
        canonical_1414 = _page(1, "2073652", "HP-1414CMP", conf=0.536)
        old_correction = _page(2, "2073653", "HP-1414CMP", conf=0.707)
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
            "book_digit_consensus",
        )

    def test_single_ambiguous_character_does_not_change_matricula(self):
        canonical = _page(1, "2147337", "HP-1534CMP", conf=0.9)
        raw_hint = _page(2, "2147338", "", status=Status.ERROR, conf=0.9)
        _matricula(raw_hint).raw_value = "HP-153FCMP"

        correct_matricula_by_book([_report(canonical, raw_hint)])

        self.assertEqual(_matricula(canonical).value, "HP-1534CMP")
        self.assertEqual(_matricula(raw_hint).value, "HP-1534CMP")

    def test_confident_wrong_digit_loses_to_the_rest_of_the_book(self):
        # Caso real: el 7 manuscrito de HP-1719CMP se lee como 3 en la
        # única página que supera el formato. Con mayoría sobre la
        # matrícula completa esa página se imponía al libro entero; con el
        # voto por posición el 7 gana porque lo aportan varias páginas.
        pages = [
            _page(1, "2307043", "", status=Status.ERROR, conf=0.675),
            _page(2, "2307044", "HP-1319CMP", conf=0.684),
            _page(3, "2307045", "", status=Status.ERROR, conf=0.675),
            _page(4, "2307048", "", status=Status.ERROR, conf=0.675),
            _page(5, "2307049", "", status=Status.ERROR, conf=0.675),
        ]
        for page, raw in zip(pages, (
            "H0-1219", "4P-1319.C0P", "HP1119-CMC", "HPI7I0", "4o-1719cn0",
        )):
            _matricula(page).raw_value = raw

        correct_matricula_by_book([_report(*pages)])

        for page in pages:
            self.assertEqual(_matricula(page).value, "HP-1719CMP")

    def test_same_page_scanned_twice_votes_once(self):
        # El mismo log_number en dos PDF es una sola página física: si
        # contara dos veces, un error de OCR duplicado ganaría al resto.
        first = _report(
            _page(1, "2307044", "HP-1319CMP", conf=0.684),
            _page(2, "2307049", "HP-1719CMP", conf=0.675),
        )
        second = _report(_page(1, "2307044", "HP-1319CMP", conf=0.684))
        _page_three = _page(3, "2307048", "", status=Status.ERROR, conf=0.675)
        _matricula(_page_three).raw_value = "HPI7I0"
        first.pages.append(_page_three)

        correct_matricula_by_book([first, second])

        for report in (first, second):
            for page in report.pages:
                self.assertEqual(_matricula(page).value, "HP-1719CMP")

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
        # La inferencia se conserva, pero una lectura canónica distinta no
        # puede publicarse bajo el separador ganador sin que alguien la mire.
        self.assertIs(_matricula(pages[5]).status, Status.WARNING)
        self.assertTrue(por_revisar(pages[5]))

    def test_empty_value_inferred(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2147339", ""),
        ]
        correct_matricula_by_book([_report(*pages)])
        self.assertEqual(_matricula(pages[2]).value, "HP-1534CMP")
        self.assertIn("Inferred from book readings", _matricula(pages[2]).comment)
        self.assertIs(_matricula(pages[2]).status, Status.OK)
        self.assertFalse(por_revisar(pages[2]))

    def test_low_confidence_matching_reading_is_confirmed_by_the_book(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(
                3, "2147339", "HP-1534CMP",
                status=Status.WARNING, conf=0.40,
            ),
        ]

        correct_matricula_by_book([_report(*pages)])

        confirmed = _matricula(pages[2])
        self.assertEqual(confirmed.value, "HP-1534CMP")
        self.assertEqual(confirmed.votes, 3)
        self.assertEqual(
            confirmed.inference_method, "book_consensus_confirmation"
        )
        self.assertIs(confirmed.status, Status.OK)
        self.assertFalse(por_revisar(pages[2]))

    def test_single_reading_still_infers_but_goes_to_review(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", ""),
        ]

        correct_matricula_by_book([_report(*pages)])

        inferred = _matricula(pages[1])
        self.assertEqual(inferred.value, "HP-1534CMP")
        self.assertEqual(inferred.votes, 1)
        self.assertIs(inferred.status, Status.WARNING)
        self.assertTrue(por_revisar(pages[1]))

    def test_duplicate_scan_does_not_supply_a_second_autoindex_vote(self):
        blank = _page(2, "2147338", "")
        first = _report(
            _page(1, "2147337", "HP-1534CMP"),
            blank,
        )
        duplicate = _report(
            _page(1, "2147337", "HP-1534CMP"),
        )

        correct_matricula_by_book([first, duplicate])

        inferred = _matricula(blank)
        self.assertEqual(inferred.value, "HP-1534CMP")
        self.assertEqual(inferred.votes, 1)
        self.assertIs(inferred.status, Status.WARNING)
        self.assertTrue(por_revisar(blank))

    def test_low_confidence_consensus_still_infers_but_goes_to_review(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP", conf=0.49),
            _page(2, "2147338", "HP-1534CMP", conf=0.49),
            _page(3, "2147339", ""),
        ]

        correct_matricula_by_book([_report(*pages)])

        inferred = _matricula(pages[2])
        self.assertEqual(inferred.value, "HP-1534CMP")
        self.assertEqual(inferred.votes, 2)
        self.assertIs(inferred.status, Status.WARNING)
        self.assertTrue(por_revisar(pages[2]))

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
