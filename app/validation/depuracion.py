"""Descarte de páginas repetidas o en blanco dentro de una ejecución.

La ventana principal y el visor de CSV ofrecen lo mismo sobre los datos que
ya están procesados: quitar de la ejecución las páginas que no aportan nada a
la entrega. Como el criterio tiene que ser el mismo en las dos, vive aquí y
no en ninguna de ellas.

Duplicada es toda aparición posterior de un ``log_number`` ya visto (la
primera se conserva, que es la que se entrega), y en blanco la que el
pipeline marcó como tal al procesarla.

De una bitácora repetida se va una sola aparición, la más nueva, lo pida el
cuadro o lo haga el proceso automático: marcar el grupo entero no la borra
de la ejecución, se queda la primera.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

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
    """Un indicador por página, en el orden en que están en los reportes.

    Marca la aparición **sobrante**, no toda página repetida. La tabla y el
    CSV señalan las dos apariciones, porque las dos hay que mirarlas, pero
    borrar las dos dejaría la ejecución sin esa bitácora: lo que se quita
    por omisión es la posterior.
    """
    return [item.sobrante for item in detect_duplicate_log_pages(reports)]


@dataclass(frozen=True)
class PaginaDepurable:
    """Una página de la ejecución con lo que hace falta para elegirla.

    ``clave`` la identifica dentro de la ejecución que se está mirando: el
    reporte por su posición en la lista y la página por su número. No sirve
    entre ejecuciones distintas, y no falta que sirva: quien elige tiene
    delante una sola.
    """

    reporte: int
    pagina: int
    archivo: str
    log_number: int | None
    #: Aparición sobrante: la que se marca sola al encender el criterio. No
    #: es lo mismo que estar repetida, que lo están todas las del grupo.
    duplicada: bool
    en_blanco: bool

    @property
    def clave(self) -> tuple[int, int]:
        return (self.reporte, self.pagina)


def paginas_depurables(
    reports: Sequence[ValidationReport],
) -> List[PaginaDepurable]:
    """Todas las páginas de la ejecución, en el orden de los reportes."""
    marcas = _marcas_duplicadas(reports)
    numeros = [item.log_number for item in detect_duplicate_log_pages(reports)]
    paginas: List[PaginaDepurable] = []
    posicion = 0
    for indice, report in enumerate(reports):
        archivo = Path(report.pdf_path).name
        for page in report.pages:
            paginas.append(
                PaginaDepurable(
                    reporte=indice,
                    pagina=page.page_number,
                    archivo=archivo,
                    log_number=numeros[posicion],
                    duplicada=marcas[posicion],
                    en_blanco=bool(page.blank),
                )
            )
            posicion += 1
    return paginas


def grupos_duplicados(
    reports: Sequence[ValidationReport],
) -> List[tuple[int, List[PaginaDepurable]]]:
    """Cada ``log_number`` repetido con todas sus apariciones.

    Se devuelve el grupo entero, no solo las apariciones sobrantes: para
    decidir cuál se va hay que ver también la que se conservaría. El orden
    es el de la ejecución, así que la primera de cada lista es la que el
    borrado automático deja en pie.
    """
    grupos: dict[int, List[PaginaDepurable]] = {}
    for pagina in paginas_depurables(reports):
        if pagina.log_number is None:
            continue
        grupos.setdefault(pagina.log_number, []).append(pagina)
    return [
        (numero, paginas)
        for numero, paginas in sorted(grupos.items())
        if len(paginas) > 1
    ]


def claves_depurables(
    reports: Sequence[ValidationReport],
    claves: Iterable[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Las claves elegidas menos la que salva a cada bitácora repetida.

    De un ``log_number`` repetido se va la aparición más nueva y se queda la
    primera: de dos apariciones cae una sola. Si la elección se lleva el
    grupo entero (marcando las dos a mano, o porque una está además en
    blanco y cae por el otro criterio) se descarta de la elección la más
    antigua, que es la que la ejecución conserva: sin ella esa bitácora no
    quedaría en ninguna parte de la entrega.

    Pasa por aquí todo lo que se borra, venga del cuadro o del proceso
    automático, así que la ejecución no puede perder una bitácora repetida
    por ninguno de los dos caminos.
    """
    elegidas = set(claves)
    for _numero, paginas in grupos_duplicados(reports):
        if all(pagina.clave in elegidas for pagina in paginas):
            # La que se salva es la primera con algo escrito. Conservar una
            # página en blanco y borrar la que sí se lee sería cumplir la
            # regla y perder la bitácora igual.
            legibles = [pagina for pagina in paginas if not pagina.en_blanco]
            elegidas.discard((legibles or paginas)[0].clave)
    return elegidas


