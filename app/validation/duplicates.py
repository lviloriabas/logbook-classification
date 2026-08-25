"""Deteccion de paginas de bitacora repetidas dentro de un batch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.models.schemas import ValidationReport
from app.validation.grouping import log_number


@dataclass(frozen=True)
class DuplicateLogPage:
    """Resultado de duplicidad para una pagina, en el orden del batch.

    ``duplicate`` dice que ese ``log_number`` aparece mas de una vez, y vale
    para **todas** sus apariciones: si la misma bitacora esta dos veces, las
    dos estan repetidas y las dos hay que mirarlas. Marcar solo la segunda
    obligaba a buscar a mano con cual chocaba.

    ``primera`` distingue la que aparece antes. La marca de repetida no dice
    cual sobra, y para borrar hace falta saberlo: el descarte automatico
    conserva la primera de cada grupo.
    """

    pdf_path: str
    page_number: int
    log_number: int | None
    duplicate: bool
    primera: bool = False

    @property
    def sobrante(self) -> bool:
        """Aparicion posterior: la que se quita al depurar sin elegir nada."""
        return self.duplicate and not self.primera


def detect_duplicate_log_pages(
    reports: Sequence[ValidationReport],
) -> list[DuplicateLogPage]:
    """Marca todas las apariciones de un ``log_number`` valido repetido.

    Hacen falta dos pasadas: hasta no haber recorrido el batch entero no se
    sabe si la primera aparicion tenia companyera, y esa primera se marca
    igual que las demas.

    Los valores ausentes o que no tengan exactamente siete digitos no se
    consideran duplicados: sin numero legible no hay con que comparar.
    """
    numeros = [
        (report, page, log_number(page))
        for report in reports
        for page in report.pages
    ]
    cuantas: dict[int, int] = {}
    for _report, _page, numero in numeros:
        if numero is not None:
            cuantas[numero] = cuantas.get(numero, 0) + 1

    vistos: set[int] = set()
    detected: list[DuplicateLogPage] = []
    for report, page, numero in numeros:
        repetida = numero is not None and cuantas.get(numero, 0) > 1
        detected.append(
            DuplicateLogPage(
                pdf_path=report.pdf_path,
                page_number=page.page_number,
                log_number=numero,
                duplicate=repetida,
                primera=repetida and numero not in vistos,
            )
        )
        if numero is not None:
            vistos.add(numero)
    return detected
