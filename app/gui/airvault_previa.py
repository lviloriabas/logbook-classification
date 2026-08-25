"""Vista previa de los batches de una entrega y de lo que lleva cada uno.

La ventana de AirVault enseña los batches que ya existen, pero hasta que se
pulsa «Subir a AirVault» no hay ninguno: el reparto se decide al preparar
los archivos, y hasta entonces no había forma de saber en cuántos batches
iba a quedar la ejecución ni qué bitácoras caían en cada uno. Aquí se
calcula ese mismo reparto sin escribir nada y se enseña antes de subir,
junto con los batches que ya están esperando en la cola.

De cada batch se puede abrir la lista de sus bitácoras. Esa lista se mira
como el visor de CSV, en compacto: la página de la bitácora a la izquierda,
la tabla a la derecha, un buscador encima y las columnas ordenables con un
clic en su cabecera. Comprobar que una bitácora concreta va donde se espera
no obliga entonces a abrir el PDF por fuera: se elige su fila y la hoja
escaneada aparece al lado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.airvault.model import EstadoRegistro, Registro
from app.gui.responsive import fit_to_screen
from app.gui.table_sort import ColumnSortController
from app.gui.widgets import (
    PANE_STATUS_COLORS,
    align_vertical_scrollbar_to_header,
    style_data_table,
)

# El mismo gris con el que las dos ventanas escriben sus líneas de ayuda.
COLOR_AYUDA = "#57606a"
COLOR_HECHO = PANE_STATUS_COLORS["OK"]

# Reparto del ancho entre la página y la tabla, el mismo del visor de CSV.
_PANEL_PDF = 2
_TABLA = 3

NOMBRE_ESTADO_REGISTRO = {
    EstadoRegistro.PENDIENTE: "Por indexar",
    EstadoRegistro.ESCRITA: "Indexada",
    EstadoRegistro.OMITIDA: "Omitida",
    EstadoRegistro.ERROR: "Error",
}
# Lo que se escribió y además se dio por cerrado en AirVault. El batch
# completado ya salió de la cola y se fue a Web Search, así que decir de sus
# bitácoras que están «indexadas» se queda corto: no queda nada por hacerles.
NOMBRE_COMPLETADA = "Completada"


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


def _origen(registro: Registro) -> str:
    """De qué archivo y página de la ejecución salió la bitácora."""
    if not registro.archivo_origen:
        return ""
    return f"{registro.archivo_origen}, p. {registro.pagina_origen}"


def _estado_de(registro: Registro, completado: bool = False) -> str:
    """Lo que hay que saber de la bitácora antes de escribirla.

    Manda lo que impide escribirla: un aviso deja la página bloqueada, así
    que decirlo importa más que repetir que sigue pendiente. Sin avisos vale
    su estado, que en una vista previa siempre es «por indexar» y en un
    batch ya trabajado dice cómo quedó.
    """
    partes = list(registro.avisos)
    if registro.duplicado:
        partes.append("duplicada")
    if registro.discrepancia:
        partes.append("discrepancia")
    if partes:
        return "; ".join(partes)
    if completado and registro.estado is EstadoRegistro.ESCRITA:
        return NOMBRE_COMPLETADA
    return NOMBRE_ESTADO_REGISTRO.get(registro.estado, str(registro.estado))


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


class _ListaBuscable(QDialog):
    """Cuadro con una tabla que se busca y se ordena, como el visor de CSV.

    Las dos ventanas de la vista previa enseñan listas largas (los batches
    de una entrega, las bitácoras de un batch), así que las dos necesitan lo
    mismo: escribir un texto y que la fila aparezca, y ordenar por la
    columna que se quiera. Se resuelve aquí una vez para que las dos se
    manejen igual y con los mismos atajos.
    """

    #: Columnas donde busca el texto. Vacío significa todas.
    COLUMNAS_BUSCABLES: tuple[int, ...] = ()

    def _fila_de_busqueda(self, pista: str, ayuda: str) -> QHBoxLayout:
        self._coincidencias: list[int] = []
        self._posicion = -1
        self._texto_buscado = ""
        self._pista_busqueda = pista

        fila = QHBoxLayout()
        fila.addWidget(QLabel("Buscar:"))
        self.buscar_edit = QLineEdit()
        self.buscar_edit.setPlaceholderText(pista)
        self.buscar_edit.setToolTip(ayuda)
        self.buscar_edit.setAccessibleName("Texto que se busca en la lista")
        self.buscar_edit.returnPressed.connect(self._buscar)
        fila.addWidget(self.buscar_edit, 1)
        boton = QPushButton("Buscar")
        boton.setToolTip(
            "Buscar el texto; repetido, pasa a la coincidencia siguiente"
        )
        boton.clicked.connect(self._buscar)
        fila.addWidget(boton)
        self.buscar_anterior = QPushButton("‹")
        self.buscar_anterior.setToolTip("Coincidencia anterior")
        self.buscar_anterior.setEnabled(False)
        self.buscar_anterior.clicked.connect(lambda: self._mover_busqueda(-1))
        fila.addWidget(self.buscar_anterior)
        self.buscar_siguiente = QPushButton("›")
        self.buscar_siguiente.setToolTip("Coincidencia siguiente")
        self.buscar_siguiente.setEnabled(False)
        self.buscar_siguiente.clicked.connect(lambda: self._mover_busqueda(1))
        fila.addWidget(self.buscar_siguiente)
        self.busqueda_ayuda = QLabel(pista)
        self.busqueda_ayuda.setStyleSheet(f"color: {COLOR_AYUDA};")
        # Una frase larga pide de ancho mínimo la frase entera: se recorta
        # antes que empujar el separador y estrechar la página.
        self.busqueda_ayuda.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.busqueda_ayuda.setMinimumWidth(0)
        fila.addWidget(self.busqueda_ayuda, 1)
        # Ctrl+F desde cualquier punto del cuadro, como en el resto.
        QShortcut(
            QKeySequence.StandardKey.Find, self,
            activated=self.buscar_edit.setFocus,
        )
        return fila

    def _columnas_buscables(self) -> range | tuple[int, ...]:
        return self.COLUMNAS_BUSCABLES or range(self.tabla.columnCount())

    def _buscar(self) -> None:
        """Busca el texto escrito y lleva la tabla a la primera coincidencia.

        Repetirlo con el mismo texto avanza a la siguiente, igual que ›: es
        lo que se espera al volver a pulsar Intro sobre lo que ya se buscó.
        """
        texto = self.buscar_edit.text().strip()
        if (
            texto
            and texto.casefold() == self._texto_buscado
            and self._coincidencias
        ):
            self._mover_busqueda(1)
            return
        self._texto_buscado = texto.casefold()
        self._coincidencias = []
        self._posicion = -1
        if not texto:
            self.busqueda_ayuda.setText(self._pista_busqueda)
            self._sincronizar_busqueda()
            return
        self._coincidencias = self._filas_con(self._texto_buscado)
        if not self._coincidencias:
            self.busqueda_ayuda.setText(f"«{texto}»: sin coincidencias.")
            self._sincronizar_busqueda()
            return
        self._posicion = 0
        self._mostrar_coincidencia()

    def _filas_con(self, texto: str) -> list[int]:
        """Filas que contienen el texto, las exactas primero."""
        exactas: list[int] = []
        parciales: list[int] = []
        for fila in range(self.tabla.rowCount()):
            for columna in self._columnas_buscables():
                item = self.tabla.item(fila, columna)
                valor = item.text().strip().casefold() if item else ""
                if valor == texto:
                    exactas.append(fila)
                    break
                if texto in valor:
                    parciales.append(fila)
                    break
        return exactas + parciales

    def _mover_busqueda(self, salto: int) -> None:
        if not self._coincidencias:
            return
        self._posicion = (self._posicion + salto) % len(self._coincidencias)
        self._mostrar_coincidencia()

    def _mostrar_coincidencia(self) -> None:
        fila = self._coincidencias[self._posicion]
        self.tabla.selectRow(fila)
        self.tabla.scrollToItem(self.tabla.item(fila, 0))
        self.busqueda_ayuda.setText(
            f"Coincidencia {self._posicion + 1} de "
            f"{len(self._coincidencias)}."
        )
        self._sincronizar_busqueda()

    def _sincronizar_busqueda(self) -> None:
        varias = len(self._coincidencias) > 1
        self.buscar_anterior.setEnabled(varias)
        self.buscar_siguiente.setEnabled(varias)

    def _olvidar_busqueda(self) -> None:
        """Las coincidencias son posiciones, y ordenar las mueve de sitio."""
        self._coincidencias = []
        self._posicion = -1
        self._texto_buscado = ""
        self.busqueda_ayuda.setText(self._pista_busqueda)
        self._sincronizar_busqueda()


class BitacorasDelBatch(_ListaBuscable):
    """Las bitácoras que van dentro de un batch, con su hoja al lado."""

    COLUMNAS = (
        "Página", "Matrícula", "Log Page", "Fecha", "Vuelo", "Origen",
        "Estado",
    )
    # El estado es una frase, no un dato que se busque; el resto sí.
    COLUMNAS_BUSCABLES = (0, 1, 2, 3, 4, 5)

    _PISTA = "Log Page, matrícula, fecha, archivo de origen…"

    def __init__(
        self,
        nombre: str,
        registros: Sequence[Registro],
        csv: Path | str = "",
        completado: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registros = list(registros)
        self._csv = Path(csv) if csv else None
        self._completado = bool(completado)
        # Bitácora que muestra cada fila de la tabla, en el orden en que se
        # llenó. Es lo que le pide el visor a la página que enseña.
        self._bitacoras: list[Registro] = []
        self.setWindowTitle(f"Bitácoras de {nombre or 'el batch'}")
        # Como el resto de los cuadros: el tamaño lo pone la pantalla, que
        # en un portátil bajo dejaría los botones fuera del borde.
        densidad = fit_to_screen(self, 1180, 700)
        self._build_ui(nombre, densidad)

    def _build_ui(self, nombre: str, densidad) -> None:
        from app.gui.csv_viewer import EmbeddedPdfViewer

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
            "la misma que muestra Web Index. Al elegir una fila se abre su "
            "hoja escaneada al lado; el doble clic la lleva al visor.",
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

        divisor = QSplitter(Qt.Orientation.Horizontal)
        divisor.setChildrenCollapsible(False)
        divisor.setHandleWidth(6)
        self.visor = EmbeddedPdfViewer(density=densidad)
        divisor.addWidget(self.visor)

        panel = QWidget()
        columna = QVBoxLayout(panel)
        columna.setContentsMargins(0, 0, 0, 0)
        columna.addLayout(
            self._fila_de_busqueda(
                self._PISTA,
                "Busca el texto en la lista de bitácoras. Cada coincidencia "
                "selecciona su fila y abre su hoja en el visor; se recorren "
                "con ‹ y ›, o repitiendo la búsqueda.",
            )
        )
        columna.addWidget(self.tabla, 1)
        divisor.addWidget(panel)
        divisor.setStretchFactor(0, _PANEL_PDF)
        divisor.setStretchFactor(1, _TABLA)
        cuerpo.addWidget(divisor, 1)
        self._divisor = divisor

        self.orden = ColumnSortController(self.tabla)
        self.orden.sortChanged.connect(self._olvidar_busqueda)
        self.tabla.itemSelectionChanged.connect(self._mostrar_la_elegida)
        self.tabla.itemDoubleClicked.connect(self._abrir_la_elegida)

        self._cargar_paginas(bitacoras)

        fila = QHBoxLayout()
        fila.addStretch()
        self.boton_cerrar = QPushButton("Cerrar")
        self.boton_cerrar.clicked.connect(self.accept)
        fila.addWidget(self.boton_cerrar)
        cuerpo.addLayout(fila)

        if self.tabla.rowCount():
            self.tabla.selectRow(0)

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self._repartir_ancho()

    def _repartir_ancho(self) -> None:
        """El mismo reparto del visor de CSV: dos quintos para la página.

        Los factores de estiramiento solo gobiernan el espacio sobrante, y
        la tabla pide de ancho lo que suman sus columnas: sin repartirlo a
        mano la página se quedaba en su mínimo.
        """
        libre = max(0, self._divisor.width() - self._divisor.handleWidth())
        panel = libre * _PANEL_PDF // (_PANEL_PDF + _TABLA)
        self._divisor.setSizes([panel, libre - panel])

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Para el hilo de render antes de destruir el cuadro."""
        self.visor.shutdown()
        super().closeEvent(event)

    def done(self, result: int) -> None:  # noqa: D102 - API Qt
        # «Cerrar» y Escape terminan el diálogo sin evento de cierre.
        self.visor.shutdown()
        super().done(result)

    def _llenar(self, bitacoras: Sequence[Registro]) -> None:
        self.tabla.setRowCount(0)
        self._bitacoras = list(bitacoras)
        for indice, registro in enumerate(self._bitacoras):
            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)
            celdas = (
                str(registro.pagina_batch or registro.seq),
                registro.matricula,
                registro.log_number,
                registro.fecha,
                registro.flight_number,
                _origen(registro),
                _estado_de(registro, self._completado),
            )
            for columna, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                # La fila lleva encima a qué bitácora corresponde: ordenar
                # la mueve de sitio y el visor tiene que seguir acertando.
                item.setData(Qt.ItemDataRole.UserRole, indice)
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

    def _cargar_paginas(self, bitacoras: Sequence[Registro]) -> None:
        """Ubica en disco las hojas escaneadas de las que salió cada fila."""
        from app.gui.csv_viewer import resolve_source_documents

        if self._csv is None:
            self.visor.load_paths([], [])
            return
        filas = [
            {
                "file": registro.archivo_origen,
                "page": str(registro.pagina_origen),
            }
            for registro in bitacoras
        ]
        rutas, documentos, faltan = resolve_source_documents(self._csv, filas)
        self.visor.load_paths(
            documentos,
            faltan,
            [
                (ruta, registro.pagina_origen)
                for ruta, registro in zip(rutas, bitacoras)
            ],
        )

    def _indice_elegido(self) -> int:
        """Bitácora que corresponde a la fila resaltada, ordenada o no."""
        fila = self.tabla.currentRow()
        item = self.tabla.item(fila, 0) if fila >= 0 else None
        indice = item.data(Qt.ItemDataRole.UserRole) if item else None
        return indice if isinstance(indice, int) else -1

    def _mostrar_la_elegida(self) -> None:
        indice = self._indice_elegido()
        if 0 <= indice < len(self._bitacoras):
            self.visor.show_page(indice + 1)

    def _abrir_la_elegida(self, *_args) -> None:
        """El doble clic vuelve a traer la hoja, aunque ya fuera la actual."""
        self._mostrar_la_elegida()


