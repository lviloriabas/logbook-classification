"""Rango de páginas del batch completo repartido entre archivos."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.page_range import (
    FileSlice,
    PageRange,
    slice_batch,
    slice_paths,
    total_pages,
)

# Batch de referencia: 35 páginas repartidas en tres bitácoras.
PATHS = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]
COUNTS = [10, 20, 5]


def _tramos(first: int, last: int | None = None):
    return [
        (item.path.name, item.pages.first, item.pages.last)
        for item in slice_batch(PATHS, COUNTS, PageRange(first, last))
    ]


def test_the_full_range_covers_every_file_end_to_end():
    assert _tramos(1) == [("a.pdf", 1, 10), ("b.pdf", 1, 20), ("c.pdf", 1, 5)]


def test_no_range_is_the_same_as_the_full_range():
    assert slice_batch(PATHS, COUNTS) == slice_batch(
        PATHS, COUNTS, PageRange()
    )


def test_a_range_spanning_two_files_cuts_each_one_where_toca():
    """8-22 son las tres últimas de la primera y las doce de la segunda."""
    assert _tramos(8, 22) == [("a.pdf", 8, 10), ("b.pdf", 1, 12)]


def test_files_outside_the_range_do_not_appear():
    assert _tramos(31, 35) == [("c.pdf", 1, 5)]
    assert _tramos(1, 10) == [("a.pdf", 1, 10)]


def test_an_open_range_runs_to_the_end_of_the_batch():
    assert _tramos(28) == [("b.pdf", 18, 20), ("c.pdf", 1, 5)]


def test_a_single_page_lands_on_one_file():
    assert _tramos(11, 11) == [("b.pdf", 1, 1)]


def test_a_range_past_the_batch_selects_nothing():
    assert _tramos(36, 50) == []


def test_empty_files_do_not_consume_page_numbers():
    """Un PDF ilegible cuenta 0 páginas y no desplaza la numeración."""
    tramos = slice_batch(PATHS, [10, 0, 5], PageRange(9, 12))
    assert [(item.path.name, item.pages.first, item.pages.last)
            for item in tramos] == [("a.pdf", 9, 10), ("c.pdf", 1, 2)]


def test_the_slices_keep_the_position_in_the_original_batch():
    tramos = slice_batch(PATHS, COUNTS, PageRange(31))
    assert [item.index for item in tramos] == [2]


def test_the_selected_pages_add_up():
    assert total_pages(slice_batch(PATHS, COUNTS, PageRange(8, 22))) == 15
    assert total_pages(slice_batch(PATHS, COUNTS)) == 35


def test_the_range_is_normalized_on_creation():
    assert PageRange(0).first == 1
    assert PageRange(-5).first == 1
    # Un final por debajo del inicio se sube al inicio: una sola página.
    assert PageRange(10, 3).last == 10


def test_clamping_a_range_to_a_document():
    assert PageRange(1).clamped(8) == (1, 8)
    assert PageRange(3, 100).clamped(8) == (3, 8)
    first, last = PageRange(20).clamped(8)
    assert last < first  # tramo vacío: el rango cae fuera del documento
    assert PageRange(1).clamped(0) == (1, 0)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1-40", PageRange(1, 40)),
        ("200-", PageRange(200, None)),
        ("-50", PageRange(1, 50)),
        ("15", PageRange(15, 15)),
        (" 3 - 9 ", PageRange(3, 9)),
        ("3:9", PageRange(3, 9)),
    ],
)
def test_parsing_the_cli_range(text, expected):
    assert PageRange.parse(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "5-2", "1-x"])
def test_an_unusable_range_is_rejected(text):
    with pytest.raises(ValueError):
        PageRange.parse(text)


def test_the_label_reads_like_the_interface_shows_it():
    assert PageRange().label() == "todas las páginas"
    assert PageRange(1, 40).label() == "páginas 1-40"
    assert PageRange(200).label() == "desde la página 200"
    assert PageRange(7, 7).label() == "página 7"


def test_the_full_range_does_not_open_any_pdf(tmp_path):
    """Sin recorte no se paga una pasada de apertura para contar páginas."""
    missing = [tmp_path / "no_existe_1.pdf", tmp_path / "no_existe_2.pdf"]
    tramos = slice_paths(missing)
    assert tramos == [
        FileSlice(index=0, path=missing[0], pages=PageRange()),
        FileSlice(index=1, path=missing[1], pages=PageRange()),
    ]
    assert all(item.pages.is_full for item in tramos)
