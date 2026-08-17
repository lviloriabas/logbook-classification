from pathlib import Path

from app.gui.fleet_editor import FleetStore, normalise_matricula
from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.utils.fleet import load_fleet
from app.utils.postprocess import WWP_ONLY
from app.validation.fleet import verify_reports_against_fleet


def _report(value: str) -> tuple[ValidationReport, FieldResult, PageResult]:
    field = FieldResult(
        page_number=1,
        field_id="matricula",
        field_type="ocr",
        value=value,
        raw_value=value,
        confidence=0.82,
        status=Status.OK,
    )
    page = PageResult(page_number=1, fields=[field])
    report = ValidationReport(
        pdf_path="book.pdf", template_name="test", pages=[page]
    )
    return report, field, page


def test_normalise_matricula_accepts_canonical_and_digits():
    assert normalise_matricula("hp1234cmp") == "HP-1234CMP"
    assert normalise_matricula("1234") == "HP-1234CMP"
    assert normalise_matricula("not-a-registration") == ""


def test_fleet_store_round_trip(tmp_path: Path):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1234CMP", "hp5678wwp", "bad"])
    assert store.load() == ["HP-1234CMP", "HP-5678WWP"]


def test_shipped_fleet_list_agrees_with_the_wwp_exceptions():
    """La lista que se distribuye y ``WWP_ONLY`` describen la misma flota."""
    fleet = load_fleet(Path(__file__).resolve().parents[1] / "fleet.json")
    assert len(fleet) > 100
    wwp = {value[3:7] for value in fleet if value.endswith("WWP")}
    assert wwp == WWP_ONLY


def test_out_of_fleet_registration_is_reclassified_as_the_closest(
    tmp_path: Path,
):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1234CMP"])
    report, field, page = _report("HP-9999CMP")

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1234CMP"
    assert field.alternatives == ["HP-9999CMP"]
    assert field.status is Status.WARNING
    assert page.status is Status.WARNING
    assert report.summary["warning_pages"] == 1


def test_closest_fleet_match_corrects_handwritten_2_read_instead_of_7(
    tmp_path: Path,
):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1717CMP", "HP-1534CMP"])
    report, field, _ = _report("HP-1217CMP")

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1717CMP"
    assert field.alternatives == ["HP-1217CMP"]
    assert field.source == "fleet_validation"
    assert field.inference_method == "fleet_nearest_match"
    assert field.status is Status.WARNING


def test_confusable_stroke_wins_over_a_digit_that_looks_nothing_alike(
    tmp_path: Path,
):
    """A un dígito de distancia las dos, gana la que explica el trazo.

    ``HP-1217CMP`` sale de leer el 7 manuscrito como 2, no de leer un 3
    como 2: el par 2/7 se confunde en estas bitácoras y el par 2/3 no.
    """
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1717CMP", "HP-1317CMP"])
    report, field, _ = _report("HP-1217CMP")

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1717CMP"


def test_fleet_decides_the_suffix_when_only_it_differs(tmp_path: Path):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1522WWP", "HP-1521CMP"])
    report, field, _ = _report("HP-1522CMP")

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1522WWP"


def test_tied_fleet_matches_do_not_reclassify(tmp_path: Path):
    """Dos aviones igual de parecidos: elegir uno sería adivinar."""
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1717CMP", "HP-7217CMP"])
    report, field, _ = _report("HP-1217CMP")

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1217CMP"
    assert field.source == "direct"
    assert "misma distancia" in field.comment
    assert "HP-1717CMP" in field.comment and "HP-7217CMP" in field.comment


def test_reading_without_registration_format_is_only_flagged(tmp_path: Path):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1717CMP"])
    report, field, _ = _report("HP-17CMP")

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-17CMP"
    assert field.source == "direct"
    assert "no encontrada" in field.comment
