"""Subida por Quick Upload: trozos y confirmacion de indices."""

from __future__ import annotations

from app.airvault.config import (
    CAMPO_END_DATE,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
)
from app.airvault.uploader import (
    CAMPOS_QUICK_UPLOAD,
    TROZO_BYTES,
    trozos,
    valores_quick_upload,
)


def test_solo_viajan_los_campos_que_quick_upload_admite():
    valores = {
        CAMPO_MATRICULA: "HP-1848CMP",
        CAMPO_LOG_NUMBER: "2287325",
        CAMPO_END_DATE: "08/31/2026",
    }
    enviados = valores_quick_upload(valores)
    ids = {int(v["FieldId"]) for v in enviados}
    assert ids == set(CAMPOS_QUICK_UPLOAD)
    # Log Page Number y End Date no estan habilitados en ese modulo, asi
    # que el indexado posterior sigue siendo obligatorio.
    assert CAMPO_LOG_NUMBER not in ids
    assert CAMPO_END_DATE not in ids


def test_la_matricula_si_viaja():
    enviados = valores_quick_upload({CAMPO_MATRICULA: "HP-1848CMP"})
    matricula = next(v for v in enviados
                     if int(v["FieldId"]) == CAMPO_MATRICULA)
    assert matricula["Value"] == "HP-1848CMP"
    assert matricula["Dirty"] is True


def test_campo_sin_valor_va_vacio_y_no_sucio():
    enviados = valores_quick_upload({})
    assert all(v["Value"] == "" for v in enviados)
    assert all(v["Dirty"] is False for v in enviados)


def test_archivo_pequeno_va_en_un_solo_trozo(tmp_path):
    ruta = tmp_path / "chico.pdf"
    ruta.write_bytes(b"x" * 100)
    partes = list(trozos(ruta))
    assert len(partes) == 1
    assert partes[0][0] == 0 and partes[0][1] == 1
    assert partes[0][2] == b"x" * 100


def test_archivo_grande_se_parte(tmp_path):
    ruta = tmp_path / "grande.pdf"
    ruta.write_bytes(b"y" * (TROZO_BYTES + 10))
    partes = list(trozos(ruta))
    assert [p[0] for p in partes] == [0, 1]
    assert all(p[1] == 2 for p in partes)
    assert b"".join(p[2] for p in partes) == b"y" * (TROZO_BYTES + 10)


def test_archivo_vacio_manda_un_trozo(tmp_path):
    ruta = tmp_path / "vacio.pdf"
    ruta.write_bytes(b"")
    partes = list(trozos(ruta))
    assert len(partes) == 1 and partes[0][2] == b""


def test_la_subida_no_lleva_el_vuelo():
    """``Description`` es de cada pagina, no del batch.

    La subida clasifica el archivo entero con un solo juego de valores; el
    vuelo cambia de una bitacora a la siguiente y ponerlo ahi seria darle a
    las 400 paginas el vuelo de la primera. Quick Upload ni siquiera expone
    ese campo: lo escribe el indexado, pagina por pagina.
    """
    from app.airvault.config import CAMPO_DESCRIPCION
    from app.airvault.uploader import CAMPOS_QUICK_UPLOAD, valores_quick_upload

    assert CAMPO_DESCRIPCION not in CAMPOS_QUICK_UPLOAD
    salida = valores_quick_upload({CAMPO_DESCRIPCION: "CM137"})
    assert all(v["FieldId"] != str(CAMPO_DESCRIPCION) for v in salida)
