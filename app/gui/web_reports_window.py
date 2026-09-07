"""Ventana de consulta para los reportes de limpieza de AirVault."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.airvault.config import AIRVAULT_FILENAME, AirVaultConfig
from app.airvault.web_reports import (
    FILTRO_DUPLICADAS,
    FILTRO_MAL_INDEXADAS,
    TIPO_DUPLICADA,
    TIPO_MAL_INDEXADA,
    ClienteLogPageAudit,
    ConsultaCancelada,
    ExcepcionLogPageAudit,
)
from app.gui.responsive import fit_to_screen
from app.gui.tokens import TEXT_SECONDARY
from app.gui.widgets import (
    DATA_TABLE_QSS,
    align_vertical_scrollbar_to_header,
    configure_combo_box,
    size_columns_once,
    style_data_table,
    window_stylesheet,
)

WEB_REPORTS_TOOLTIP = (
    "Consulta en AirVault las páginas mal indexadas y duplicadas del reporte "
    "Log Page Audit. No modifica documentos."
)


class WebReportsWorker(QThread):
    """Consulta SSRS fuera del hilo de la interfaz."""

    avance = Signal(str)
    resultado = Signal(object)
    fallo = Signal(str)
    cancelado = Signal()

    def __init__(
        self,
        config: AirVaultConfig,
        desde: date,
        hasta: date,
        filtros: tuple[str, ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._desde = desde
        self._hasta = hasta
        self._filtros = filtros
        self._parar = False

    def cancelar(self) -> None:
        self._parar = True
        self.requestInterruption()

    def _cancelado(self) -> bool:
        return self._parar or self.isInterruptionRequested()

    def run(self) -> None:  # noqa: D102 - lo describe la clase
        try:
            excepciones = ClienteLogPageAudit(self._config).consultar(
                self._desde,
                self._hasta,
                self._filtros,
                avisar=self.avance.emit,
                cancelar=self._cancelado,
            )
            if self._cancelado():
                self.cancelado.emit()
            else:
                self.resultado.emit(excepciones)
        except ConsultaCancelada:
            self.cancelado.emit()
        except Exception as exc:  # noqa: BLE001 - llega a la interfaz
            self.fallo.emit(str(exc))


class WebReportsWindow(QDialog):
    """Suite de consulta para limpiar excepciones de Web Reports."""

    COLUMNAS = (
        "Tipo",
        "Matrícula del libro",
        "Página",
        "Tipo de libro",
        "Rango del libro",
        "Rango de fechas",
        "Detalle",
    )

    def __init__(self, raiz: Path) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self._raiz = Path(raiz)
        self._config = AirVaultConfig.load(self._raiz / AIRVAULT_FILENAME)
        self._worker: Optional[WebReportsWorker] = None
        self._cerrar_al_terminar = False
        self._resultados: list[ExcepcionLogPageAudit] = []

        self.setWindowTitle("Web Reports")
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self._densidad = fit_to_screen(self, 1080, 680)
        self.setStyleSheet(
            window_stylesheet(DATA_TABLE_QSS + self._densidad.qss)
        )
        self._build_ui()

    def _build_ui(self) -> None:
        cuerpo = QVBoxLayout(self)
        margen = max(8, self._densidad.window_margin)
        cuerpo.setContentsMargins(margen, margen, margen, margen)
        cuerpo.setSpacing(self._densidad.root_spacing)

        consulta = QGroupBox("Log Page Audit")
        grid = QGridLayout(consulta)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(self._densidad.group_spacing)

        hoy = QDate.currentDate()
        inicio = QDate(hoy.year(), hoy.month(), 1)
        grid.addWidget(QLabel("Desde:"), 0, 0)
        self.desde_edit = self._fecha(inicio)
        grid.addWidget(self.desde_edit, 0, 1)
        grid.addWidget(QLabel("Hasta:"), 0, 2)
        self.hasta_edit = self._fecha(hoy)
        grid.addWidget(self.hasta_edit, 0, 3)
        grid.addWidget(QLabel("Mostrar:"), 0, 4)
        self.filtro_combo = QComboBox()
        self.filtro_combo.addItem(
            "Mal indexadas y duplicadas",
            (FILTRO_MAL_INDEXADAS, FILTRO_DUPLICADAS),
        )
        self.filtro_combo.addItem(
            "Solo mal indexadas", (FILTRO_MAL_INDEXADAS,)
        )
        self.filtro_combo.addItem("Solo duplicadas", (FILTRO_DUPLICADAS,))
        configure_combo_box(self.filtro_combo, 26)
        grid.addWidget(self.filtro_combo, 0, 5)
        grid.setColumnStretch(5, 1)

        ayuda = QLabel(
            "M & E Integration. La consulta usa For Export = Yes para "
            "repetir los datos del libro y Refresh = Yes para regenerar el "
            "reporte."
        )
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet(f"color: {TEXT_SECONDARY};")
        grid.addWidget(ayuda, 1, 0, 1, 6)
        cuerpo.addWidget(consulta)

        self.tabla = QTableWidget(0, len(self.COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(list(self.COLUMNAS))
        self.tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tabla.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tabla.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        self.tabla.setToolTip(
            "Resultados de Log Page Audit. Esta vista no cambia nada en "
            "AirVault."
        )
        style_data_table(self.tabla)
        align_vertical_scrollbar_to_header(self.tabla)
        size_columns_once(self.tabla, stretch_last=True)
        cuerpo.addWidget(self.tabla, 1)

        self.resumen = QLabel(
            "Listo para consultar desde el primer día del mes hasta hoy."
        )
        self.resumen.setWordWrap(True)
        self.resumen.setStyleSheet(f"color: {TEXT_SECONDARY};")
        cuerpo.addWidget(self.resumen)

        fila_avance = QHBoxLayout()
        fila_avance.setSpacing(8)
        self.progreso = QProgressBar()
        self.progreso.setRange(0, 100)
        self.progreso.setValue(0)
        self.progreso.setTextVisible(False)
        fila_avance.addWidget(self.progreso, 1)
        self.boton_cancelar = QPushButton("Cancelar")
        self.boton_cancelar.setEnabled(False)
        self.boton_cancelar.clicked.connect(self._cancelar)
        fila_avance.addWidget(self.boton_cancelar)
        self.boton_consultar = QPushButton("Consultar")
        self.boton_consultar.setToolTip(WEB_REPORTS_TOOLTIP)
        self.boton_consultar.clicked.connect(self._consultar)
        fila_avance.addWidget(self.boton_consultar)
        cuerpo.addLayout(fila_avance)

    @staticmethod
    def _fecha(valor: QDate) -> QDateEdit:
        control = QDateEdit(valor)
        control.setCalendarPopup(True)
        control.setDisplayFormat("d/M/yyyy")
        control.setMinimumDate(QDate(2000, 1, 1))
        control.setMaximumDate(QDate.currentDate())
        return control

    def _consultar(self) -> None:
        if self.hilo() is not None:
            return
        desde = self.desde_edit.date().toPython()
        hasta = self.hasta_edit.date().toPython()
        if desde > hasta:
            self.resumen.setText(
                "La fecha inicial no puede ser posterior a la fecha final."
            )
            return
        filtros = tuple(self.filtro_combo.currentData())
        self._config = AirVaultConfig.load(self._raiz / AIRVAULT_FILENAME)
        worker = WebReportsWorker(
            self._config, desde, hasta, filtros, self
        )
        worker.avance.connect(self._al_avanzar)
        worker.resultado.connect(self._al_recibir)
        worker.fallo.connect(self._al_fallar)
        worker.cancelado.connect(self._al_cancelar)
        worker.finished.connect(self._al_terminar)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self._habilitar(False)
        self.progreso.setRange(0, 0)
        self.resumen.setText(
            "Abriendo Edge con la cuenta de trabajo. Si pide acceso, "
            "complételo en esa ventana."
        )
        worker.start()

    def _al_avanzar(self, texto: str) -> None:
        self.resumen.setText(texto)

    def _al_recibir(self, excepciones: object) -> None:
        self._resultados = list(excepciones)
        self._llenar_tabla(self._resultados)
        mal_indexadas = sum(
            resultado.tipo == TIPO_MAL_INDEXADA
            for resultado in self._resultados
        )
        duplicadas = sum(
            resultado.tipo == TIPO_DUPLICADA
            for resultado in self._resultados
        )
        if not self._resultados:
            self.resumen.setText(
                "El reporte no encontró páginas mal indexadas ni duplicadas "
                "en el rango seleccionado."
            )
        else:
            self.resumen.setText(
                f"Se encontraron {mal_indexadas} mal indexadas y "
                f"{duplicadas} duplicadas. Solo se muestran: no se ha "
                "modificado AirVault."
            )

    def _llenar_tabla(
        self, excepciones: list[ExcepcionLogPageAudit]
    ) -> None:
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(excepciones))
        for fila, excepcion in enumerate(excepciones):
            detalle = excepcion.detalle
            if excepcion.tipo == TIPO_DUPLICADA and excepcion.copias:
                detalle = f"{excepcion.copias} apariciones: {detalle}"
            elif excepcion.destino:
                detalle = (
                    f"Indexada como {excepcion.destino}: {detalle}"
                )
            valores = (
                excepcion.tipo,
                excepcion.matricula_libro,
                excepcion.log_number,
                excepcion.tipo_libro,
                excepcion.rango_libro,
                excepcion.rango_fechas,
                detalle,
            )
            for columna, valor in enumerate(valores):
                item = QTableWidgetItem(str(valor))
                item.setToolTip(str(valor))
                if columna == 0:
                    item.setData(Qt.ItemDataRole.UserRole, excepcion)
                self.tabla.setItem(fila, columna, item)
        self.tabla.setSortingEnabled(True)
        size_columns_once(self.tabla, stretch_last=True)

    def _al_fallar(self, mensaje: str) -> None:
        self.resumen.setText(f"No se pudo consultar Web Reports: {mensaje}")

    def _al_cancelar(self) -> None:
        self.resumen.setText("Consulta cancelada. No se modificó AirVault.")

    def _al_terminar(self) -> None:
        self._worker = None
        self._habilitar(True)
        self.progreso.setRange(0, 100)
        self.progreso.setValue(0)
        if self._cerrar_al_terminar:
            self._cerrar_al_terminar = False
            self.close()

    def _habilitar(self, habilitado: bool) -> None:
        for control in (
            self.desde_edit,
            self.hasta_edit,
            self.filtro_combo,
            self.boton_consultar,
        ):
            control.setEnabled(habilitado)
        self.boton_cancelar.setEnabled(not habilitado)

    def _cancelar(self) -> None:
        worker = self.hilo()
        if worker is None:
            return
        worker.cancelar()
        self.boton_cancelar.setEnabled(False)
        self.resumen.setText("Cancelando la consulta…")

    def hilo(self) -> Optional[QThread]:
        worker = self._worker
        if worker is None:
            return None
        try:
            return worker if worker.isRunning() else None
        except RuntimeError:
            return None

    def detener(self) -> None:
        worker = self.hilo()
        if worker is not None:
            worker.cancelar()

    def closeEvent(self, event) -> None:
        if self.hilo() is not None:
            self._cerrar_al_terminar = True
            self._cancelar()
            event.ignore()
            return
        super().closeEvent(event)
