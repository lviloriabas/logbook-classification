from pathlib import Path

from app.gui.fleet_editor import FleetStore, normalise_matricula
from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.fleet import verify_reports_against_fleet


def test_normalise_matricula_accepts_canonical_and_digits():
    assert normalise_matricula("hp1234cmp") == "HP-1234CMP"
    assert normalise_matricula("1234") == "HP-1234CMP"
    assert normalise_matricula("not-a-registration") == ""


def test_fleet_store_round_trip(tmp_path: Path):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1234CMP", "hp5678wwp", "bad"])
    assert store.load() == ["HP-1234CMP", "HP-5678WWP"]


def test_unknown_fleet_registration_is_warning(tmp_path: Path):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1234CMP"])
    page = PageResult(
        page_number=1,
        fields=[
            FieldResult(
                page_number=1,
                field_id="matricula",
                field_type="ocr",
                value="HP-9999CMP",
            )
        ],
    )
    report = ValidationReport(pdf_path="book.pdf", template_name="test", pages=[page])
    verify_reports_against_fleet([report], store.path)
    assert page.fields[0].status is Status.WARNING
    assert "lista de flota" in page.fields[0].comment
    assert report.summary["warning_pages"] == 1


def test_unique_fleet_match_corrects_handwritten_2_read_instead_of_7(
    tmp_path: Path,
):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1717CMP", "HP-1534CMP"])
    field = FieldResult(
        page_number=1,
        field_id="matricula",
        field_type="ocr",
        value="HP-1217CMP",
        raw_value="HP-1217CMP",
        confidence=0.82,
        status=Status.OK,
    )
    page = PageResult(page_number=1, fields=[field])
    report = ValidationReport(pdf_path="book.pdf", template_name="test", pages=[page])

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1717CMP"
    assert field.alternatives == ["HP-1217CMP"]
    assert field.source == "fleet_validation"
    assert field.inference_method == "fleet_unique_hamming"
    assert field.status is Status.WARNING


def test_ambiguous_nearby_fleet_match_does_not_correct(tmp_path: Path):
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1717CMP", "HP-1317CMP"])
    field = FieldResult(
        page_number=1,
        field_id="matricula",
        field_type="ocr",
        value="HP-1217CMP",
    )
    page = PageResult(page_number=1, fields=[field])
    report = ValidationReport(pdf_path="book.pdf", template_name="test", pages=[page])

    verify_reports_against_fleet([report], store.path)

    assert field.value == "HP-1217CMP"
    assert field.source == "direct"
    assert "no encontrada" in field.comment
