"""Las filas del CSV vistas por la tabla, sin un ítem por celda.

Una ejecución grande son unas 2.400 páginas por 86 columnas: más de 200.000
celdas. Con un ``QTableWidgetItem`` por celda, abrir el CSV costaba varios
segundos y cada clic en una cabecera movía los 200.000 ítems de sitio, así
que la ventana se quedaba sin responder y el orden parecía no aplicarse.

Aquí la tabla no guarda nada: lee del CSV que ya está en memoria y pinta
solo lo que se ve. Ordenar es reordenar una lista de índices, que es
instantáneo por grande que sea la ejecución.

La primera columna es la de las casillas. Vive aparte de los datos porque
es de la tabla, no del CSV: marcar páginas sueltas para borrarlas no puede
depender de que la vista resumida deje a la vista la columna que le tocara.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from app.gui.table_sort import sorted_row_order

# Columna de las casillas: siempre la primera y siempre visible.
CHECK_COLUMN = 0
# Ancho de esa columna. Solo lleva la casilla, así que se fija a mano: por
# contenido queda tan estrecha que la marca toca los bordes.
CHECK_COLUMN_WIDTH = 34

STATUS_COLORS = {
    "OK": "#1a7f37",
    "WARNING": "#9a6700",
    "ERROR": "#cf222e",
}


class CsvTableModel(QAbstractTableModel):
    """Filas y columnas del CSV abierto, con su casilla y sus colores."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._columns: list[str] = []
        self._rows: list[dict[str, str]] = []
        # Fila del CSV que ocupa hoy cada fila mostrada. Es lo único que
        # cambia al ordenar.
        self._order: list[int] = []
        self._checked: set[int] = set()
        self._status: Callable[[dict[str, str], str], str | None] = (
            lambda _row, _column: None
        )
        self._comment: Callable[[dict[str, str], str], str] = (
            lambda _row, _column: ""
        )

    # ── contenido ───────────────────────────────────────────────────────

    def set_lookups(
        self,
        status: Callable[[dict[str, str], str], str | None],
        comment: Callable[[dict[str, str], str], str],
    ) -> None:
        """Fija de dónde salen el color y el comentario de cada celda."""
        self._status = status
        self._comment = comment

    def set_content(
        self, columns: Sequence[str], rows: Sequence[dict[str, str]]
    ) -> None:
        """Cambia el CSV que se muestra y devuelve la tabla a su orden."""
        self.beginResetModel()
        self._columns = list(columns)
        self._rows = list(rows)
        self._order = list(range(len(self._rows)))
        self._checked = set()
        self.endResetModel()

    @property
    def columns(self) -> list[str]:
        return list(self._columns)

    @property
    def rows(self) -> list[dict[str, str]]:
        return self._rows

    def source_row(self, display_row: int) -> int:
        """Fila del CSV que se está mostrando en esa posición."""
        if 0 <= display_row < len(self._order):
            return self._order[display_row]
        return -1

    def display_row(self, source_row: int) -> int:
        """Dónde está hoy en la tabla una fila del CSV."""
        try:
            return self._order.index(source_row)
        except ValueError:
            return -1

    def column_of(self, name: str) -> int:
        """Columna de la tabla que muestra esa columna del CSV."""
        try:
            return self._columns.index(name) + 1
        except ValueError:
            return -1

    def name_of(self, column: int) -> str:
        """Columna del CSV que se ve en esa columna de la tabla."""
        indice = column - 1
        if 0 <= indice < len(self._columns):
            return self._columns[indice]
        return ""

    # ── casillas ────────────────────────────────────────────────────────

    def checked_source_rows(self) -> list[int]:
        """Filas del CSV marcadas, en el orden en que están en el CSV."""
        return sorted(self._checked)

    def clear_checked(self) -> None:
        if not self._checked:
            return
        self._checked = set()
        self._repaint_rows()

    def toggle_rows(self, display_rows: Iterable[int]) -> None:
        """Invierte la marca de esas filas mostradas, todas a la vez.

        Con varias elegidas manda la primera: si no estaba marcada, se
        marcan todas; si lo estaba, se desmarcan todas. Es lo que hace
        cualquier lista con casillas y evita que una selección mezclada
        quede medio marcada tras pulsar una sola vez.
        """
        filas = [
            self._order[fila]
            for fila in display_rows
            if 0 <= fila < len(self._order)
        ]
        if not filas:
            return
        marcar = filas[0] not in self._checked
        for origen in filas:
            if marcar:
                self._checked.add(origen)
            else:
                self._checked.discard(origen)
        self._repaint_rows()

    def _repaint_rows(self) -> None:
        if not self._order:
            return
        # La fila entera se repinta: marcarla la sombrea de un extremo al
        # otro, no solo donde está la casilla.
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._order) - 1, self.columnCount() - 1),
            [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.BackgroundRole],
        )

    # ── orden ───────────────────────────────────────────────────────────

    def column_values(self, column: int) -> list[str]:
        """Texto de esa columna para cada fila del CSV, en orden del CSV."""
        if column == CHECK_COLUMN:
            return [
                "1" if fila in self._checked else "0"
                for fila in range(len(self._rows))
            ]
        name = self.name_of(column)
        if not name:
            return [""] * len(self._rows)
        return [row.get(name, "") for row in self._rows]

    def apply_order(self, order: Sequence[int]) -> None:
        """Reubica las filas sin tocar los datos ni perder la selección."""
        nuevo = list(order)
        if nuevo == self._order:
            return
        self.layoutAboutToBeChanged.emit()
        anteriores = self.persistentIndexList()
        posicion = {origen: fila for fila, origen in enumerate(nuevo)}
        destinos = [
            self.index(
                posicion.get(self._order[indice.row()], indice.row()),
                indice.column(),
            )
            if 0 <= indice.row() < len(self._order)
            else indice
            for indice in anteriores
        ]
        self._order = nuevo
        self.changePersistentIndexList(anteriores, destinos)
        self.layoutChanged.emit()

    def sort_rows(self, column: int, descending: bool) -> None:
        """Ordena por una columna; la de las casillas, por lo marcado."""
        self.apply_order(
            sorted_row_order(self.column_values(column), descending)
        )

    def reset_order(self) -> None:
        """Devuelve la tabla al orden en que se generó el CSV."""
        self.apply_order(list(range(len(self._rows))))

    # ── API de Qt ───────────────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - API Qt
        return 0 if parent.isValid() else len(self._order)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - API Qt
        return 0 if parent.isValid() else len(self._columns) + 1

    def headerData(  # noqa: N802 - API Qt
        self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole
    ):
        if orientation is not Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if section == CHECK_COLUMN else self.name_of(section)
        if role == Qt.ItemDataRole.ToolTipRole and section == CHECK_COLUMN:
            return (
                "Marque las páginas que quiera juntar aunque no estén "
                "seguidas. Mientras haya alguna marcada, son esas las que se "
                "eliminan."
            )
        return None

    def flags(self, index):  # noqa: D102 - API Qt
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        if index.column() == CHECK_COLUMN:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: D102
        if not index.isValid():
            return None
        origen = self.source_row(index.row())
        if origen < 0:
            return None
        column = index.column()

        if column == CHECK_COLUMN:
            if role == Qt.ItemDataRole.CheckStateRole:
                return (
                    Qt.CheckState.Checked
                    if origen in self._checked
                    else Qt.CheckState.Unchecked
                )
            if role == Qt.ItemDataRole.UserRole:
                return origen
            return None

        if role == Qt.ItemDataRole.UserRole:
            return origen

        row = self._rows[origen]
        name = self.name_of(column)
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get(name, "")
        if role in (
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.ToolTipRole,
        ):
            status = self._status(row, name)
            if not status:
                return None
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor(STATUS_COLORS[status])
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#ffffff")
            comment = self._comment(row, name)
            return f"Estado: {status}" + (f"\n{comment}" if comment else "")
        return None

    def setData(  # noqa: N802 - API Qt
        self, index, value, role=Qt.ItemDataRole.CheckStateRole
    ) -> bool:
        if (
            not index.isValid()
            or index.column() != CHECK_COLUMN
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        self.toggle_rows([index.row()])
        return True