class VistaPreviaBatches(_ListaBuscable):
    """En cuántos batches queda la entrega y qué lleva cada uno.

    Los que ya están en AirVault salen con su estado; los demás son los que
    se crearían al subir. Abrir esto no los prepara: la lista se calcula
    leyendo el índice de páginas y el CSV, y cerrarla no deja nada hecho.
    """

    COLUMNAS = ("Batch", "Páginas", "Bitácoras", "Estado")

    _PISTA = "Nombre del batch, número de páginas, estado…"

    def __init__(
        self,
        previstos: Sequence,
        csv: Path | str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._previstos = list(previstos)
        self._csv = Path(csv) if csv else None
        self.setWindowTitle("Vista previa de los batches")
        fit_to_screen(self, 780, 520)
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
        cuerpo.addLayout(
            self._fila_de_busqueda(
                self._PISTA,
                "Busca el texto en la lista de batches. Cada coincidencia "
                "selecciona su fila; se recorren con ‹ y ›, o repitiendo la "
                "búsqueda.",
            )
        )
        cuerpo.addWidget(self.tabla, 1)
        self.orden = ColumnSortController(self.tabla)
        self.orden.sortChanged.connect(self._olvidar_busqueda)

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
            "que ocupa cada una dentro del batch y su hoja escaneada al lado."
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
        for indice, previsto in enumerate(self._previstos):
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
                item.setData(Qt.ItemDataRole.UserRole, indice)
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
        item = self.tabla.item(fila, 0) if fila >= 0 else None
        indice = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(indice, int) or indice >= len(self._previstos):
            return None
        return self._previstos[indice]

    def _ajustar_boton(self) -> None:
        self.boton_bitacoras.setEnabled(self._elegido() is not None)

    def _abrir_bitacoras(self, *_args) -> None:
        previsto = self._elegido()
        if previsto is None:
            return
        BitacorasDelBatch(
            previsto.nombre,
            previsto.registros,
            csv=self._csv or "",
            completado=bool(previsto.completado),
            parent=self,
        ).exec()
