"""Ordenamiento por columna de tres estados para las tablas de CSV."""

from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QTableWidget

HEADER_TOOLTIP = (
    "Clic: ordena de mayor a menor · 2.º clic: de menor a mayor · "
    "3.er clic: vuelve al orden original"
)


def column_sort_key(text: str) -> tuple[int, float, str]:
    """Compara números como números y el resto como texto sin distinguir caja."""
    value = text.strip()
    try:
        number = float(value)
    except ValueError:
        return (1, 0.0, value.casefold())
    if not math.isfinite(number):
        return (1, 0.0, value.casefold())
    return (0, number, "")


def sorted_row_order(values: Sequence[str], descending: bool) -> list[int]:
    """Orden de filas para una columna; las celdas vacías quedan siempre al final."""
    filled = [index for index, value in enumerate(values) if value.strip()]
    blanks = [index for index, value in enumerate(values) if not value.strip()]
    filled.sort(key=lambda index: column_sort_key(values[index]), reverse=descending)
    return filled + blanks


class ColumnSortController(QObject):
    """Cicla cada columna entre mayor a menor, menor a mayor y orden original.

    El ordenamiento propio de ``QTableWidget`` solo alterna entre ascendente y
    descendente, así que nunca devuelve la tabla al orden en que se generó el
    CSV. Aquí las filas se reubican a mano para conservar colores, tooltips y
    datos de cada celda, y ``_order`` recuerda a qué fila original corresponde
    cada fila mostrada.
    """

    sortChanged = Signal()
    """Se emite cuando quien mira la tabla cambia el orden, no al rellenarla.

    Llenar la tabla la devuelve a su orden original, y eso no es una
    decisión de nadie: quien escuche esta señal para recordar el criterio
    no debe olvidarlo cada vez que la ejecución se vuelve a leer del disco.
    """

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table
        self._order: list[int] = []
        self._column = -1
        self._descending = False
        self._ready = True
        table.setSortingEnabled(False)
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.setToolTip(HEADER_TOOLTIP)
        header.sectionClicked.connect(self.cycle_column)
        self.reset()

    @property
    def sorted_column(self) -> int:
        """Columna ordenada, o ``-1`` cuando la tabla está en su orden original."""
        return self._column

    @property
    def descending(self) -> bool:
        """``True`` mientras la columna activa se muestre de mayor a menor."""
        return self._descending

    def row_order(self) -> list[int]:
        """Fila original que ocupa hoy cada fila mostrada."""
        return list(self._order)

    def reset(self) -> None:
        """Deja la tabla recién llenada lista para ordenarse, sin orden activo."""
        self._ready = True
        self._order = list(range(self._table.rowCount()))
        self._column = -1
        self._descending = False
        self._sync_indicator()

    def suspend(self) -> None:
        """Ignora los clics mientras la tabla se llena por batches."""
        self.reset()
        self._ready = False

    def cycle_column(self, column: int) -> None:
        """Avanza al siguiente estado de orden para ``column``."""
        if not self._ready or not 0 <= column < self._table.columnCount():
            return
        if len(self._order) != self._table.rowCount():
            # La tabla cambió sin avisar: se parte otra vez del orden original.
            self.reset()
        if column != self._column:
            self._column, self._descending = column, True
        elif self._descending:
            self._descending = False
        else:
            self._column, self._descending = -1, False
        self._apply()
        self.sortChanged.emit()

    def restore(self, column: int, descending: bool) -> None:
        """Vuelve a ordenar por ``column`` sin pasar por el ciclo de clics.

        La usa quien rellena la tabla otra vez con los mismos datos —al
        borrar páginas, por ejemplo— para devolverla al criterio que ya
        estaba puesto. Una columna fuera de rango deja la tabla como está,
        que es lo que corresponde cuando el CSV nuevo no la trae.
        """
        if not self._ready or not 0 <= column < self._table.columnCount():
            return
        self._column, self._descending = column, descending
        self._apply()

    def _apply(self) -> None:
        if self._column < 0:
            target = list(range(self._table.rowCount()))
        else:
            target = sorted_row_order(
                self._column_values(self._column), self._descending
            )
        self._reorder_rows(target)
        self._sync_indicator()

    def _column_values(self, column: int) -> list[str]:
        values = [""] * len(self._order)
        for display_row, original in enumerate(self._order):
            item = self._table.item(display_row, column)
            values[original] = item.text() if item is not None else ""
        return values

    def _reorder_rows(self, target: list[int]) -> None:
        if target == self._order:
            return
        table = self._table
        current_row = table.currentRow()
        selected = (
            self._order[current_row] if 0 <= current_row < len(self._order) else -1
        )
        columns = range(table.columnCount())
        table.setUpdatesEnabled(False)
        try:
            rows = {
                original: [table.takeItem(display_row, column) for column in columns]
                for display_row, original in enumerate(self._order)
            }
            for display_row, original in enumerate(target):
                for column, item in zip(columns, rows[original]):
                    if item is not None:
                        table.setItem(display_row, column, item)
        finally:
            table.setUpdatesEnabled(True)
        self._order = target
        if selected >= 0:
            table.setCurrentCell(target.index(selected), max(0, table.currentColumn()))

    def _sync_indicator(self) -> None:
        header = self._table.horizontalHeader()
        header.setSortIndicatorShown(self._column >= 0)
        header.setSortIndicator(
            self._column,
            Qt.SortOrder.DescendingOrder
            if self._descending
            else Qt.SortOrder.AscendingOrder,
        )
