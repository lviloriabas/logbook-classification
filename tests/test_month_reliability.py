"""Regresiones de meses ambiguos, evidencia visual y límites de libro."""

import numpy as np
import pytest

from app.core.pipeline import _join_char_fields, process_page_image
from app.models.schemas import FieldResult, PageResult, Status
from app.ocr.date_ocr import read_date_slots
from app.ocr.month_evidence import rank_month_slots
from app.ocr.month_retry import retry_month
from app.utils.postprocess import MONTH_WORDS, _parse_month, apply_postprocess, month_candidates
from app.validation.date_corrector import correct_dates_by_book, _date_candidates
from test_char_fields import _char_template, _config, _page
from test_date_corrector import _page as book_page, _report, _field_of


def month_page(raw="", value=None, confidence=0.0, letters=("", "", ""), scores=(0.0, 0.0, 0.0)):
    month = FieldResult(page_number=1, field_id="month", field_type="ocr",
                        raw_value=raw, value=value, confidence=confidence,
                        status=Status.OK if value else Status.ERROR)
    cells = [FieldResult(page_number=1, field_id=f"month_{i + 1}", field_type="ocr",
                         raw_value=letter, value=letter or None, confidence=score)
             for i, (letter, score) in enumerate(zip(letters, scores))]
    return PageResult(page_number=1, fields=[month, *cells])


@pytest.mark.parametrize("word,number", MONTH_WORDS)
def test_all_months_and_languages_remain_exact(word, number):
    assert _parse_month(word) == number
    evidence = rank_month_slots([(ch, 0.9) for ch in word])
    assert _parse_month(evidence.word) == number
    assert len(evidence.candidates) == 1
    assert evidence.confidence >= 0.7


@pytest.mark.parametrize("raw,expected", [("JUX", {6, 7}), ("MAZ", {3, 5}), ("JUN JUL", {6, 7})])
def test_ties_keep_candidates_without_choosing_a_month(raw, expected):
    assert set(month_candidates(raw)) == expected
    assert _parse_month(raw) is None
    value, note = apply_postprocess("month", "month", raw)
    assert value == ""
    assert "ambiguo" in note


def test_weak_final_letter_is_not_hidden_by_two_confident_letters():
    evidence = rank_month_slots([("J", 0.99), ("U", 0.99), ("L", 0.1)])
    assert not evidence.word
    assert set(evidence.candidates) == {"JUN", "JUL"}
    assert evidence.decisive == (2,)


def test_two_spellings_of_december_are_one_month():
    evidence = rank_month_slots([("D", 0.9), ("", 0.0), ("C", 0.9)])
    assert _parse_month(evidence.word) == 12
    assert evidence.candidates == ("DIC",)


def test_slot_reader_keeps_digit_shapes_for_month_equivalences():
    from app.models.schemas import OcrResult
    class Engine:
        def __init__(self):
            self.letters = iter("30C")
        def recognize(self, image):
            return [OcrResult(text=next(self.letters), confidence=0.9)]
    reading = read_date_slots("month", "month", [np.zeros((20, 20), dtype=np.uint8)] * 3,
                              preprocess=False, engine=Engine())
    assert reading[0] == "JUL"


def test_fuzzy_month_cannot_claim_to_be_an_exact_reading():
    assert "fuzzy" in apply_postprocess("month", "month", "JOC")[1]


def test_global_and_cells_disagreement_keeps_both_months():
    page = month_page("JUN", "JUN", 0.9, tuple("JUL"), (0.9, 0.9, 0.9))
    _join_char_fields(page)
    assert page.fields[0].value is None
    assert set(page.fields[0].alternatives) == {"JUN", "JUL"}


@pytest.mark.parametrize("raw,confidence,letters,scores", [
    ("50C", 0.735, ("3", "0", "c"), (0.437, 0.594, 0.221)),
    ("5UL", 0.363, ("A", "G", "L"), (0.12, 0.321, 0.727)),
    ("JOC", 0.694, ("丁", "0", "C"), (0.37, 0.363, 0.488)),
])
def test_real_july_readings_combine_complementary_strokes(raw, confidence, letters, scores):
    # Evidencia registrada de p150, p375 y p064 de las imágenes etiquetadas.
    page = month_page(raw, None, confidence, letters, scores)
    _join_char_fields(page)
    assert page.fields[0].value == "JUL"
    assert page.fields[0].raw_value == raw


