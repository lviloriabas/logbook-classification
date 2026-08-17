"""Rango de páginas aplicado al lote completo, no a cada archivo.

Los controles anteriores ("primeros N archivos" y "primeras N páginas de
cada archivo") obligaban a razonar en dos ejes a la vez: para ver 40 páginas
había que adivinar cuántos archivos y cuántas páginas de cada uno, y el
resultado dependía del tamaño de cada bitácora. Aquí el lote se numera de
corrido —igual que ya lo navega el visor de la ventana principal— y un solo
rango 1-based decide qué páginas entran, sin importar en cuántos archivos
caigan.

Ejemplo con tres PDFs de 10, 20 y 5 páginas (35 en total): el rango 8–22
procesa las páginas 8–10 del primero y 1–12 del segundo, y no abre el
tercero.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PageRange:
    """Rango de páginas 1-based e inclusivo por ambos extremos.

    ``last=None`` significa "hasta la última página disponible", que es lo
    que se necesita tanto para el rango completo como para un "desde la 200
    en adelante" sin saber cuántas páginas trae el lote.
    """

    first: int = 1
    last: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "first", max(1, int(self.first)))
        if self.last is not None:
            object.__setattr__(self, "last", max(self.first, int(self.last)))

    @property
    def is_full(self) -> bool:
        """True cuando el rango no recorta nada del lote."""
        return self.first <= 1 and self.last is None

    @property
    def length(self) -> Optional[int]:
        """Páginas que abarca el rango, o None si está abierto por el final."""
        if self.last is None:
            return None
        return self.last - self.first + 1

    def clamped(self, count: int) -> Tuple[int, int]:
        """Ajusta el rango a un documento de ``count`` páginas.

        Devuelve ``(first, last)`` 1-based inclusivo. Si el rango queda fuera
        del documento devuelve un tramo vacío (``last < first``).
        """
        if count <= 0:
            return 1, 0
        last = count if self.last is None else min(count, self.last)
        return self.first, last

    def label(self) -> str:
        """Texto corto para logs y mensajes de la interfaz."""
        if self.is_full:
            return "todas las páginas"
        if self.last is None:
            return f"desde la página {self.first}"
        if self.first == self.last:
            return f"página {self.first}"
        return f"páginas {self.first}-{self.last}"

    @classmethod
    def parse(cls, text: str) -> "PageRange":
        """Interpreta ``N``, ``N-M``, ``N-`` o ``-M`` (guion o dos puntos).

        Raises:
            ValueError: si el texto no es un rango reconocible.
        """
        raw = str(text).strip().replace(":", "-").replace("–", "-")
        if not raw:
            raise ValueError("Rango de páginas vacío")
        if "-" not in raw:
            value = _positive_int(raw)
            return cls(first=value, last=value)
        start, _, end = raw.partition("-")
        start, end = start.strip(), end.strip()
        first = _positive_int(start) if start else 1
        last = _positive_int(end) if end else None
        if last is not None and last < first:
            raise ValueError(
                f"El final del rango ({last}) es menor que el inicio ({first})"
            )
        return cls(first=first, last=last)


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"Número de página inválido: {text!r}") from exc
    if value < 1:
        raise ValueError(f"Las páginas se numeran desde 1, no {value}")
    return value


@dataclass(frozen=True)
class FileSlice:
    """Tramo de un PDF del lote que cae dentro del rango global."""

    index: int  # posición del archivo en el lote original (0-based)
    path: Path
    pages: PageRange
    count: Optional[int] = None  # páginas del tramo, si ya se contaron

    @property
    def bounds(self) -> Tuple[int, Optional[int]]:
        """``(primera, última)`` dentro del archivo; última None = hasta el fin."""
        return self.pages.first, self.pages.last


def slice_batch(
    paths: Sequence[Path],
    counts: Sequence[int],
    page_range: Optional[PageRange] = None,
) -> List[FileSlice]:
    """Reparte un rango global entre los archivos que lo contienen.

    Args:
        paths: Archivos del lote, en el orden en que se numeran.
        counts: Páginas de cada archivo, alineado con ``paths``.
        page_range: Rango global; None procesa el lote entero.

    Returns:
        Un ``FileSlice`` por archivo que aporta al menos una página, en
        orden. Los archivos fuera del rango no aparecen.
    """
    selection = page_range or PageRange()
    slices: List[FileSlice] = []
    offset = 0  # páginas del lote que quedan antes del archivo actual
    for index, (path, count) in enumerate(zip(paths, counts)):
        count = max(0, int(count))
        start = offset + 1
        offset += count
        if count == 0 or offset < selection.first:
            continue
        if selection.last is not None and start > selection.last:
            break
        first_page = max(1, selection.first - start + 1)
        last_page = (
            count if selection.last is None
            else min(count, selection.last - start + 1)
        )
        if last_page < first_page:
            continue
        slices.append(FileSlice(
            index=index,
            path=Path(path),
            pages=PageRange(first=first_page, last=last_page),
            count=last_page - first_page + 1,
        ))
    return slices


def slice_paths(
    paths: Sequence[Path], page_range: Optional[PageRange] = None
) -> List[FileSlice]:
    """Como :func:`slice_batch`, contando las páginas de cada PDF.

    Con el rango completo no abre ningún archivo: devuelve tramos abiertos
    (``last=None``) y deja que cada Pipeline resuelva el final. Así un lote
    sin recorte no paga una pasada extra de apertura de PDFs.
    """
    selection = page_range or PageRange()
    if selection.is_full:
        return [
            FileSlice(index=index, path=Path(path), pages=PageRange())
            for index, path in enumerate(paths)
        ]

    from app.vision.pdf_loader import page_count

    counts: List[int] = []
    for path in paths:
        try:
            counts.append(page_count(Path(path)))
        except Exception:  # noqa: BLE001 - el pipeline reporta el PDF inválido
            counts.append(0)
    return slice_batch(paths, counts, selection)


def total_pages(slices: Sequence[FileSlice]) -> int:
    """Páginas que suman los tramos con recuento conocido."""
    return sum(item.count or 0 for item in slices)
