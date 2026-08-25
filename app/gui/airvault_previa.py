"""Vista previa de los batches de una entrega y de lo que lleva cada uno.

La ventana de AirVault enseña los batches que ya existen, pero hasta que se
pulsa «Subir a AirVault» no hay ninguno: el reparto se decide al preparar
los archivos, y hasta entonces no había forma de saber en cuántos batches
iba a quedar la ejecución ni qué bitácoras caían en cada uno. Aquí se
calcula ese mismo reparto sin escribir nada y se enseña antes de subir,
junto con los batches que ya están esperando en la cola.

De cada batch se puede abrir la lista de sus bitácoras: la página que
ocupará dentro del batch, su matrícula, su Log Page Number y su fecha. Es
lo que permite comprobar que una bitácora concreta va donde se espera sin
tener que abrir el PDF.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.airvault.model import EstadoRegistro, Registro
from app.gui.responsive import fit_to_screen
from app.gui.widgets import (
    PANE_STATUS_COLORS,
    align_vertical_scrollbar_to_header,
    style_data_table,
)

# El mismo gris con el que las dos ventanas escriben sus líneas de ayuda.
COLOR_AYUDA = "#57606a"
COLOR_HECHO = PANE_STATUS_COLORS["OK"]

NOMBRE_ESTADO_REGISTRO = {
    EstadoRegistro.PENDIENTE: "Por escribir",
    EstadoRegistro.ESCRITA: "Escrita",
    EstadoRegistro.OMITIDA: "Omitida",
    EstadoRegistro.ERROR: "Error",
}


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


def _origen(registro: Registro) -> str:
    """De qué archivo y página de la ejecución salió la bitácora."""
    if not registro.archivo_origen:
        return ""
    return f"{registro.archivo_origen}, p. {registro.pagina_origen}"


def _estado_de(registro: Registro) -> str:
    """Lo que hay que saber de la bitácora antes de escribirla.

    Manda lo que impide escribirla: un aviso deja la página bloqueada, así
    que decirlo importa más que repetir que sigue pendiente. Sin avisos vale
    su estado, que en una vista previa siempre es «por escribir» y en un
    batch ya trabajado dice cómo quedó.
    """
    partes = list(registro.avisos)
    if registro.duplicado:
        partes.append("duplicada")
    if registro.discrepancia:
        partes.append("discrepancia")
    if not partes:
        return NOMBRE_ESTADO_REGISTRO.get(
            registro.estado, str(registro.estado)
        )
    return "; ".join(partes)


def _tabla(columnas: Sequence[str], ayuda: str) -> QTableWidget:
    """Una tabla de datos con el aspecto de las de las dos ventanas."""
    tabla = QTableWidget(0, len(columnas))
    tabla.setHorizontalHeaderLabels(list(columnas))
    tabla.setToolTip(ayuda)
    tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tabla.setAlternatingRowColors(True)
    tabla.verticalHeader().setVisible(False)
    style_data_table(tabla)
    align_vertical_scrollbar_to_header(tabla)
    return tabla


class BitacorasDelBatch(QDialog):
    """Las bitácoras que van dentro de un batch, en el orden del PDF."""

    COLUMNAS = (
        "Página", "Matrícula", "Log Page", "Fecha", "Vuelo", "Origen",
        "Estado",
    )

    def __init__(
        self,
        nombre: str,
        registros: Sequence[Registro],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registros = list(registros)
        self.setWindowTitle(f"Bitácoras de {nombre or 'el batch'}")
        # Como el resto de los cuadros: el tamaño lo pone la pantalla, que
        # en un portátil bajo dejaría los botones fuera del borde.
        fit_to_screen(self, 760, 520)
        self._build_ui(nombre)

    def _build_ui(self, nombre: str) -> None:
        cuerpo = QVBoxLayout(self)

        bitacoras = [r for r in self._registros if not r.es_separador]
        separadores = len(self._registros) - len(bitacoras)
        resumen = (
            f"«{nombre}»: {_plural(len(bitacoras), 'bitácora', 'bitácoras')} "
            f"en {_plural(len(self._registros), 'página', 'páginas')} del "
            "batch."
        )
        if separadores:
            resumen += (
                f" Las otras {separadores} son páginas separadoras: no se "
                "indexan y se borran al terminar."
            )
        intro = QLabel(resumen)
        intro.setWordWrap(True)
        cuerpo.addWidget(intro)

        self.tabla = _tabla(
            self.COLUMNAS,
            "Cada bitácora con la página que ocupa dentro del batch, que es "
            "la misma que muestra Web Index.",
        )
        cabecera = self.tabla.horizontalHeader()
        for columna in range(len(self.COLUMNAS) - 1):
            cabecera.setSectionResizeMode(
                columna, QHeaderView.ResizeMode.ResizeToContents
            )
        cabecera.setSectionResizeMode(
            len(self.COLUMNAS) - 1, QHeaderView.ResizeMode.Stretch
        )
        self._llenar(bitacoras)
        cuerpo.addWidget(self.tabla, 1)

        fila = QHBoxLayout()
        fila.addStretch()
        self.boton_cerrar = QPushButton("Cerrar")
        self.boton_cerrar.clicked.connect(self.accept)
        fila.addWidget(self.boton_cerrar)
        cuerpo.addLayout(fila)

    def _llenar(self, bitacoras: Sequence[Registro]) -> None:
        self.tabla.setRowCount(0)
        for registro in bitacoras:
            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)
            celdas = (
                str(registro.pagina_batch or registro.seq),
                registro.matricula,
                registro.log_number,
                registro.fecha,
                registro.flight_number,
                _origen(registro),
                _estado_de(registro),
            )
            for columna, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if columna == 0:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if registro.estado is EstadoRegistro.ESCRITA:
                    # El mismo verde de la tabla de batches, y significa lo
                    # mismo: eso ya está escrito en AirVault.
                    item.setForeground(QColor(COLOR_HECHO))
                self.tabla.setItem(fila, columna, item)


class VistaPreviaBatches(QDialog):
    """En cuántos batches queda la entrega y qué lleva cada uno.

    Los que ya están en AirVault salen con su estado; los demás son los que
    se crearían al subir. Abrir esto no los prepara: la lista se calcula
    leyendo el índice de páginas y el CSV, y cerrarla no deja nada hecho.
    """

    COLUMNAS = ("Batch", "Páginas", "Bitácoras", "Estado")

    def __init__(
        self, previstos: Sequence, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._previstos = list(previstos)
        self.setWindowTitle("Vista previa de los batches")
        fit_to_screen(self, 720, 480)
        self._build_ui()

    def _build_ui(self) -> None:
        cuerpo = QVBoxLayout(self)

        por_subir = [p for p in self._previstos if not p.subido]
        bitacoras = sum(len(p.bitacoras) for p in self._previstos)
        intro = QLabel(
            f"{_plural(len(self._previstos), 'batch', 'batches')} con "
            f"{_plural(bitacoras, 'bitácora', 'bitácoras')} en total, "
            f"{len(por_subir)} sin subir todavía. Es el reparto que haría "
            "«Subir a AirVault» con el máximo de páginas elegido; mirarlo "
            "no prepara ni sube nada."
        )
        intro.setWordWrap(True)
        cuerpo.addWidget(intro)

        self.tabla = _tabla(
            self.COLUMNAS,
            "Batches de la entrega. Los que ya están en AirVault salen con "
            "su estado; los demás se crearían al subir.",
        )
        cabecera = self.tabla.horizontalHeader()
        cabecera.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        cabecera.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        cabecera.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        cabecera.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tabla.itemDoubleClicked.connect(self._abrir_bitacoras)
        self.tabla.itemSelectionChanged.connect(self._ajustar_boton)
        self._llenar()
        cuerpo.addWidget(self.tabla, 1)

        self.ayuda = QLabel(
            "Elija un batch para ver las bitácoras que lleva dentro."
        )
        self.ayuda.setStyleSheet(f"color: {COLOR_AYUDA};")
        self.ayuda.setWordWrap(True)
        cuerpo.addWidget(self.ayuda)

        fila = QHBoxLayout()
        self.boton_bitacoras = QPushButton("Ver las bitácoras…")
        self.boton_bitacoras.setEnabled(False)
        self.boton_bitacoras.setToolTip(
            "Abre la lista de las bitácoras del batch elegido, con la página "
            "que ocupa cada una dentro del batch."
        )
        self.boton_bitacoras.clicked.connect(self._abrir_bitacoras)
        fila.addWidget(self.boton_bitacoras)
        fila.addStretch()
        self.boton_cerrar = QPushButton("Cerrar")
        self.boton_cerrar.clicked.connect(self.accept)
        fila.addWidget(self.boton_cerrar)
        cuerpo.addLayout(fila)

        if self._previstos:
            self.tabla.selectRow(0)

    def _llenar(self) -> None:
        self.tabla.setRowCount(0)
        for previsto in self._previstos:
            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)
            celdas = (
                previsto.nombre or "(sin nombre)",
                str(previsto.paginas),
                str(len(previsto.bitacoras)),
                previsto.estado or "Por subir",
            )
            for columna, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if columna == 0:
                    item.setToolTip(previsto.nombre)
                if columna in (1, 2):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if previsto.subido:
                    item.setForeground(QColor(COLOR_HECHO))
                elif not previsto.existe:
                    # Gris es lo que todavía no existe en ninguna parte: el
                    # batch que solo está previsto.
                    item.setForeground(Qt.GlobalColor.gray)
                self.tabla.setItem(fila, columna, item)
        self.tabla.resizeColumnToContents(0)

    def _elegido(self):
        fila = self.tabla.currentRow()
        if fila < 0 or fila >= len(self._previstos):
            return None
        return self._previstos[fila]

    def _ajustar_boton(self) -> None:
        self.boton_bitacoras.setEnabled(self._elegido() is not None)

    def _abrir_bitacoras(self, *_args) -> None:
        previsto = self._elegido()
        if previsto is None:
            return
        BitacorasDelBatch(previsto.nombre, previsto.registros, self).exec()