def test_pipeline_keeps_ambiguous_candidates_without_cells(monkeypatch):
    import app.core.pipeline as module
    template = _char_template()
    template.fields = [field for field in template.fields if field.id in {"day", "month", "year"}]
    monkeypatch.setattr(module, "ocr_regions", lambda *args, **kwargs: [("20", .9), ("JUX", .9), ("26", .9)])
    page = process_page_image(_page(), 1, _config(), object(), template, None)
    month = _field_of(page, "month")
    assert month.value is None
    assert set(month.alternatives) == {"JUN", "JUL"}
    assert page.date is None


def test_retry_reads_only_whole_month_and_decisive_letter(monkeypatch):
    import app.ocr.month_retry as module
    page = month_page("JUX", None, .9, tuple("JUX"), (.9, .9, .9))
    _join_char_fields(page)
    calls = []
    def reread(engine, image, fields, **kwargs):
        calls.append(([field.id for field in fields], kwargs))
        return [("JUN", .9), ("N", .9)]
    monkeypatch.setattr(module, "ocr_regions", reread)
    class Engine:
        def recognize_lines(self, images):
            raise AssertionError("La lectura está simulada")
    retry_month(page, _page(), _char_template(), Engine())
    assert calls[0][0] == ["month", "month_3"]
    assert calls[0][1]["preprocess"] is False
    month = page.fields[0]
    assert month.value == "JUN"
    assert month.raw_value == "JUX"
    assert month.inference_method == "month_retry"
    assert month.votes is None


def test_clear_month_does_not_trigger_extra_ocr(monkeypatch):
    import app.ocr.month_retry as module
    page = month_page("JUL", "JUL", .9)
    def unexpected(*args, **kwargs):
        raise AssertionError("Un mes firme no necesita relectura")
    monkeypatch.setattr(module, "ocr_regions", unexpected)
    retry_month(page, _page(), _char_template(), object())


def ambiguous_book_page(pn=2, log="2147302", day="30"):
    page = book_page(pn, log, day, None, "26")
    month = _field_of(page, "month")
    month.alternatives = ["JUN", "JUL"]
    month.status = Status.ERROR
    month.inference_method = "month_ambiguous"
    return page


def test_two_direct_dates_resolve_month_across_month_boundary():
    middle = ambiguous_book_page()
    before = book_page(1, "2147301", "29", "JUN", "26")
    after = book_page(3, "2147303", "01", "JUL", "26")
    correct_dates_by_book([_report(after, middle, before)])
    month = _field_of(middle, "month")
    assert month.value == "JUN"
    assert month.source == "inferred"
    assert month.inference_method == "log_number_month_candidates"
    assert month.status is Status.WARNING


def test_wide_interval_does_not_choose_the_nearest_month():
    middle = ambiguous_book_page(day="21")
    before = book_page(1, "2147301", "20", "JUN", "26")
    after = book_page(3, "2147303", "25", "JUL", "26")
    assert _date_candidates(middle) == []
    correct_dates_by_book([_report(before, middle, after)])
    assert _field_of(middle, "month").value is None


def test_ambiguous_month_never_uses_an_anchor_from_previous_book():
    middle = ambiguous_book_page(log="2147350")
    before = book_page(1, "2147349", "29", "JUN", "26")
    after = book_page(3, "2147351", "01", "JUL", "26")
    correct_dates_by_book([_report(before, middle, after)])
    assert _field_of(middle, "month").value is None


def test_inferred_date_is_not_an_independent_anchor():
    middle = ambiguous_book_page()
    before = book_page(1, "2147301", "29", "JUN", "26")
    _field_of(before, "month").source = "inferred"
    after = book_page(3, "2147303", "01", "JUL", "26")
    correct_dates_by_book([_report(before, middle, after)])
    assert _field_of(middle, "month").value is None
