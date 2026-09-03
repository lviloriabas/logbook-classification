"""La alineación no verificada anota la firma, pero no borra su lectura.

Sin ancla fiable la página no se transforma: el recorte cae donde lo pone la
plantilla, que es donde ya caía. Bajar la confianza por debajo de los dos
umbrales del campo dejaba ilegibles las cinco firmas de la página a la vez, y
con ellas el tipo de entrada, así que toda página mal alineada salía marcada
como discrepancia hubiera lo que hubiera en ella.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import process_page_image
from app.models.schemas import FieldResult, Status
from app.templates.schema import FieldTemplate, FieldType, Template
from app.vision.alignment import TransformResult
from app.validation.discrepancias import clasificar_lote
from app.models.schemas import ValidationReport


def _template() -> Template:
    return Template(
        name="fixture",
        fields=[
            FieldTemplate(id=campo, type=FieldType.SIGNATURE,
                          x=0.1 + 0.15 * indice, y=0.4, w=0.12, h=0.05)
            for indice, campo in enumerate((
                "pilot_signature", "captain_signature", "captain_license",
                "technician_license", "technician_signature",
            ))
        ],
    )


def _imagen() -> np.ndarray:
    """Página con algo de tinta: una hoja en blanco no llega al detector."""
    image = np.full((200, 240, 3), 255, dtype=np.uint8)
    image[20:60, 20:80] = 0
    return image


def _firma(field, page_number, **_kwargs) -> FieldResult:
    """Detector de mentira: toda firma sale presente y con lectura firme."""
    return FieldResult(
        page_number=page_number, field_id=field.id,
        field_type=FieldType.SIGNATURE.value, value="true",
        confidence=0.80, status=Status.OK, comment="Firma detectada",
        source="vision",
    )


def _procesar(alineacion_fiable: bool):
    config = AppConfig(dpi=200, deskew=False, align=True)
    transform = TransformResult(reliable=alineacion_fiable)
    with patch.object(
        pipeline_module, "detect_signature",
        side_effect=lambda _crop, field, page_number, dpi=200: _firma(
            field, page_number),
    ), patch.object(pipeline_module, "apply_transform",
                    side_effect=lambda image, _t: image), patch.object(
        pipeline_module, "ocr_regions", return_value=[]
    ):
        return process_page_image(
            _imagen(), 1, config, object(), _template(),
            reference=_imagen(), transform=transform,
            transform_reliable=alineacion_fiable,
        )


def test_sin_ancla_fiable_la_firma_conserva_su_confianza():
    page = _procesar(alineacion_fiable=False)

    assert page.alignment_quality == "low"
    for field in page.fields:
        assert field.confidence == 0.80, field.field_id
        assert field.status is Status.WARNING, field.field_id
        assert field.inference_method == "alignment_low"
        assert "Alineación no confiable" in field.comment


def test_una_pagina_mal_alineada_y_completa_no_es_discrepancia():
    """Antes lo era siempre: las cinco firmas quedaban por debajo del umbral."""
    page = _procesar(alineacion_fiable=False)
    reporte = ValidationReport(
        pdf_path="fixture.pdf", template_name="fixture", pages=[page],
    )

    assert clasificar_lote([reporte], _template()) == []
    assert page.discrepancy is False


def test_con_ancla_fiable_la_firma_no_lleva_marca():
    page = _procesar(alineacion_fiable=True)

    assert page.alignment_quality == "ok"
    for field in page.fields:
        assert field.status is Status.OK
        assert field.inference_method is None
        assert "Alineación" not in field.comment
