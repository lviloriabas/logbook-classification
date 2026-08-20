"""Ventana «Indexar en AirVault».

Es la cara de :mod:`app.airvault`: no decide nada por su cuenta, solo pide
los datos que hacen falta, lanza el recorrido en un hilo aparte y cuenta
como fue. Todo lo que decide si una página se escribe o no vive en el
módulo, que se prueba sin interfaz.

Va en ventana aparte y no colgando de la principal. Empotrado, el indexado
le quitaba alto a la vista previa y descuadraba el reparto: al desplegarse
cambiaba el mínimo de la ventana, y en pantallas bajas eso la sacaba del
escritorio. Aparte tiene el sitio que necesita —el historial entero de
ejecuciones, su propio avance— y la ventana principal vuelve a medirse
sola.

El trabajo va en tres tiempos, separados porque duran cosas muy distintas:

1. **Subir a AirVault** manda los PDF. Termina cuando termina la subida.
2. **Comprobar** pregunta si AirVault ya procesó lo subido. Eso puede
   tardar minutos u horas, así que no se espera delante: se pregunta cada
   tantos minutos, o cuando alguien pulse. Según van quedando listos, los
   lotes aparecen en la lista con lo que se les escribiría.
3. **Indexar** escribe los que ya están listos, y con «Completar batch»
   marcado los da además por terminados en AirVault.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.csv_utils import find_csv_files, find_run_dirs
from app.gui.responsive import fit_to_screen
from app.gui.widgets import APP_CHROME_QSS, DATA_TABLE_QSS, ElidedLabel

# Gris con el que la ventana principal escribe las líneas de ayuda.
COLOR_AYUDA = "#57606a"

# Ejecuciones que lista el historial, las mismas que el visor de CSV: es la
# ventana de trabajo de un turno. Lo de más atrás sigue en output/ y se
# alcanza con «Otra ejecución…».
LIMITE_HISTORIAL = 25

# Cada cuántos minutos se le pregunta a AirVault sin que nadie pulse nada.
# Cinco es holgado a propósito: un lote tarda lo suyo en cruzar la cola del
# servidor y preguntar cada rato no lo apura.
MINUTOS_POR_DEFECTO = 5

# Líneas que conserva la bitácora. Con la comprobación automática corriendo
# toda una tarde, sin tope crecería sin fin.
LIMITE_BITACORA = 300

TEXTO_SIN_SUBIR = (
    "Sin subir. «Subir a AirVault» manda los PDF de la entrega; nada se "
    "indexa hasta que los lotes estén listos y se apruebe."
)

AIRVAULT_TOOLTIP = (
    "Escribe en AirVault los datos que la ejecución ya leyó, sin teclear "
    "página por página en el Web Index."
)


def csv_de_corrida(carpeta: Path | str) -> Optional[Path]:
    """CSV mínimo de una ejecución, que es el que va a AirVault.

    El indexado necesita el CSV corto —el de las columnas del Web Index—,
    no el ``_completo``, que trae además el detalle de la lectura.
    """
    carpeta = Path(carpeta)
    preferido = carpeta / "datos" / f"{carpeta.name}.CSV"
    if preferido.is_file():
        return preferido
    candidatos = [
        ruta for ruta in find_csv_files(carpeta)
        if not ruta.stem.casefold().endswith("_completo")
    ]
    return candidatos[0] if candidatos else None


def paginas_de_corrida(carpeta: Path | str) -> Optional[int]:
    """Páginas que dejó la ejecución, según sus estadísticas."""
    try:
        datos = json.loads(
            (Path(carpeta) / "stats.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    total = datos.get("total_paginas") if isinstance(datos, dict) else None
    return int(total) if isinstance(total, (int, float)) else None


def estado_de_entrega(csv: Path | str) -> tuple[str, bool]:
    """Qué tiene la ejecución para subir, y si con eso alcanza.

    Se mira aquí para que el historial diga de un vistazo cuáles se pueden
    subir. El motivo exacto lo vuelve a comprobar ``comprobar_entrega`` al
    arrancar, que es quien manda: esto solo evita empezar un trabajo que ya
    se sabe que no va a salir.
    """
    from app.airvault.flujo import pdfs_de_corrida, ruta_indice_paginas

    pdfs = pdfs_de_corrida(csv)
    if not pdfs:
        return "Sin exportar", False
    if not ruta_indice_paginas(csv).is_file():
        # Exportada antes de que existiera el índice de páginas: hay PDF,
        # pero nada que diga qué página del lote es cuál.
        return "Falta reexportar", False
    return ("1 archivo" if len(pdfs) == 1 else f"{len(pdfs)} archivos"), True


class TrabajoCancelado(BaseException):
    """Alguien pulsó Cancelar; el trabajo se deshace y se sale.

    Hereda de ``BaseException`` a propósito: el recorrido del indexado
    atrapa ``Exception`` en varios sitios para anotar la página que falló y
    seguir, y una cancelación no puede quedarse ahí anotada como si fuera
    el error de una página. Así atraviesa todo hasta el hilo, pasando por
    los ``finally`` que sueltan los lotes en AirVault.
    """


class TrabajoAirVaultWorker(QThread):
    """Corre las etapas del indexado fuera del hilo de la interfaz.

    Una ejecución completa sube casi dos gigas y escribe cientos de páginas
    por red; hecho en el hilo de la ventana, Windows la daría por colgada.

    Todo lo que tarda pasa por ``_avisar``, que además es por donde entra la
    cancelación: así se puede parar dentro de una subida de mil trozos o de
    una espera de quince minutos sin que el recorrido sepa que existe un
    botón de cancelar.
    """

    paso = Signal(str, int, int)
    subido = Signal(object)
    comprobado = Signal(object)
    indexado = Signal(object)
    fallo = Signal(str)
    cancelado = Signal()

    def __init__(self, modo: str, panel_estado: dict, parent=None) -> None:
        super().__init__(parent)
        self.modo = modo
        self.estado = panel_estado
        # Bandera propia, ademas de la de Qt. ``requestInterruption`` no
        # hace nada sobre un hilo que todavia no arranco, y el cierre de la
        # ventana puede pedir la cancelacion en ese hueco.
        self._parar = False

    def cancelar(self) -> None:
        """Pide que pare, arrancado o no."""
        self._parar = True
        self.requestInterruption()

    def hay_que_parar(self) -> bool:
        return self._parar or self.isInterruptionRequested()

    # ── ejecución ──────────────────────────────────────────────────

    def run(self) -> None:  # noqa: D102 - lo describe la clase
        etapas = {
            "subir": self._subir,
            "comprobar": self._comprobar,
            "indexar": self._indexar,
        }
        try:
            etapas[self.modo]()
        except TrabajoCancelado:
            self.cancelado.emit()
        except Exception as exc:  # noqa: BLE001 - llega a la interfaz
            self.fallo.emit(str(exc))

    def _avisar(self, texto: str, hechas: int, total: int) -> None:
        """Cuenta en qué va y, de paso, mira si hay que parar."""
        if self.hay_que_parar():
            raise TrabajoCancelado()
        self.paso.emit(texto, int(hechas), int(total))

    def _dormir(self, segundos: float) -> None:
        """Espera troceada, para que cancelar no tarde lo que tarde la espera.

        AirVault puede tardar minutos en sacar el lote de su cola. Dormir
        eso de una vez dejaba el botón de cancelar sin efecto hasta el
        siguiente sondeo.
        """
        restante = float(segundos)
        while restante > 0:
            if self.hay_que_parar():
                raise TrabajoCancelado()
            self.msleep(int(min(0.5, restante) * 1000))
            restante -= 0.5
        if self.hay_que_parar():
            raise TrabajoCancelado()

    # ── la conexión ────────────────────────────────────────────────

    def _conectar(self):
        """Devuelve el cliente de AirVault, abriendo sesión si hace falta.

        La comprobación periódica reusa el mismo: la sesión se renueva sola
        cuando caduca, así que volver a abrirla cada cinco minutos serían
        dos viajes al navegador para nada.
        """
        from app.airvault.client import ClienteHttp
        from app.airvault.session import abrir_sesion, comprobar_o_renovar

        cliente = self.estado.get("cliente")
        if cliente is not None:
            return cliente

        def avisar_texto(texto: str) -> None:
            self._avisar(texto, 0, 0)

        self._avisar("Entrando a AirVault", 0, 0)
        sesion = abrir_sesion(
            self.estado["config"], cookie=self.estado.get("cookie") or None,
            avisar=avisar_texto,
        )
        # Comprobar antes de subir nada: si la sesión guardada ya no vale,
        # esto vuelve a abrir el navegador en lugar de morir en la primera
        # página con un mensaje que manda a copiar cookies a mano.
        self._avisar("Comprobando la sesión de AirVault", 0, 0)
        comprobar_o_renovar(sesion, avisar=avisar_texto)
        self._avisar(f"Sesión tomada del {sesion.origen}", 0, 0)
        cliente = ClienteHttp(sesion, self.estado["config"])
        self.estado["cliente"] = cliente
        self.estado["sesion"] = sesion
        return cliente

    # ── subir ──────────────────────────────────────────────────────

    def _subir(self) -> None:
        from app.airvault.flujo import (
            comprobar_entrega,
            preparar_partes,
            subir_partes,
        )
        from app.airvault.mapping import FLOTA_CACHE_FILENAME, ResolutorFlota

        estado = self.estado
        csv = Path(estado["csv"])
        raiz = Path(estado["raiz"])

        self._avisar(f"Leyendo la ejecución {csv.parent.parent.name}", 0, 0)
        entrega = comprobar_entrega(csv)
        cuantas = sum(len(p.paginas) for p in entrega)
        archivos = ("1 archivo" if len(entrega) == 1
                    else f"{len(entrega)} archivos")
        self._avisar(f"{cuantas} páginas en {archivos} de entrega", 0, 0)
        resolutor = ResolutorFlota.load(raiz / FLOTA_CACHE_FILENAME)
        trabajos = preparar_partes(
            estado["config"], Path(estado["carpeta_job"]), csv,
            estado["nombre_lote"], resolutor=resolutor,
        )
        estado["trabajos"] = trabajos
        for trabajo in trabajos:
            manifiesto = trabajo.manifiesto
            self._avisar(
                f"Lote «{manifiesto.nombre_batch}»: "
                f"{len(manifiesto.bitacoras())} bitácoras y "
                f"{len(manifiesto.separadores())} separadores", 0, 0,
            )

        cliente = self._conectar()
        subir_partes(
            trabajos, estado["sesion"], avisar=self._avisar, cliente=cliente,
            dormir=self._dormir,
        )
        self.subido.emit({
            "trabajos": trabajos, "cliente": cliente,
            "sesion": estado.get("sesion"),
        })

    # ── comprobar ──────────────────────────────────────────────────

    def _comprobar(self) -> None:
        """Pregunta a AirVault y planifica lo que ya esté listo.

        Planificar es solo leer: abre el lote, lee sus páginas, calcula qué
        se escribiría y lo suelta. Se hace aquí, en cuanto una parte queda
        lista, para que la lista pueda decir «14 se escribirían, 5
        bloqueadas» en vez de un «listo» a secas.
        """
        from app.airvault.flujo import comprobar_partes
        from app.airvault.mapping import FLOTA_CACHE_FILENAME, ResolutorFlota
        from app.airvault.report import (
            escribir_csv_de_partes,
            escribir_html_de_partes,
        )

        estado = self.estado
        raiz = Path(estado["raiz"])
        carpeta = Path(estado["carpeta_job"])
        trabajos = estado["trabajos"]
        planes: Dict[str, tuple] = estado.setdefault("planes", {})

        cliente = self._conectar()
        estados = comprobar_partes(trabajos, cliente, avisar=self._avisar)

        resolutor = ResolutorFlota.load(raiz / FLOTA_CACHE_FILENAME)
        nuevos = 0
        for parte in estados:
            clave = str(parte.trabajo.carpeta)
            if clave in planes or not parte.se_puede_indexar:
                continue
            self._avisar(
                f"Leyendo el lote {parte.batch_id} para ver qué se "
                f"escribiría", 0, 0,
            )
            planes[clave] = parte.trabajo.planificar(
                cliente, resolutor, avisar=self._avisar
            )
            nuevos += 1
        if nuevos:
            resolutor.guardar(raiz / FLOTA_CACHE_FILENAME)

        # Un solo reporte para toda la ejecución: se aprueba de una vez, no
        # lote por lote.
        partes = [
            (t.manifiesto.nombre_batch, planes[str(t.carpeta)][0])
            for t in trabajos if str(t.carpeta) in planes
        ]
        reporte = estado.get("reporte")
        if nuevos and partes:
            self._avisar("Escribiendo el reporte de revisión", 0, 0)
            escribir_csv_de_partes(partes, carpeta / "revision.csv")
            reporte = escribir_html_de_partes(
                partes, carpeta / "revision.html",
                f"Indexado de {carpeta.name}",
            )
            estado["reporte"] = reporte
        self.comprobado.emit({
            "estados": estados, "planes": planes, "partes": partes,
            "cliente": cliente, "reporte": reporte, "nuevos": nuevos,
        })

    # ── indexar ────────────────────────────────────────────────────

    def _indexar(self) -> None:
        from app.airvault.flujo import (
            cerrar_partes,
            completar_partes,
            indexar_partes,
            verificar_partes,
        )

        estado = self.estado
        cliente = estado["cliente"]
        trabajos = list(estado["listos"])
        planes = [estado["planes"][str(t.carpeta)] for t in trabajos]
        cierres: list = []
        try:
            resultado = indexar_partes(trabajos, planes, avisar=self._avisar)
            self._avisar("Comprobando cómo quedaron los lotes", 0, 0)
            validas, total, _problemas = verificar_partes(trabajos, cliente)
            if estado.get("completar"):
                cierres = completar_partes(
                    trabajos, cliente, avisar=self._avisar
                )
        finally:
            # Escribir toma el lote y lo suelta al terminar; esto es la red
            # de seguridad para cuando algo se corta por el medio. Un lote
            # que queda tomado no da error: cuelga la próxima vez que
            # alguien lo abra.
            cerrar_partes(trabajos, cliente)
        self.indexado.emit({
            "resultado": resultado, "validas": validas, "total": total,
            "lotes": len(trabajos), "cierres": cierres,
        })


def _soltar(trabajos, cliente) -> None:
    """Suelta los lotes y se calla si no puede: es limpieza, no trabajo."""
    from app.airvault.flujo import cerrar_partes

    try:
        cerrar_partes(trabajos, cliente)
    except Exception:  # noqa: BLE001 - soltando no se avisa de nada
        pass


class SoltarLotesWorker(QThread):
    """Suelta los lotes fuera del hilo de la ventana.

    Es una petición por lote contra un servidor que puede tardar un minuto
    en contestar. En el hilo de la ventana, cambiar de ejecución o cancelar
    la dejaba congelada todo ese rato.
    """

    def __init__(self, trabajos, cliente, parent=None) -> None:
        super().__init__(parent)
        self._trabajos = trabajos
        self._cliente = cliente

    def run(self) -> None:  # noqa: D102 - lo describe la clase
        _soltar(self._trabajos, self._cliente)


class AirVaultWindow(QDialog):
    """Ventana aparte que sube al Web Index una ejecución del historial."""

    def __init__(self, raiz: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raiz = Path(raiz)
        self._worker: Optional[TrabajoAirVaultWorker] = None
        # Todo lo que el hilo necesita y devuelve: la conexión abierta, los
        # trabajos de cada parte, los planes ya calculados y el reporte.
        # Vive aquí para que la comprobación periódica reuse la sesión en
        # vez de volver al navegador cada cinco minutos.
        self._estado: dict = {}
        self._trabajos: list = []
        self._estados: list = []
        self._config = None
        self._listo_para_subir = False
        self._listas: list[bool] = []
        # Reloj del paso en curso y último texto anotado, para no repetir
        # una línea por cada trozo de una subida.
        self._reloj: Optional[QTimer] = None
        self._inicio_paso = time.monotonic()
        self._ultimo_paso = ""
        # El que pregunta solo por los lotes cada tantos minutos.
        self._vigilante: Optional[QTimer] = None
        # Encadena una comprobacion en cuanto termine lo que esta en vuelo:
        # subir e indexar dejan la lista desactualizada.
        self._comprobar_al_terminar = False
        # Cerrar con trabajo en vuelo no bloquea: se pide la cancelación y
        # la ventana se va en cuanto el hilo suelta lo que tenía tomado.
        self._cerrar_al_terminar = False
        # Hilos que están soltando lotes en AirVault, para que Qt no los
        # destruya a media petición.
        self._soltando: list[QThread] = []

        self.setWindowTitle("Indexar en AirVault")
        # Con botón de minimizar: escribir una ejecución entera tarda, y
        # mientras tanto se sigue trabajando en la ventana principal.
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        # Como el resto de las ventanas: el tamaño lo pone la pantalla, que
        # en un portátil bajo dejaría los botones fuera del borde.
        densidad = fit_to_screen(self, 780, 720)
        self.setStyleSheet(APP_CHROME_QSS + DATA_TABLE_QSS + densidad.qss)
        self._build_ui()

    # ── construcción ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        cuerpo = QVBoxLayout(self)

        intro = QLabel(
            "Elija la ejecución que se va a subir. Se escriben en el Web "
            "Index los datos que el OCR ya leyó, en lugar de teclearlos "
            "página por página. La ejecución tiene que estar exportada."
        )
        intro.setWordWrap(True)
        cuerpo.addWidget(intro)

        cuerpo.addWidget(self._historial(), 1)
        cuerpo.addLayout(self._campos())
        cuerpo.addWidget(self._titulo("Lotes en AirVault"))
        cuerpo.addWidget(self._lotes())
        cuerpo.addLayout(self._fila_vigilancia())
        cuerpo.addLayout(self._fila_avance())
        cuerpo.addWidget(self._bitacora())

        self.resumen = QLabel(TEXTO_SIN_SUBIR)
        self.resumen.setWordWrap(True)
        self.resumen.setStyleSheet(f"color: {COLOR_AYUDA};")
        # Sitio para tres líneas: los motivos de fallo son largos, y sin
        # reservarlo la ventana daba un salto cada vez que aparecía uno.
        self.resumen.setMinimumHeight(48)
        self.resumen.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        cuerpo.addWidget(self.resumen)

        cuerpo.addLayout(self._fila_botones())

    @staticmethod
    def _titulo(texto: str) -> QLabel:
        etiqueta = QLabel(texto)
        etiqueta.setStyleSheet("font-weight: 600;")
        return etiqueta

    def _historial(self) -> QTableWidget:
        tabla = QTableWidget(0, 3)
        tabla.setHorizontalHeaderLabels(["Ejecución", "Páginas", "Entrega"])
        tabla.setToolTip(
            "Ejecuciones procesadas, de la más reciente a la más antigua. Solo "
            "se suben las exportadas; las anteriores a las últimas "
            f"{LIMITE_HISTORIAL} se alcanzan con «Otra ejecución…»"
        )
        tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        tabla.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabla.setAlternatingRowColors(True)
        tabla.verticalHeader().setVisible(False)
        cabecera = tabla.horizontalHeader()
        cabecera.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        cabecera.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        cabecera.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        tabla.itemSelectionChanged.connect(self._al_elegir_del_historial)
        self.historial = tabla
        return tabla

    def _campos(self) -> QGridLayout:
        """Las tres líneas de datos, en rejilla para que se alineen.

        En filas sueltas cada etiqueta medía lo suyo y los tres cuadros
        empezaban en sitios distintos.
        """
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for fila, etiqueta in enumerate(("Ejecución:", "Lote:", "Sesión:")):
            grid.addWidget(QLabel(etiqueta), fila, 0)

        self.corrida_edit = QLineEdit()
        self.corrida_edit.setReadOnly(True)
        self.corrida_edit.setPlaceholderText(
            "CSV de la ejecución que se va a indexar"
        )
        self.corrida_edit.setToolTip(
            "CSV de la ejecución cuyos datos se escriben en AirVault. Lo "
            "pone la ejecución elegida arriba."
        )
        grid.addWidget(self.corrida_edit, 0, 1)

        self.boton_buscar = QPushButton("Otra ejecución…")
        self.boton_buscar.setToolTip(
            "Elegir el CSV de una ejecución que no está en la lista"
        )
        self.boton_buscar.clicked.connect(self._elegir_corrida)
        grid.addWidget(self.boton_buscar, 0, 2)

        self.lote_edit = QLineEdit()
        self.lote_edit.setPlaceholderText("Nombre del lote en AirVault")
        self.lote_edit.setToolTip(
            "Nombre con el que el lote queda en AirVault. Lleva la fecha y "
            "la hora de la ejecución para que no se confunda con otro: en la "
            "cola conviven lotes con nombres repetidos."
        )
        grid.addWidget(self.lote_edit, 1, 1, 1, 2)

        # El campo de la sesión queda por si el navegador no puede: el
        # camino normal es que se resuelva sola.
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie_edit.setPlaceholderText(
            "Se resuelve sola; solo si el navegador falla"
        )
        self.cookie_edit.setToolTip(
            "Normalmente no hay que escribir nada aquí. El programa abre "
            "Edge con un perfil propio y toma de ahí la sesión: la primera "
            "vez se entra a AirVault en esa ventana, y a partir de entonces "
            "se abre sola y sin ventana. Este campo es el respaldo por si "
            "eso falla: se pega la cookie de AirVault copiada del navegador. "
            "No se guarda en el disco."
        )
        grid.addWidget(self.cookie_edit, 2, 1, 1, 2)
        return grid

    def _lotes(self) -> QTableWidget:
        """En qué va cada lote de esta ejecución dentro de AirVault.

        Una entrega puede ser varios lotes —las partes, y el de REVISAR—, y
        no llegan a estar listos a la vez: AirVault los procesa en su cola.
        Aquí se ve cuál ya se puede indexar y cuál sigue esperando, en vez
        de una sola línea de estado que solo puede decir una cosa.
        """
        tabla = QTableWidget(0, 3)
        tabla.setHorizontalHeaderLabels(["Lote", "Páginas", "Estado"])
        tabla.setToolTip(
            "Lotes de esta ejecución en AirVault. Van pasando a «Listo para "
            "indexar» según el servidor termina de procesarlos."
        )
        tabla.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabla.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabla.setAlternatingRowColors(True)
        tabla.verticalHeader().setVisible(False)
        tabla.setMaximumHeight(132)
        cabecera = tabla.horizontalHeader()
        cabecera.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        cabecera.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        cabecera.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.lotes = tabla
        return tabla

    def _fila_vigilancia(self) -> QHBoxLayout:
        """Cada cuánto se pregunta solo, y el botón para preguntar ya."""
        fila = QHBoxLayout()
        self.auto_check = QCheckBox("Comprobar cada")
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip(
            "Le pregunta a AirVault cada tantos minutos si ya terminó de "
            "procesar lo subido, y deja de preguntar cuando no queda nada "
            "por esperar. Se puede apagar y usar solo «Comprobar ahora»."
        )
        self.auto_check.toggled.connect(self._ajustar_vigilancia)
        fila.addWidget(self.auto_check)

        self.minutos_spin = QSpinBox()
        self.minutos_spin.setRange(1, 60)
        self.minutos_spin.setValue(MINUTOS_POR_DEFECTO)
        self.minutos_spin.setSuffix(" min")
        self.minutos_spin.setToolTip(
            "Un lote tarda minutos, a veces mucho más, en salir de la cola "
            "de AirVault. Preguntar más seguido no lo apura."
        )
        self.minutos_spin.valueChanged.connect(self._ajustar_vigilancia)
        fila.addWidget(self.minutos_spin)

        self.boton_comprobar = QPushButton("Comprobar ahora")
        self.boton_comprobar.setEnabled(False)
        self.boton_comprobar.setToolTip(
            "Pregunta a AirVault en qué van los lotes de esta ejecución, y "
            "calcula qué se escribiría en los que ya estén listos. No "
            "escribe nada."
        )
        self.boton_comprobar.clicked.connect(self._comprobar)
        fila.addWidget(self.boton_comprobar)
        fila.addStretch()
        return fila

    def _fila_avance(self) -> QHBoxLayout:
        """Estado, reloj y barra propios: la ventana no cuelga de la principal."""
        fila = QHBoxLayout()
        self.estado_label = ElidedLabel("Listo.")
        fila.addWidget(self.estado_label, 1)
        # Cuánto lleva el paso actual. Sin esto, una espera de AirVault y un
        # programa colgado se ven exactamente igual.
        self.reloj_label = QLabel("")
        self.reloj_label.setStyleSheet(f"color: {COLOR_AYUDA};")
        self.reloj_label.setMinimumWidth(56)
        self.reloj_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        fila.addWidget(self.reloj_label)
        self.progreso = QProgressBar()
        self.progreso.setRange(0, 100)
        self.progreso.setValue(0)
        fila.addWidget(self.progreso, 1)
        return fila

    def _bitacora(self) -> QListWidget:
        """Lo que va haciendo, paso a paso y con la hora.

        Un lote tarda lo suyo y pasa por etapas muy distintas —subir,
        esperar a que AirVault lo procese, leer el lote, escribir—. Con una
        sola línea de estado no había forma de saber en cuál estaba ni
        cuánto llevaba, y una espera larga no se distinguía de un cuelgue.
        """
        lista = QListWidget()
        lista.setToolTip("Lo que el indexado va haciendo, con la hora de cada paso")
        lista.setMaximumHeight(110)
        lista.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        lista.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bitacora = lista
        return lista

    def _fila_botones(self) -> QHBoxLayout:
        fila = QHBoxLayout()

        self.completar_check = QCheckBox("Completar batch")
        self.completar_check.setToolTip(
            "Al terminar de escribir, da el lote por terminado en AirVault y "
            "lo saca de la cola del Web Index. AirVault solo lo acepta con "
            "todas las páginas en verde: si a alguna le falta un campo "
            "obligatorio, el lote se queda en la cola y se dice cuáles son. "
            "Sin marcar, el lote queda ahí para revisarlo."
        )
        fila.addWidget(self.completar_check)
        fila.addStretch()

        self.boton_subir = QPushButton("Subir a AirVault")
        self.boton_subir.setObjectName("primaryButton")
        self.boton_subir.setEnabled(False)
        self.boton_subir.setToolTip(
            "Manda a AirVault los PDF de la entrega. Termina cuando termina "
            "la subida; que el servidor los procese tarda más y se comprueba "
            "después."
        )
        self.boton_subir.clicked.connect(self._subir)

        self.boton_indexar = QPushButton("Indexar")
        self.boton_indexar.setEnabled(False)
        self.boton_indexar.setToolTip(
            "Escribe en AirVault los datos de los lotes que ya están listos. "
            "Las páginas marcadas como bloqueadas no se tocan."
        )
        self.boton_indexar.clicked.connect(self._indexar)

        self.boton_reporte = QPushButton("Ver reporte…")
        self.boton_reporte.setEnabled(False)
        self.boton_reporte.setToolTip(
            "Abre el detalle página por página de lo que se escribiría"
        )
        self.boton_reporte.clicked.connect(self._abrir_reporte)

        # Siempre disponible mientras hay trabajo en vuelo. Es lo que
        # convierte una espera larga en algo de lo que se puede salir: sin
        # él, una sesión que no llega o un lote que AirVault no suelta
        # dejaban la ventana sin nada que pulsar durante minutos.
        self.boton_cancelar = QPushButton("Cancelar")
        self.boton_cancelar.setEnabled(False)
        self.boton_cancelar.setToolTip(
            "Detiene lo que esté en marcha y suelta los lotes que se hayan "
            "tomado en AirVault. Lo ya escrito se conserva y al volver a "
            "comprobar se retoma sin repetirlo."
        )
        self.boton_cancelar.clicked.connect(self._cancelar)

        self.boton_cerrar = QPushButton("Cerrar")
        self.boton_cerrar.clicked.connect(self.close)

        for boton in (
            self.boton_reporte, self.boton_subir, self.boton_indexar,
            self.boton_cancelar, self.boton_cerrar,
        ):
            fila.addWidget(boton)
        return fila

    # ── el historial ───────────────────────────────────────────────

    def showEvent(self, event) -> None:
        """Rehace el historial cada vez que la ventana se muestra.

        Vive escondida mientras se procesa, y una ejecución recién
        exportada tiene que estar en la lista sin cerrar nada.
        """
        super().showEvent(event)
        self._refrescar_historial()

    def _refrescar_historial(self) -> None:
        corridas = [
            (carpeta, csv_de_corrida(carpeta))
            for carpeta in find_run_dirs(
                self._raiz / "output", LIMITE_HISTORIAL
            )
        ]
        tabla = self.historial
        # Rellenar mueve la selección; las señales se cortan para que eso no
        # se lea como que alguien eligió otra ejecución y tire lo hecho.
        tabla.blockSignals(True)
        try:
            tabla.setRowCount(0)
            # Qué fila se puede subir, en el orden en que quedan: lo mira
            # tanto la ejecución que se propone al abrir como el aviso de lo
            # que le falta a la elegida.
            self._listas: list[bool] = []
            for carpeta, csv in corridas:
                if csv is not None:
                    self._agregar_fila(carpeta, csv)
        finally:
            tabla.blockSignals(False)
        if not tabla.rowCount():
            self.resumen.setText(
                "No hay ejecuciones procesadas todavía. Procese y exporte una, "
                "o elíjala con «Otra ejecución…»."
            )
            return
        if not self.corrida_edit.text().strip():
            self.fijar_corrida(self._primera_que_se_puede_subir())
        else:
            self._marcar_en_historial(self.corrida_edit.text())

    def _primera_que_se_puede_subir(self) -> str:
        """CSV con el que abrir: la ejecución exportada más reciente.

        Sin nada elegido se propone una, y proponer la más reciente a secas
        deja la ventana señalando una ejecución que todavía no se puede
        subir cuando lo último que se hizo fue procesar sin exportar.
        """
        tabla = self.historial
        for fila in range(tabla.rowCount()):
            if self._listas[fila]:
                return tabla.item(fila, 0).data(Qt.ItemDataRole.UserRole)
        return tabla.item(0, 0).data(Qt.ItemDataRole.UserRole)

    def _agregar_fila(self, carpeta: Path, csv: Path) -> None:
        entrega, listo = estado_de_entrega(csv)
        paginas = paginas_de_corrida(carpeta)
        fila = self.historial.rowCount()
        self.historial.insertRow(fila)
        self._listas.append(listo)
        celdas = (
            carpeta.name,
            "—" if paginas is None else str(paginas),
            entrega,
        )
        for columna, texto in enumerate(celdas):
            item = QTableWidgetItem(texto)
            if columna:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignVCenter
                )
            if not listo:
                # Sale en la lista igualmente: quien la busca tiene que
                # verla, y ver que lo que le falta es exportarla.
                item.setForeground(Qt.GlobalColor.gray)
            self.historial.setItem(fila, columna, item)
        self.historial.item(fila, 0).setData(
            Qt.ItemDataRole.UserRole, str(csv)
        )

    def _al_elegir_del_historial(self) -> None:
        filas = self.historial.selectionModel().selectedRows()
        if not filas:
            return
        item = self.historial.item(filas[0].row(), 0)
        csv = item.data(Qt.ItemDataRole.UserRole) if item else None
        if csv and csv != self.corrida_edit.text():
            self.fijar_corrida(csv)

    def _marcar_en_historial(self, csv: Path | str) -> None:
        """Deja señalada en la lista la ejecución elegida, si está en ella."""
        clave = str(Path(csv)).casefold()
        tabla = self.historial
        tabla.blockSignals(True)
        try:
            for fila in range(tabla.rowCount()):
                dato = tabla.item(fila, 0).data(Qt.ItemDataRole.UserRole)
                if str(dato).casefold() == clave:
                    tabla.selectRow(fila)
                    return
            # Elegida con «Otra ejecución…»: no está en la lista, y dejar
            # una fila marcada haría creer que se sube esa.
            tabla.clearSelection()
        finally:
            tabla.blockSignals(False)

    # ── estado de la ejecución ─────────────────────────────────────

    def fijar_corrida(self, csv: Path | str) -> None:
        """Apunta la ventana a una ejecución y propone el nombre del lote."""
        from app.airvault.flujo import carpeta_de_corrida, carpeta_de_trabajo
        from app.airvault.naming import nombre_desde_corrida

        # Cambiar de ejecución tira lo hecho, y con ello los lotes que
        # hubieran quedado tomados en AirVault: sin soltarlos quedan
        # colgados para quien los abra después.
        self._soltar_lotes()
        self._parar_vigilancia()
        ruta = Path(csv)
        self.corrida_edit.setText(str(ruta))
        self.lote_edit.setText(nombre_desde_corrida(ruta))
        self.boton_indexar.setEnabled(False)
        self.boton_reporte.setEnabled(False)
        self._marcar_en_historial(ruta)
        self._sincronizar_entrega(ruta)
        # Una ejecución que ya se subió en otro momento se retoma sin
        # volver a subir nada: sus manifiestos dicen en qué quedó.
        carpeta = self._raiz / carpeta_de_trabajo(carpeta_de_corrida(csv).name)
        self._cargar_trabajos(carpeta, ruta)

    def _cargar_trabajos(self, carpeta: Path, csv: Path) -> None:
        """Retoma los trabajos que ya existan para esta ejecución."""
        from app.airvault.flujo import cargar_partes, estado_local

        try:
            self._trabajos = cargar_partes(self._config_actual(), carpeta, csv)
        except Exception:  # noqa: BLE001 - sin trabajos se empieza de cero
            self._trabajos = []
        # La conexion sobrevive al cambio de ejecucion: es el mismo
        # servidor, y volver a abrirla es volver a arrancar el navegador.
        self._estado = {
            clave: self._estado[clave]
            for clave in ("cliente", "sesion") if clave in self._estado
        }
        self._estados = [estado_local(t) for t in self._trabajos]
        self._pintar_lotes()
        self._ajustar_vigilancia()

    def _sincronizar_entrega(self, csv: Path) -> None:
        """Dice si la ejecución elegida se puede subir, antes de intentarlo."""
        entrega, listo = estado_de_entrega(csv)
        self._listo_para_subir = listo
        self.boton_subir.setEnabled(listo)
        if listo:
            self.resumen.setText(TEXTO_SIN_SUBIR)
        elif entrega == "Sin exportar":
            self.resumen.setText(
                "La ejecución no tiene ningún PDF de entrega. Expórtela antes "
                "de subirla a AirVault."
            )
        else:
            self.resumen.setText(
                "La ejecución se exportó antes de que existiera el índice de "
                "páginas. Vuelva a exportarla para poder indexarla."
            )

    def _elegir_corrida(self) -> None:
        ruta, _filtro = QFileDialog.getOpenFileName(
            self, "Elegir el CSV de la ejecución",
            str(self._raiz / "output"), "CSV (*.csv *.CSV)",
        )
        if ruta:
            self.fijar_corrida(ruta)

    # ── la lista de lotes ──────────────────────────────────────────

    def _pintar_lotes(self) -> None:
        """Vuelca en la tabla en qué va cada lote."""
        from app.airvault.flujo import LISTO

        tabla = self.lotes
        tabla.setRowCount(0)
        for parte in self._estados:
            fila = tabla.rowCount()
            tabla.insertRow(fila)
            nombre = parte.nombre or "(sin nombre)"
            if parte.batch_id:
                nombre = f"{nombre}  ·  {parte.batch_id}"
            esperadas = len(parte.trabajo.manifiesto.registros)
            celdas = (nombre, str(esperadas), str(parte))
            for columna, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if columna == 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if parte.estado != LISTO and not parte.se_acabo:
                    # Todavía no hay nada que hacer con él: se ve, pero sin
                    # llamar la atención de quien busca cuál puede indexar.
                    item.setForeground(Qt.GlobalColor.gray)
                tabla.setItem(fila, columna, item)

    def _listos(self) -> list:
        """Partes que ya se pueden escribir y tienen su plan calculado."""
        planes = self._estado.get("planes") or {}
        return [
            parte.trabajo for parte in self._estados
            if parte.se_puede_indexar and str(parte.trabajo.carpeta) in planes
        ]

    def _falta_esperar(self) -> bool:
        """Si queda algún lote que AirVault todavía no ha terminado."""
        from app.airvault.flujo import LISTO

        return any(
            not parte.se_acabo and parte.estado != LISTO
            for parte in self._estados
        )

    # ── la comprobación periódica ──────────────────────────────────

    def _ajustar_vigilancia(self) -> None:
        """Arranca o para la comprobación automática según haga falta.

        Se pregunta mientras quede algo que esperar. Cuando todos los lotes
        están listos —o ya indexados, o son el de REVISAR— no hay nada que
        AirVault vaya a cambiar solo, así que se deja de preguntar en vez
        de golpear el servidor toda la tarde.
        """
        if not self.auto_check.isChecked() or not self._falta_esperar():
            self._parar_vigilancia()
            return
        if self._vigilante is None:
            self._vigilante = QTimer(self)
            self._vigilante.timeout.connect(self._comprobar_solo)
        self._vigilante.setInterval(self.minutos_spin.value() * 60_000)
        self._vigilante.start()

    def _parar_vigilancia(self) -> None:
        if self._vigilante is not None:
            self._vigilante.stop()

    def _comprobar_solo(self) -> None:
        """Lo que dispara el reloj. Se salta el turno si hay algo en vuelo."""
        if self.hilo() is not None:
            return
        self._comprobar()

    # ── acciones ───────────────────────────────────────────────────

    def _config_actual(self):
        from app.airvault.config import AIRVAULT_FILENAME, AirVaultConfig

        if self._config is None:
            self._config = AirVaultConfig.load(self._raiz / AIRVAULT_FILENAME)
        return self._config

    def _base_del_estado(self) -> Optional[dict]:
        """Los datos comunes del trabajo, o ``None`` si falta algo."""
        from app.airvault.flujo import carpeta_de_corrida, carpeta_de_trabajo

        csv = self.corrida_edit.text().strip()
        if not csv:
            self.resumen.setText(
                "Falta elegir la ejecución que se va a indexar."
            )
            return None
        if not self.lote_edit.text().strip():
            self.resumen.setText("Falta el nombre del lote en AirVault.")
            return None
        job = carpeta_de_corrida(csv).name
        self._estado.update({
            "config": self._config_actual(),
            "csv": csv,
            "raiz": self._raiz,
            "carpeta_job": self._raiz / carpeta_de_trabajo(job),
            "nombre_lote": self.lote_edit.text().strip(),
            "cookie": self.cookie_edit.text(),
        })
        self._estado.setdefault("trabajos", self._trabajos)
        return self._estado

    def _subir(self) -> None:
        estado = self._base_del_estado()
        if estado is None:
            return
        self._lanzar("subir", estado)

    def _comprobar(self) -> None:
        estado = self._base_del_estado()
        if estado is None:
            return
        if not estado.get("trabajos"):
            self.resumen.setText(
                "Esta ejecución todavía no se ha subido, así que no hay "
                "ningún lote que comprobar en AirVault."
            )
            return
        self._lanzar("comprobar", estado)

    def _indexar(self) -> None:
        listos = self._listos()
        if not listos:
            return
        self._estado["listos"] = listos
        self._estado["completar"] = self.completar_check.isChecked()
        self._lanzar("indexar", self._estado)

    def _lanzar(self, modo: str, estado: dict) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._habilitar(False)
        worker = TrabajoAirVaultWorker(modo, estado, self)
        worker.paso.connect(self._mostrar_paso)
        worker.subido.connect(self._al_subir)
        worker.comprobado.connect(self._al_comprobar)
        worker.indexado.connect(self._al_indexar)
        worker.fallo.connect(self._al_fallar)
        worker.cancelado.connect(self._al_cancelar)
        worker.finished.connect(self._al_terminar)
        self._worker = worker
        self._arrancar_reloj()
        worker.start()

    def _habilitar(self, activo: bool) -> None:
        # Mientras se trabaja no se cambia de ejecución: lo que está en
        # vuelo es de la que estaba elegida cuando arrancó.
        self.historial.setEnabled(activo)
        self.boton_subir.setEnabled(activo and self._listo_para_subir)
        self.boton_buscar.setEnabled(activo)
        self.lote_edit.setEnabled(activo)
        self.cookie_edit.setEnabled(activo)
        self.boton_comprobar.setEnabled(activo and bool(self._trabajos))
        # Cerrar y Cancelar nunca se apagan a la vez: mientras hay trabajo
        # en vuelo tiene que haber siempre algo que pulsar, o la ventana se
        # queda muda durante una espera de minutos.
        self.boton_cancelar.setEnabled(not activo)
        self.boton_indexar.setEnabled(activo and bool(self._listos()))

    # ── el reloj del paso ──────────────────────────────────────────

    def _arrancar_reloj(self) -> None:
        """Deja a la vista que el trabajo sigue vivo mientras espera.

        La barra sola no basta: en las etapas sin cuenta —entrar a
        AirVault, esperar a que el lote salga de la cola— no se mueve, y
        una espera de diez minutos se lee como un cuelgue.
        """
        self._inicio_paso = time.monotonic()
        if self._reloj is None:
            self._reloj = QTimer(self)
            self._reloj.setInterval(1000)
            self._reloj.timeout.connect(self._marcar_reloj)
        self._marcar_reloj()
        self._reloj.start()

    def _parar_reloj(self) -> None:
        if self._reloj is not None:
            self._reloj.stop()
        self.reloj_label.setText("")

    def _marcar_reloj(self) -> None:
        segundos = int(time.monotonic() - self._inicio_paso)
        self.reloj_label.setText(f"{segundos // 60:d}:{segundos % 60:02d}")

    # ── respuestas del hilo ────────────────────────────────────────

    def _mostrar_paso(self, texto: str, hechas: int, total: int) -> None:
        self.estado_label.setText(texto)
        self.estado_label.setToolTip(texto)
        if total > 0:
            self.progreso.setRange(0, total)
            self.progreso.setValue(min(hechas, total))
        else:
            # Sin cuenta, la barra va en marcha continua: parada en cero se
            # lee como que no está pasando nada.
            self.progreso.setRange(0, 0)
        # Los avisos de una subida llegan por trozo —mil en una entrega
        # grande—, así que solo se anota cuando cambia el texto: la
        # bitácora cuenta por qué etapa va, no cuántas veces avisó.
        if texto != self._ultimo_paso:
            self._ultimo_paso = texto
            self._inicio_paso = time.monotonic()
            self._anotar(texto)

    def _anotar(self, texto: str) -> None:
        """Apunta el paso en la bitácora, con su hora."""
        self.bitacora.addItem(f"{time.strftime('%H:%M:%S')}  {texto}")
        while self.bitacora.count() > LIMITE_BITACORA:
            self.bitacora.takeItem(0)
        self.bitacora.scrollToBottom()

    def _al_subir(self, datos: dict) -> None:
        self._trabajos = datos["trabajos"]
        self._estado["trabajos"] = self._trabajos
        cuantos = len(self._trabajos)
        lotes = "el lote" if cuantos == 1 else f"los {cuantos} lotes"
        self.resumen.setText(
            f"Subida terminada. AirVault tiene que procesar {lotes} antes "
            f"de poder indexarlos, y eso tarda: se va preguntando solo y "
            f"la lista de arriba dice en qué van."
        )
        self.estado_label.setText("Subida terminada")
        self._anotar("Subida terminada; falta que AirVault los procese")
        # Se pregunta ya, sin esperar al primer turno del reloj: a veces el
        # lote está listo enseguida.
        self._comprobar_al_terminar = True

    def _al_comprobar(self, datos: dict) -> None:
        from app.airvault.flujo import LISTO

        self._estado["planes"] = datos["planes"]
        self._estados = datos["estados"]
        self._pintar_lotes()
        self._ajustar_vigilancia()
        if datos.get("reporte"):
            self.boton_reporte.setEnabled(True)
        listos = [p for p in self._estados if p.estado == LISTO]
        self.boton_indexar.setEnabled(bool(self._listos()))
        self.estado_label.setText("Comprobado")
        if listos:
            self.resumen.setText(self._resumen_de_listos(datos["partes"]))
        elif self._falta_esperar():
            pendientes = ", ".join(
                f"{p.nombre}: {p}" for p in self._estados if not p.se_acabo
            )
            self.resumen.setText(
                f"AirVault todavía no ha terminado. {pendientes}. Se vuelve "
                f"a preguntar solo cada {self.minutos_spin.value()} min."
            )
        else:
            self.resumen.setText(
                "No queda nada pendiente en AirVault para esta ejecución."
            )
        self._anotar(
            "Comprobado: "
            + "; ".join(f"{p.nombre} {p}" for p in self._estados)
        )
        self._limpiar_progreso()

    def _resumen_de_listos(self, partes) -> str:
        """Cuántas páginas se escribirían y cuántas quedan bloqueadas."""
        from app.airvault.report import _resumen_sumado

        if not partes:
            return "Hay lotes listos; falta calcular qué se les escribiría."
        resumen = _resumen_sumado(partes)
        donde = (
            f"{len(partes)} lotes" if len(partes) > 1
            else f"Lote {partes[0][0] or partes[0][1].batch_id}"
        )
        return (
            f"{donde}: {resumen['total']} páginas, "
            f"{resumen['escribibles']} se escribirían y "
            f"{resumen['bloqueadas']} quedan bloqueadas. "
            f"Nada se ha escrito todavía."
        )

    def _al_indexar(self, datos: dict) -> None:
        resultado = datos["resultado"]
        lotes = datos.get("lotes", 1)
        self.boton_indexar.setEnabled(False)
        donde = f" en {lotes} lotes" if lotes > 1 else ""
        cuenta = (
            f"Escritas {resultado.escritas}, omitidas {resultado.omitidas} "
            f"y fallidas {resultado.fallidas}. En AirVault quedaron "
            f"{datos['validas']} de {datos['total']} páginas válidas{donde}."
        )
        if resultado.interrumpido:
            self.resumen.setText(
                f"{cuenta} El indexado se cortó: {resultado.interrumpido} "
                f"Lo que falta queda pendiente; al volver a comprobar se "
                f"retoma sin repetir lo escrito."
            )
            self.estado_label.setText("El indexado se cortó a medio camino")
            self._limpiar_progreso()
            return
        self.resumen.setText(cuenta + self._cuenta_de_cierres(datos))
        self.estado_label.setText("Indexado terminado")
        self._limpiar_progreso()
        # Vuelve a preguntar para que la lista quede diciendo cómo acabó
        # cada lote, en vez de con lo que se sabía antes de escribir.
        self._comprobar_al_terminar = True

    def _cuenta_de_cierres(self, datos: dict) -> str:
        """Qué pasó con «Completar batch», si estaba marcado."""
        cierres = datos.get("cierres") or []
        if not cierres:
            return ""
        cerrados = [t for t, r in cierres if r.completado]
        colgados = [(t, r) for t, r in cierres if not r.completado]
        partes = []
        if cerrados:
            texto = (
                f" Se cerraron {len(cerrados)} lotes en AirVault."
                if len(cerrados) > 1 else " El lote quedó cerrado en AirVault."
            )
            quitadas = sum(len(r.quitadas) for _t, r in cierres if r.completado)
            if quitadas:
                # Se dice porque es un cambio en el lote: esas páginas ya no
                # están, y quien lo abra en AirVault no las va a encontrar.
                texto += (
                    f" Se quitaron {quitadas} páginas separadoras, que no son "
                    f"bitácoras y no dejan cerrar el lote."
                )
            partes.append(texto)
        for trabajo, resultado in colgados:
            partes.append(
                f" El lote {trabajo.manifiesto.nombre_batch} no se pudo "
                f"cerrar: {resultado.detalle}"
            )
        return "".join(partes)

    def _al_fallar(self, mensaje: str) -> None:
        self.resumen.setText(mensaje)
        self.estado_label.setText("El indexado no pudo continuar")
        self._anotar(f"Se detuvo: {mensaje}")
        # Un fallo con la comprobación automática puesta se repetiría cada
        # cinco minutos con el mismo error: se para y se vuelve a pulsar.
        self._parar_vigilancia()
        self._limpiar_progreso()

    def _al_cancelar(self) -> None:
        """Lo paró quien lo lanzó: se dice y se sueltan los lotes."""
        self.estado_label.setText("Cancelado")
        self._anotar("Cancelado; se sueltan los lotes tomados en AirVault")
        self._parar_vigilancia()
        self._soltar_lotes()
        self.resumen.setText(
            "El trabajo se canceló. Los lotes quedaron sueltos en AirVault "
            "y lo que ya se hubiera escrito se conserva: al volver a "
            "comprobar se retoma sin repetirlo."
        )
        self._limpiar_progreso()

    def _al_terminar(self) -> None:
        """Cierre común del hilo, salga como salga."""
        self._habilitar(True)
        self._parar_reloj()
        if self._cerrar_al_terminar:
            self._cerrar_al_terminar = False
            self.close()
            return
        if getattr(self, "_comprobar_al_terminar", False):
            self._comprobar_al_terminar = False
            self._comprobar()

    def _limpiar_progreso(self) -> None:
        """Deja la barra quieta en cero: lo que pasó lo cuenta el resumen."""
        self.progreso.setRange(0, 100)
        self.progreso.setValue(0)
        self._parar_reloj()

    def _abrir_reporte(self) -> None:
        reporte = self._estado.get("reporte")
        if reporte and Path(reporte).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(reporte)))

    # ── cierre ─────────────────────────────────────────────────────

    def hilo(self) -> Optional[QThread]:
        """Hilo del indexado si está en marcha, para que el cierre lo espere.

        Cerrar el programa destruyendo un ``QThread`` vivo lo mata, y este
        puede estar a medio escribir un lote.
        """
        worker = self._worker
        if worker is None:
            return None
        try:
            return worker if worker.isRunning() else None
        except RuntimeError:
            # El objeto C++ ya se destruyó tras ``deleteLater``.
            return None

    def closeEvent(self, event) -> None:
        """Cerrar siempre se puede; con trabajo en vuelo, lo cancela antes.

        Antes se negaba a cerrar mientras hubiera un hilo vivo, y como el
        hilo podía estar esperando cinco minutos a que alguien entrara a
        AirVault, la ventana se quedaba sin salida: ni cerraba, ni avanzaba,
        ni había nada que pulsar. Ahora se pide la cancelación y la ventana
        se va sola en cuanto el hilo suelta los lotes que tuviera tomados,
        que es lo que no se puede dejar a medias.

        Lo comprobado sí sobrevive a un cierre sin trabajo en vuelo: los
        manifiestos guardan en qué quedó cada lote y al reabrir se retoma.

        Cerrarla **no** apaga la comprobación automática. Esperar a que
        AirVault procese un lote puede llevar horas, y lo normal es cerrar
        esta ventana y seguir procesando en la principal; al volver, la
        lista ya está al día. El que sí la apaga es el cierre del programa.
        """
        if self.hilo() is not None:
            self._cerrar_al_terminar = True
            self._cancelar()
            self.resumen.setText(
                "Cancelando el trabajo en marcha. La ventana se cierra en "
                "cuanto AirVault suelte los lotes que tenía tomados."
            )
            event.ignore()
            return
        super().closeEvent(event)

    def _cancelar(self) -> None:
        """Le pide al hilo que pare. No espera: esperar congelaría esto."""
        worker = self.hilo()
        if worker is None:
            return
        worker.cancelar()
        self.boton_cancelar.setEnabled(False)
        self.estado_label.setText("Cancelando…")
        self._anotar("Cancelando: se espera a que suelte lo que tiene tomado")

    def detener(self) -> None:
        """Pide al hilo que pare; la llama la ventana principal al cerrarse."""
        self._parar_vigilancia()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancelar()
            self._worker.wait(5000)
        # Aquí sí se espera: el programa se está cerrando y un lote que
        # queda tomado deja colgada la próxima apertura.
        self._soltar_lotes(esperar=True)
        # Y a los que ya estuvieran soltando en su propio hilo: destruirlos
        # a media petición deja el lote tomado, que es lo que se venía a
        # evitar.
        for hilo in list(self._soltando):
            hilo.wait(5000)

    def _soltar_lotes(self, esperar: bool = False) -> None:
        """Suelta en AirVault los lotes que hubieran quedado tomados.

        Con el recorrido normal no queda ninguno: leer el lote lo suelta en
        cuanto termina y escribirlo también. Esto es para lo que se corta
        por el medio —un cierre, una cancelación, un fallo de red—, porque
        un lote tomado no da error: deja colgada la próxima vez que alguien
        lo abra.

        Va en un hilo aparte salvo al cerrar el programa. Soltar es una
        petición por lote contra un servidor que puede tardar un minuto en
        contestar, y hacerlo en el hilo de la ventana la dejaba congelada
        justo al cambiar de ejecución o al cancelar.
        """
        trabajos = self._trabajos
        cliente = self._estado.get("cliente")
        if not trabajos or cliente is None:
            return
        if esperar:
            _soltar(trabajos, cliente)
            return
        hilo = SoltarLotesWorker(trabajos, cliente, self)
        # Se guarda la referencia: un QThread sin dueño vivo se destruye al
        # salir del método y Qt lo mata a media petición.
        self._soltando.append(hilo)
        hilo.finished.connect(lambda: self._soltando.remove(hilo)
                              if hilo in self._soltando else None)
        hilo.start()
