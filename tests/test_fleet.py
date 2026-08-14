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
