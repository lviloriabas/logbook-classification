"""Descarte de páginas repetidas o en blanco dentro de una corrida.

La ventana principal y el visor de CSV ofrecen lo mismo sobre los datos que
ya están procesados: quitar de la corrida las páginas que no aportan nada a
la entrega. Como el criterio tiene que ser el mismo en las dos, vive aquí y
no en ninguna de ellas.

Duplicada es toda aparición posterior de un ``log_number`` ya visto —la
primera se conserva, que es la que se entrega—, y en blanco la que el
pipeline marcó como tal al procesarla.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from app.models.schemas import ValidationReport
from app.validation.book_corrector import _recompute_summary
from app.validation.duplicates import detect_duplicate_log_pages


@dataclass(frozen=True)
class ResumenDepuracion:
    """Cuántas páginas caen por cada criterio.

    Una página repetida puede además estar en blanco. Se cuenta en los dos
    conteos, porque cada uno responde a «cuántas quita esta casilla», pero
    ``total`` no la suma dos veces: es lo que se elimina de verdad.
    """

    duplicadas: int
    en_blanco: int
    total: int

    def __bool__(self) -> bool:
        return self.total > 0


def _marcas_duplicadas(reports: Sequence[ValidationReport]) -> List[bool]:
    """Un indicador por página, en el orden en que están en los reportes."""
    return [item.duplicate for item in detect_duplicate_log_pages(reports)]


def contar_depuracion(
    reports: Sequence[ValidationReport],
    duplicados: bool,
    en_blanco: bool,
) -> ResumenDepuracion:
    """Cuenta lo que se quitaría, sin tocar los reportes.

    Se usa para llenar el diálogo antes de confirmar: el número que ve quien
    decide sale de los mismos reportes sobre los que después se borra.
    """
    marcas = _marcas_duplicadas(reports)
    repetidas = 0
    blancas = 0
    total = 0
    posicion = 0
    for report in reports:
        for page in report.pages:
            es_duplicada = marcas[posicion]
            posicion += 1
            if es_duplicada:
                repetidas += 1
            if page.blank:
                blancas += 1
            if (duplicados and es_duplicada) or (en_blanco and page.blank):
                total += 1
    return ResumenDepuracion(
        duplicadas=repetidas if duplicados else 0,
        en_blanco=blancas if en_blanco else 0,
        total=total,
    )


def depurar(
    reports: Sequence[ValidationReport],
    duplicados: bool,
    en_blanco: bool,
) -> tuple[List[ValidationReport], ResumenDepuracion]:
    """Quita las páginas marcadas y devuelve los reportes que quedan.

    Los reportes se modifican en el sitio, igual que el resto de los pasos
    que corrigen una corrida ya procesada, y se recalcula el resumen de cada
    uno para que el JSON y las estadísticas cuadren con sus páginas. El que
    se queda sin ninguna no entra en la lista devuelta: un PDF sin páginas no
    tiene nada que escribir.
    """
    resumen = contar_depuracion(reports, duplicados, en_blanco)
    if not resumen.total:
        return list(reports), resumen

    marcas = _marcas_duplicadas(reports)
    quedan: List[ValidationReport] = []
    posicion = 0
    for report in reports:
        conservadas = []
        for page in report.pages:
            es_duplicada = marcas[posicion]
            posicion += 1
            fuera = (duplicados and es_duplicada) or (en_blanco and page.blank)
            if not fuera:
                conservadas.append(page)
        if len(conservadas) != len(report.pages):
            report.pages = conservadas
            _recompute_summary(report)
        if conservadas:
            quedan.append(report)
    return quedan, resumen
