"""Ventana de consulta para los reportes de limpieza de AirVault."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.airvault.config import AIRVAULT_FILENAME, AirVaultConfig
from app.airvault.correcciones import (
    ACCION_REVISAR,
    Correccion,
    CorrectorLogPageAudit,
    planificar,
    resumen_del_plan,
)
from app.airvault.web_reports import (
    FILTRO_DUPLICADAS,
    FILTRO_MAL_INDEXADAS,
    TIPO_DUPLICADA,
    TIPO_MAL_INDEXADA,
    ClienteLogPageAudit,
    ConsultaCancelada,
    ExcepcionLogPageAudit,
    abrir_en_web_search,
)
from app.gui.responsive import fit_to_screen
from app.gui.tokens import (
    SPACE_L,
    SPACE_S,
    TEXT_SECONDARY,
    link_text_color,
)
from app.gui.widgets import (
    DATA_TABLE_QSS,
    align_vertical_scrollbar_to_header,
    configure_combo_box,
    size_columns_once,
    style_data_table,
    window_stylesheet,
)

# El mismo nombre que le dan la ventana de AirVault y la vista previa al
# gris de las frases de ayuda.
COLOR_AYUDA = TEXT_SECONDARY

# Con el que se enseñan las dos fechas y con el que se mide cuánto ocupan.
FORMATO_FECHA = "d/M/yyyy"

# La fecha más ancha que pueden llegar a enseñar: día y mes de dos cifras.
FECHA_MAS_LARGA = QDate(2000, 12, 30)

WEB_REPORTS_TOOLTIP = (
    "Consulta en AirVault las páginas mal indexadas y duplicadas del reporte "
    "Log Page Audit. No modifica documentos."
)

CORREGIR_TOOLTIP = (
    "Deja una sola copia de cada bitácora repetida (la más antigua) y "
    "devuelve las mal indexadas a la matrícula de su libro, en toda la "
    "tabla. Enseña el plan y pide autorización antes de escribir en "
    "AirVault."
)

CORREGIR_SELECCION_TOOLTIP = (
    "Lo mismo, pero solo en las filas elegidas. Con Ctrl o Mayús se eligen "
    "varias; sin ninguna elegida no hay nada que corregir."
)

# Las dos columnas que llevan a Web Search, y lo que abre cada una: la
# página, sus apariciones; el rango, el libro entero al que pertenece.
COLUMNA_BITACORA = 2
COLUMNA_RANGO_LIBRO = 4

# Donde cada una de esas celdas guarda su dirección. No se recalcula al
# pulsar: la tabla se ordena, y la fila que se pulsa ya no es la que trajo
# la consulta.
ROL_ENLACE = int(Qt.ItemDataRole.UserRole) + 1


def _ensanchar_hasta_la_fecha_mas_larga(campo: QDateEdit) -> None:
    """Deja sitio para 30/12/2025, no solo para las fechas que lo limitan.

    Qt pide el ancho del campo midiendo sus dos límites, y aquí los dos
    (1/1/2000 y el día de hoy) llevan día y mes de una cifra. Con ese ancho
    una fecha de dos y dos no entra: del día 10 en adelante el año se cortaba
    por el final.

    Se llama con el campo ya colgado de la ventana. Mientras el cuadro que lo
    contiene no cuelgue de ella, el campo no lleva puesta la hoja de estilo y
    el ancho que pide sale sin contar ni el relleno de los lados ni el pozo de
    la flecha del calendario.
    """
    fuente = campo.fontMetrics()
    limites = max(
        fuente.horizontalAdvance(fecha.toString(FORMATO_FECHA))
        for fecha in (campo.minimumDate(), campo.maximumDate())
    )
    falta = (
        fuente.horizontalAdvance(FECHA_MAS_LARGA.toString(FORMATO_FECHA))
        - limites
    )
    campo.setMinimumWidth(campo.sizeHint().width() + max(0, falta))


class _TrabajoEnEdge(QThread):
    """Lo que comparten los dos hilos que conducen el navegador.

    Consultar el reporte y corregir lo que dice son trabajos distintos, pero
    se cancelan igual, informan igual y acaban igual: quien los lanza no
    tiene por qué saber cuál de los dos está corriendo.
    """

    avance = Signal(str)
    resultado = Signal(object)
    fallo = Signal(str)
    cancelado = Signal()

    def __init__(self, config: AirVaultConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._parar = False

    def cancelar(self) -> None:
        self._parar = True
        self.requestInterruption()

    def _cancelado(self) -> bool:
        return self._parar or self.isInterruptionRequested()

    def _trabajar(self):
        """Lo propio de cada hilo. Devuelve lo que se emite al terminar."""
        raise NotImplementedError

    def run(self) -> None:  # noqa: D102 - lo describe la clase
        try:
            hecho = self._trabajar()
            if self._cancelado():
                self.cancelado.emit()
            else:
                self.resultado.emit(hecho)
        except ConsultaCancelada:
            self.cancelado.emit()
        except Exception as exc:  # noqa: BLE001 - llega a la interfaz
            self.fallo.emit(str(exc))


class WebReportsWorker(_TrabajoEnEdge):
    """Consulta SSRS fuera del hilo de la interfaz."""

    def __init__(
        self,
        config: AirVaultConfig,
        desde: date,
        hasta: date,
        filtros: tuple[str, ...],
        parent=None,
    ) -> None:
        super().__init__(config, parent)
        self._desde = desde
        self._hasta = hasta
        self._filtros = filtros

    def _trabajar(self):
        return ClienteLogPageAudit(self._config).consultar(
            self._desde,
            self._hasta,
            self._filtros,
            avisar=self.avance.emit,
            cancelar=self._cancelado,
        )


class CorreccionWorker(_TrabajoEnEdge):
    """Aplica el plan en AirVault fuera del hilo de la interfaz."""

    def __init__(
        self,
        config: AirVaultConfig,
        plan: list[Correccion],
        parent=None,
    ) -> None:
        super().__init__(config, parent)
        self._plan = list(plan)

    def _trabajar(self):
        # Sin ensayo porque a este hilo solo se llega después de que alguien
        # haya leído el plan y lo haya autorizado. Cada caso se sigue
        # comprobando contra la pantalla antes de escribir nada.
        return CorrectorLogPageAudit(self._config).aplicar(
            self._plan,
            avisar=self.avance.emit,
            cancelar=self._cancelado,
            ensayo=False,
        )


class WebSearchWorker(_TrabajoEnEdge):
    """Deja una búsqueda de Web Search abierta y a la vista.

    Es el más corto de los tres, pero va por aquí igual: levantar Edge tarda
    lo suyo, y hacerlo en el hilo de la ventana la dejaba congelada sin
    decir por qué.
    """

    def __init__(
        self,
        config: AirVaultConfig,
        url: str,
        etiqueta: str,
        parent=None,
    ) -> None:
        super().__init__(config, parent)
        self._url = url
        self._etiqueta = etiqueta

    def _trabajar(self):
        abrir_en_web_search(
            self._config, self._url, avisar=self.avance.emit
        )
        return self._etiqueta


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
        self._worker: Optional[_TrabajoEnEdge] = None
        self._cerrar_al_terminar = False
        self._resultados: list[ExcepcionLogPageAudit] = []
        # Si ahora mismo no hay ningún trabajo en Edge. Lo consulta el
        # cambio de selección, que llega por su cuenta y no puede encender
        # un botón en mitad de una consulta.
        self._libre = True

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
        grid.setHorizontalSpacing(SPACE_S)
        grid.setVerticalSpacing(self._densidad.group_spacing)

        hoy = QDate.currentDate()
        inicio = QDate(hoy.year(), hoy.month(), 1)
        grid.addWidget(QLabel("Desde:"), 0, 0)
        self.desde_edit = self._fecha(inicio)
        self.desde_edit.setToolTip("Primer día del reporte, incluido.")
        grid.addWidget(self.desde_edit, 0, 1)
        grid.addWidget(QLabel("Hasta:"), 0, 2)
        self.hasta_edit = self._fecha(hoy)
        self.hasta_edit.setToolTip("Último día del reporte, incluido.")
        grid.addWidget(self.hasta_edit, 0, 3)
        # Columna vacía entre el rango y el filtro: son dos preguntas
        # distintas y sin ese aire la fila se leía como seis controles
        # seguidos, con «Mostrar:» pegado al campo de la fecha final.
        grid.setColumnMinimumWidth(4, SPACE_L)
        grid.addWidget(QLabel("Mostrar:"), 0, 5)
        self.filtro_combo = QComboBox()
        self.filtro_combo.addItem(
            "Mal indexadas y duplicadas",
            (FILTRO_MAL_INDEXADAS, FILTRO_DUPLICADAS),
        )
        self.filtro_combo.addItem(
            "Solo mal indexadas", (FILTRO_MAL_INDEXADAS,)
        )
        self.filtro_combo.addItem("Solo duplicadas", (FILTRO_DUPLICADAS,))
        # Sin suelo de caracteres y ajustado al contenido: pide lo que mide
        # su frase más larga y ni un píxel más. Con el suelo de 26 pedía el
        # ancho de 26 mayúsculas, casi el doble de lo que ocupan sus tres
        # opciones, y ese exceso salía de los campos de fecha, que se
        # quedaban en su mínimo.
        configure_combo_box(self.filtro_combo, 0)
        self.filtro_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.filtro_combo.setToolTip(
            "Qué excepciones del reporte se traen a la tabla."
        )
        grid.addWidget(self.filtro_combo, 0, 6)
        # El sitio que sobra se queda al final de la fila. Con el estiramiento
        # en la columna del desplegable, este crecía hasta el borde de la
        # ventana: cuatrocientos veintisiete píxeles para tres frases que
        # miden la mitad.
        grid.setColumnStretch(7, 1)

        ayuda = QLabel(
            "M & E Integration. La consulta usa For Export = Yes para "
            "repetir los datos del libro y Refresh = Yes para regenerar el "
            "reporte. En la tabla, lo subrayado abre Web Search en Edge: la "
            "página, con sus apariciones; el rango, con el libro entero. "
            "Cada una se suma en su pestaña y se queda abierta."
        )
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet(f"color: {COLOR_AYUDA};")
        grid.addWidget(ayuda, 1, 0, 1, 8)
        cuerpo.addWidget(consulta)
        # Con el cuadro ya colgado de la ventana, que es cuando los campos
        # heredan la hoja de estilo y saben cuánto miden de verdad.
        for campo in (self.desde_edit, self.hasta_edit):
            _ensanchar_hasta_la_fecha_mas_larga(campo)

        self.tabla = QTableWidget(0, len(self.COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(list(self.COLUMNAS))
        self.tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        # Varias filas a la vez porque la selección es lo que elige qué
        # corregir. Con una sola, «Corregir seleccionadas…» habría sido un
        # botón para una bitácora, y lo que se pidió fue elegir un grupo.
        self.tabla.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tabla.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        self.tabla.setToolTip(
            "Resultados de Log Page Audit. Elija filas (con Ctrl o Mayús "
            "para varias) y use «Corregir seleccionadas…». Consultar no "
            "cambia nada en AirVault; corregir sí, y avisa antes de "
            "hacerlo. Las celdas subrayadas se abren en Web Search."
        )
        self.tabla.setAccessibleName("Excepciones de Log Page Audit")
        # Sin seguimiento del ratón no llega «cellEntered», y sin él el
        # cursor no puede cambiar de flecha a mano al pasar por las dos
        # columnas que abren algo.
        self.tabla.setMouseTracking(True)
        self.tabla.cellEntered.connect(self._al_pasar_por_la_celda)
        self.tabla.cellClicked.connect(self._al_pulsar_la_celda)
        style_data_table(self.tabla)
        align_vertical_scrollbar_to_header(self.tabla)
        size_columns_once(self.tabla, stretch_last=True)
        cuerpo.addWidget(self.tabla, 1)

        # El mismo orden que la ventana de AirVault: la barra sola en su
        # fila, debajo la frase de estado y al final los botones. Compartir
        # fila con los botones dejaba la barra corta y ponía las acciones a
        # media altura de la ventana, donde no las busca nadie.
        self.progreso = QProgressBar()
        self.progreso.setRange(0, 100)
        self.progreso.setValue(0)
        self.progreso.setTextVisible(False)
        cuerpo.addWidget(self.progreso)

        self.resumen = QLabel(
            "Listo para consultar desde el primer día del mes hasta hoy."
        )
        self.resumen.setWordWrap(True)
        self.resumen.setStyleSheet(f"color: {COLOR_AYUDA};")
        # Con el sitio reservado, como allá: los motivos de fallo de esta
        # consulta son igual de largos («Complete el acceso en Edge con la
        # cuenta de trabajo…») y sin el hueco la ventana pegaba un salto
        # cada vez que aparecía uno.
        self.resumen.setMinimumHeight(
            self._densidad.airvault_summary_min_height
        )
        self.resumen.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        cuerpo.addWidget(self.resumen)

        cuerpo.addLayout(self._fila_botones())
        # Después de los botones: lo que hace al cambiar la selección es
        # encenderlos o apagarlos, y hasta aquí no existen.
        self.tabla.itemSelectionChanged.connect(self._al_elegir_filas)

    def _fila_botones(self) -> QHBoxLayout:
        """Las acciones contra el margen derecho, como en AirVault.

        En azul va «Corregir todas…», que es la que cierra el trabajo de la
        ventana. Las otras dos que arrancan algo quedan en el gris de
        siempre: consultar es el paso previo, y corregir lo elegido es la
        misma acción sobre menos filas.
        """
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(SPACE_S)
        fila.addStretch()

        self.boton_consultar = QPushButton("Consultar")
        self.boton_consultar.setToolTip(WEB_REPORTS_TOOLTIP)
        self.boton_consultar.clicked.connect(self._consultar)

        self.boton_corregir = QPushButton("Corregir seleccionadas…")
        self.boton_corregir.setEnabled(False)
        self.boton_corregir.setToolTip(CORREGIR_SELECCION_TOOLTIP)
        self.boton_corregir.clicked.connect(self._corregir_seleccion)

        self.boton_corregir_todas = QPushButton("Corregir todas…")
        self.boton_corregir_todas.setObjectName("primaryButton")
        self.boton_corregir_todas.setEnabled(False)
        self.boton_corregir_todas.setToolTip(CORREGIR_TOOLTIP)
        self.boton_corregir_todas.clicked.connect(self._corregir_todas)

        self.boton_cancelar = QPushButton("Cancelar")
        self.boton_cancelar.setEnabled(False)
        self.boton_cancelar.setToolTip(
            "Detiene el trabajo en curso y cierra la pestaña de Edge. Lo ya "
            "hecho se conserva; nada queda a medias."
        )
        self.boton_cancelar.clicked.connect(self._cancelar)

        self.boton_cerrar = QPushButton("Cerrar")
        self.boton_cerrar.clicked.connect(self.close)

        for boton in (
            self.boton_consultar,
            self.boton_corregir,
            self.boton_corregir_todas,
            self.boton_cancelar,
            self.boton_cerrar,
        ):
            fila.addWidget(boton)
        return fila

    # ── corregir ────────────────────────────────────────────────────

    def plan(self) -> list[Correccion]:
        """Qué haría la corrección con lo que hay ahora en la tabla."""
        return planificar(self._resultados)

    def seleccionadas(self) -> list[ExcepcionLogPageAudit]:
        """Las excepciones de las filas elegidas, de arriba abajo.

        Salen de la propia celda y no de la posición de la fila: la tabla se
        ordena por cualquier columna, así que la fila tercera de la pantalla
        no tiene por qué ser la tercera que trajo la consulta.
        """
        filas = sorted(
            {indice.row() for indice in self.tabla.selectedIndexes()}
        )
        elegidas: list[ExcepcionLogPageAudit] = []
        for fila in filas:
            item = self.tabla.item(fila, 0)
            if item is None:
                continue
            excepcion = item.data(Qt.ItemDataRole.UserRole)
            if excepcion is not None:
                elegidas.append(excepcion)
        return elegidas

    def plan_seleccionado(self) -> list[Correccion]:
        """El plan de siempre, recortado a las filas elegidas.

        Se planifica con la tabla entera y después se recorta, y no al
        revés: el plan cruza unas excepciones con otras (una mal indexada
        que además está repetida se deja para después de quitar las
        copias). Planificando solo lo elegido, elegir la mal indexada sin su
        duplicada la habría dado por reindexable, que es justo lo que esa
        regla evita.
        """
        elegidas = {id(excepcion) for excepcion in self.seleccionadas()}
        return [
            correccion
            for correccion in self.plan()
            if id(correccion.excepcion) in elegidas
        ]

    @staticmethod
    def _aplicables(plan: list[Correccion]) -> list[Correccion]:
        return [
            correccion
            for correccion in plan
            if correccion.accion != ACCION_REVISAR
        ]

    def _corregir_todas(self) -> None:
        """Todo lo que el reporte deja decidido, sin elegir nada."""
        self._corregir(self.plan())

    def _corregir_seleccion(self) -> None:
        """Solo las filas elegidas en la tabla."""
        plan = self.plan_seleccionado()
        if not plan:
            self.resumen.setText(
                "No hay ninguna fila elegida. Elija las bitácoras que quiere "
                "corregir, o use «Corregir todas…»."
            )
            return
        self._corregir(plan)

    def _corregir(self, plan: list[Correccion]) -> None:
        """Aplica en AirVault lo que el reporte deja decidido."""
        if self.hilo() is not None:
            return
        if not self._aplicables(plan):
            self.resumen.setText(resumen_del_plan(plan))
            return
        if not self._autorizado(plan):
            return
        self._config = AirVaultConfig.load(self._raiz / AIRVAULT_FILENAME)
        worker = CorreccionWorker(self._config, plan, self)
        worker.avance.connect(self._al_avanzar)
        worker.resultado.connect(self._al_corregir)
        worker.fallo.connect(self._al_fallar)
        worker.cancelado.connect(self._al_cancelar)
        worker.finished.connect(self._al_terminar)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self._habilitar(False)
        self.progreso.setRange(0, 0)
        self.resumen.setText("Abriendo AirVault en Edge para corregir.")
        worker.start()

    def _autorizado(self, plan: list[Correccion]) -> bool:
        """Lo que va a pasar, por escrito, antes de tocar nada.

        Con la lista entera detrás del botón de detalles: el resumen dice
        cuánto, y quien autoriza tiene derecho a ver qué bitácoras son sin
        salir del cuadro.
        """
        dialogo = QMessageBox(self)
        dialogo.setIcon(QMessageBox.Icon.Question)
        dialogo.setWindowTitle("Corregir en AirVault")
        dialogo.setText(resumen_del_plan(plan))
        dialogo.setInformativeText(
            "Lo que se borre en AirVault no se deshace desde aquí. Cada caso "
            "se comprueba contra la pantalla antes de tocarlo: el que ya no "
            "coincida con el reporte se deja como está."
        )
        dialogo.setDetailedText(
            "\n".join(correccion.descripcion for correccion in plan)
        )
        aceptar = dialogo.addButton(
            "Corregir", QMessageBox.ButtonRole.AcceptRole
        )
        dialogo.addButton("No corregir", QMessageBox.ButtonRole.RejectRole)
        dialogo.exec()
        return dialogo.clickedButton() is aceptar

    def _al_corregir(self, resultados: object) -> None:
        """Cuenta lo que se hizo y lo que no, sin esconder lo segundo."""
        resultados = list(resultados)
        hechos = [resultado for resultado in resultados if resultado.hecho]
        intentados = [
            resultado
            for resultado in resultados
            if resultado.correccion.accion != ACCION_REVISAR
        ]
        quedaron = len(intentados) - len(hechos)
        texto = f"Corregidas {len(hechos)} de {len(intentados)} bitácoras."
        if quedaron:
            texto += (
                f" {quedaron} se dejaron como estaban; el detalle dice por "
                "qué."
            )
        texto += " Vuelva a consultar para ver cómo quedó el reporte."
        self.resumen.setText(texto)

        aviso = QMessageBox(self)
        aviso.setIcon(QMessageBox.Icon.Information)
        aviso.setWindowTitle("Corregir en AirVault")
        aviso.setText(texto)
        aviso.setDetailedText(
            "\n".join(
                f"{resultado.log_number}: {resultado.detalle}"
                for resultado in resultados
                if resultado.detalle
            )
        )
        aviso.exec()

    @staticmethod
    def _fecha(valor: QDate) -> QDateEdit:
        control = QDateEdit(valor)
        control.setCalendarPopup(True)
        control.setDisplayFormat(FORMATO_FECHA)
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
            # Consultar sigue sin tocar nada, y lo dice. Lo que cambia es
            # que ahora hay adónde ir después: se cuenta cuántos de esos
            # casos quedan decididos, que es lo que «Corregir…» aplicaría.
            texto = (
                f"Se encontraron {mal_indexadas} mal indexadas y "
                f"{duplicadas} duplicadas. Consultar no modificó AirVault."
            )
            aplicables = len(self._aplicables(self.plan()))
            if aplicables:
                texto += (
                    f" El reporte deja {aplicables} resueltas: «Corregir "
                    "todas…» enseña el plan antes de aplicarlo, y eligiendo "
                    "filas se corrigen solo esas."
                )
            else:
                texto += " Ninguna se puede corregir sola."
            self.resumen.setText(texto)

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
            enlaces = {
                COLUMNA_BITACORA: excepcion.url_busqueda,
                COLUMNA_RANGO_LIBRO: excepcion.url_busqueda_libro,
            }
            for columna, valor in enumerate(valores):
                item = QTableWidgetItem(str(valor))
                item.setToolTip(str(valor))
                if columna == 0:
                    item.setData(Qt.ItemDataRole.UserRole, excepcion)
                if enlaces.get(columna):
                    self._marcar_enlace(item, columna, enlaces[columna])
                self.tabla.setItem(fila, columna, item)
        self.tabla.setSortingEnabled(True)
        size_columns_once(self.tabla, stretch_last=True)

    def _marcar_enlace(
        self, item: QTableWidgetItem, columna: int, url: str
    ) -> None:
        """Deja la celda con pinta de enlace y con su dirección dentro.

        El subrayado es el indicador que se ve siempre, incluso en la fila
        seleccionada: ahí Qt pinta el texto con el color de la selección y
        el azul se pierde, pero la raya de debajo se queda. El color y el
        cursor lo acompañan mientras la fila no esté elegida.
        """
        item.setData(ROL_ENLACE, url)
        item.setForeground(QColor(link_text_color()))
        fuente = self.tabla.font()
        fuente.setUnderline(True)
        item.setFont(fuente)
        item.setToolTip(
            f"Clic para abrir {self._lo_que_abre(columna, item.text())} en "
            "Web Search."
        )

    @staticmethod
    def _lo_que_abre(columna: int, valor: str) -> str:
        """Cómo se nombra lo que hay al otro lado de cada enlace."""
        if columna == COLUMNA_BITACORA:
            return f"la bitácora {valor}"
        return f"el libro {valor}"

    def _al_pasar_por_la_celda(self, fila: int, columna: int) -> None:
        """Mano sobre lo que abre algo, flecha sobre lo demás."""
        item = self.tabla.item(fila, columna)
        enlace = item.data(ROL_ENLACE) if item is not None else None
        self.tabla.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor
            if enlace
            else Qt.CursorShape.ArrowCursor
        )

    def _al_pulsar_la_celda(self, fila: int, columna: int) -> None:
        """Abre en Web Search lo que la celda pulsada tenga guardado."""
        item = self.tabla.item(fila, columna)
        url = str(item.data(ROL_ENLACE) or "") if item is not None else ""
        if not url:
            return
        self._abrir_en_web_search(
            url, self._lo_que_abre(columna, item.text())
        )

    def _abrir_en_web_search(self, url: str, etiqueta: str) -> None:
        """Lleva el Edge del programa a esa búsqueda, sin colgar la ventana.

        Con el mismo candado que el resto: Edge admite un navegador por
        perfil, así que abrir esto mientras se consulta o se corrige le
        quitaría la pestaña al trabajo que ya estaba corriendo. Las
        búsquedas de una en una, entonces; abierta la primera, cada una
        siguiente se suma a la misma ventana y ninguna cierra a la anterior.
        """
        if self.hilo() is not None:
            self.resumen.setText(
                "Hay un trabajo en Edge sin terminar. Espere a que acabe y "
                "vuelva a pulsar."
            )
            return
        self._config = AirVaultConfig.load(self._raiz / AIRVAULT_FILENAME)
        worker = WebSearchWorker(self._config, url, etiqueta, self)
        worker.avance.connect(self._al_avanzar)
        worker.resultado.connect(self._al_abrir)
        worker.fallo.connect(self._al_fallar_al_abrir)
        worker.cancelado.connect(self._al_cancelar_la_apertura)
        worker.finished.connect(self._al_terminar)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self._habilitar(False)
        self.progreso.setRange(0, 0)
        self.resumen.setText(f"Abriendo {etiqueta} en Web Search.")
        worker.start()

    def _al_abrir(self, etiqueta: object) -> None:
        self.resumen.setText(
            f"Web Search abierto en {etiqueta}. Cada búsqueda se suma en su "
            "pestaña y ninguna se cierra sola: ciérrelas al terminar de "
            "mirarlas."
        )

    def _al_fallar_al_abrir(self, mensaje: str) -> None:
        self.resumen.setText(f"No se pudo abrir Web Search: {mensaje}")

    def _al_cancelar_la_apertura(self) -> None:
        """Abrir no se puede detener a medias, y no se finge que sí.

        Cuando llega la cancelación, Edge ya está arriba con la búsqueda
        dentro: lo único que queda por decir es dónde quedó la ventana.
        """
        self.resumen.setText(
            "Web Search se abrió antes de que llegara la cancelación: la "
            "ventana de Edge está abierta. No se modificó AirVault."
        )

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
        self._libre = habilitado
        for control in (
            self.desde_edit,
            self.hasta_edit,
            self.filtro_combo,
            self.boton_consultar,
        ):
            control.setEnabled(habilitado)
        # Corregir solo se ofrece cuando hay algo que el reporte deje
        # decidido. Con la tabla vacía, o con todo pendiente de revisar a
        # mano, el botón no tendría nada que hacer; y el de la selección
        # tampoco mientras no haya filas elegidas que corregir.
        self.boton_corregir_todas.setEnabled(
            habilitado and bool(self._aplicables(self.plan()))
        )
        self.boton_corregir.setEnabled(
            habilitado and bool(self._aplicables(self.plan_seleccionado()))
        )
        self.boton_cancelar.setEnabled(not habilitado)

    def _al_elegir_filas(self) -> None:
        """Elegir filas enciende o apaga el botón que actúa sobre ellas."""
        self._habilitar(self._libre)

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