def _claves_por_criterio(
    reports: Sequence[ValidationReport],
    duplicados: bool,
    en_blanco: bool,
) -> set[tuple[int, int]]:
    """Lo que marcan las dos casillas, antes de proteger ninguna bitácora."""
    return {
        pagina.clave
        for pagina in paginas_depurables(reports)
        if (duplicados and pagina.duplicada) or (en_blanco and pagina.en_blanco)
    }


def paginas_en_blanco(
    reports: Sequence[ValidationReport],
) -> List[PaginaDepurable]:
    """Las páginas que el procesamiento marcó como vacías."""
    return [pagina for pagina in paginas_depurables(reports) if pagina.en_blanco]


def depurar_claves(
    reports: Sequence[ValidationReport],
    claves: Iterable[tuple[int, int]],
) -> tuple[List[ValidationReport], int]:
    """Quita las páginas indicadas y dice cuántas se fueron.

    Es la puerta que usa el cuadro cuando la elección se hizo página por
    página. ``depurar`` sigue existiendo para quien solo quiere aplicar los
    dos criterios enteros, y se apoya en esta.

    De cada bitácora repetida se respeta una aparición aunque lleguen
    marcadas todas: lo que se quita es la más nueva. Por eso el número que
    devuelve puede ser menor que las claves que recibió.
    """
    elegidas = claves_depurables(reports, claves)
    quedan: List[ValidationReport] = []
    quitadas = 0
    for indice, report in enumerate(reports):
        conservadas = [
            page
            for page in report.pages
            if (indice, page.page_number) not in elegidas
        ]
        quitadas += len(report.pages) - len(conservadas)
        if len(conservadas) != len(report.pages):
            report.pages = conservadas
            _recompute_summary(report)
        if conservadas:
            quedan.append(report)
    return quedan, quitadas


def contar_depuracion(
    reports: Sequence[ValidationReport],
    duplicados: bool,
    en_blanco: bool,
) -> ResumenDepuracion:
    """Cuenta lo que se quitaría, sin tocar los reportes.

    Se usa para llenar el diálogo antes de confirmar: el número que ve quien
    decide sale de los mismos reportes sobre los que después se borra y con
    la misma protección, así que una bitácora repetida que los dos criterios
    marcan entera cuenta una página menos: la que se conserva.
    """
    paginas = paginas_depurables(reports)
    elegidas = claves_depurables(
        reports, _claves_por_criterio(reports, duplicados, en_blanco)
    )
    repetidas = sum(
        1 for pagina in paginas if pagina.duplicada and pagina.clave in elegidas
    )
    blancas = sum(
        1 for pagina in paginas if pagina.en_blanco and pagina.clave in elegidas
    )
    return ResumenDepuracion(
        duplicadas=repetidas if duplicados else 0,
        en_blanco=blancas if en_blanco else 0,
        total=len(elegidas),
    )


def depurar(
    reports: Sequence[ValidationReport],
    duplicados: bool,
    en_blanco: bool,
) -> tuple[List[ValidationReport], ResumenDepuracion]:
    """Quita las páginas marcadas y devuelve los reportes que quedan.

    Los reportes se modifican en el sitio, igual que el resto de los pasos
    que corrigen una ejecución ya procesada, y se recalcula el resumen de cada
    uno para que el JSON y las estadísticas cuadren con sus páginas. El que
    se queda sin ninguna no entra en la lista devuelta: un PDF sin páginas no
    tiene nada que escribir.
    """
    resumen = contar_depuracion(reports, duplicados, en_blanco)
    if not resumen.total:
        return list(reports), resumen

    quedan, _quitadas = depurar_claves(
        reports, _claves_por_criterio(reports, duplicados, en_blanco)
    )
    return quedan, resumen
