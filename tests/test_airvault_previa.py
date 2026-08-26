"""La vista previa del reparto: lo que se enseña antes de subir nada.

Lo que promete es que predice el reparto de verdad sin hacerlo, así que se
comprueba contra ``preparar_partes``: la misma ejecución, el mismo máximo
de páginas y los mismos nombres, cantidades y bitácoras. Si el reparto
cambia y la vista previa no, aquí se ve.

La ejecución de prueba se arma con el mismo constructor que usa la prueba
de la entrega: exporta un PDF de verdad, escribe su índice de páginas y su
CSV, que es lo único de lo que la vista previa saca sus datos.
"""

from __future__ import annotations

from pathlib import Path

from app.airvault.config import AirVaultConfig
from app.airvault.flujo import (
    ErrorDeCorrida,
    preparar_partes,
    previsualizar_reparto,
)
from tests.test_airvault_entrega import corrida


def archivos(carpeta: Path) -> set[Path]:
    """Todo lo que hay dentro, para comprobar que no aparece nada nuevo."""
    if not carpeta.exists():
        return set()
    return {ruta.relative_to(carpeta) for ruta in carpeta.rglob("*")}


def resumen(previstos) -> list[tuple[str, int, int]]:
    return [
        (p.nombre, p.paginas, len(p.bitacoras)) for p in previstos
    ]


def resumen_de_trabajos(trabajos) -> list[tuple[str, int, int]]:
    return [
        (
            t.manifiesto.nombre_batch,
            len(t.manifiesto.registros),
            len(t.manifiesto.bitacoras()),
        )
        for t in trabajos
    ]


def test_predice_el_reparto_que_haria_subir(tmp_path):
    """Es la promesa entera: lo previsto y lo preparado coinciden."""
    csv_path, _partes = corrida(tmp_path, paginas_por_parte=0)
    carpeta = tmp_path / "job"
    config = AirVaultConfig()

    previstos = previsualizar_reparto(
        config, carpeta, csv_path, paginas_por_batch=6
    )
    trabajos = preparar_partes(
        config, carpeta, csv_path, paginas_por_batch=6
    )

    assert resumen(previstos) == resumen_de_trabajos(trabajos)
    assert len(previstos) > 1, "con 12 paginas y tope de 6 hay varios batches"


def test_las_bitacoras_previstas_son_las_del_manifiesto(tmp_path):
    """Lo que se enseña de cada batch es lo que se le va a escribir."""
    csv_path, _partes = corrida(tmp_path)
    carpeta = tmp_path / "job"
    config = AirVaultConfig()

    previstos = previsualizar_reparto(config, carpeta, csv_path)
    trabajos = preparar_partes(config, carpeta, csv_path)

    previstas = [
        (r.seq, r.matricula, r.log_number, r.fecha)
        for r in previstos[0].bitacoras
    ]
    del_manifiesto = [
        (r.seq, r.matricula, r.log_number, r.fecha)
        for r in trabajos[0].manifiesto.bitacoras()
    ]
    assert previstas == del_manifiesto
    assert len(previstos[0].separadores) == 4


def test_mirar_la_vista_previa_no_deja_nada_escrito(tmp_path):
    """Se puede abrir tantas veces como se quiera antes de decidir."""
    csv_path, _partes = corrida(tmp_path)
    carpeta = tmp_path / "job"
    antes = archivos(tmp_path)

    previsualizar_reparto(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=6
    )
    previsualizar_reparto(
        AirVaultConfig(), carpeta, csv_path, paginas_por_batch=6
    )

    assert archivos(tmp_path) == antes
    assert not carpeta.exists(), "la vista previa no crea la carpeta de trabajo"


def test_lo_ya_preparado_sale_como_existente(tmp_path):
    """Un batch que ya tiene manifiesto se distingue del que solo se prevé."""
    csv_path, _partes = corrida(tmp_path)
    carpeta = tmp_path / "job"
    config = AirVaultConfig()

    assert not any(p.existe for p in previsualizar_reparto(
        config, carpeta, csv_path, paginas_por_batch=6
    ))
    preparar_partes(config, carpeta, csv_path, paginas_por_batch=6)
    previstos = previsualizar_reparto(
        config, carpeta, csv_path, paginas_por_batch=6
    )

    assert previstos and all(p.existe for p in previstos)
    # Preparado no es subido: sin ID en AirVault sigue siendo un pendiente.
    assert not any(p.subido for p in previstos)
    assert all(p.estado for p in previstos)


def test_una_ejecucion_sin_exportar_lo_dice(tmp_path):
    """Sin indice de paginas no hay reparto que prever."""
    carpeta = tmp_path / "job"
    datos = tmp_path / "BITS 20 AUG 2026 10 00" / "datos"
    datos.mkdir(parents=True)
    csv_path = datos / "BITS 20 AUG 2026 10 00.CSV"
    csv_path.write_text("file,page,log_number,matricula,date\n", encoding="utf-8")

    try:
        previsualizar_reparto(AirVaultConfig(), carpeta, csv_path)
    except ErrorDeCorrida as error:
        assert "exportar" in str(error)
    else:
        raise AssertionError("una ejecución sin PDF de entrega no se reparte")
