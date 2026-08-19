"""Panel «Indexar en AirVault» de la ventana principal.

Es la cara de :mod:`app.airvault`: no decide nada por su cuenta, solo pide
los datos que hacen falta, lanza el recorrido en un hilo aparte y cuenta
como fue. Todo lo que decide si una página se escribe o no vive en el
módulo, que se prueba sin interfaz.

Va desplegable, como «Opciones avanzadas», porque se usa una vez al final
de cada corrida y cerrado no le quita alto a la vista previa.

El trabajo se hace en dos tiempos a propósito. «Subir y revisar» deja el
lote en AirVault y calcula qué se escribiría, sin tocar ni un índice;
«Indexar» solo se habilita después, cuando ya hay un reporte que mirar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from app.gui.widgets import ElidedLabel

# Gris con el que la ventana escribe las líneas de ayuda; es el mismo que
# usan la distribución automática de hilos y el aviso del OCR.
COLOR_AYUDA = "#57606a"

TEXTO_SIN_REVISAR = (
    "Sin revisar. La corrida se sube a AirVault y se calcula qué se "
    "escribiría; nada se indexa hasta aprobarlo."
)


class TrabajoAirVaultWorker(QThread):
    """Corre las etapas del indexado fuera del hilo de la interfaz.

    Una corrida completa sube casi dos gigas y escribe cientos de páginas
    por red; hecho en el hilo de la ventana, Windows la daría por colgada.
    """

    paso = Signal(str, int, int)
    revisado = Signal(object)
    indexado = Signal(object)
    fallo = Signal(str)

    def __init__(self, modo: str, panel_estado: dict, parent=None) -> None:
        super().__init__(parent)
        self.modo = modo
        self.estado = panel_estado

    # ── ejecución ──────────────────────────────────────────────────

    def run(self) -> None:  # noqa: D102 - lo describe la clase
        try:
            if self.modo == "revisar":
                self._revisar()
            else:
                self._indexar()
        except Exception as exc:  # noqa: BLE001 - llega a la interfaz
            self.fallo.emit(str(exc))

    def _avisar(self, texto: str, hechas: int, total: int) -> None:
        self.paso.emit(texto, int(hechas), int(total))

    def _dormir(self, segundos: float) -> None:
        """Espera troceada para que cancelar la ventana no tarde minutos."""
        restante = float(segundos)
        while restante > 0 and not self.isInterruptionRequested():
            self.msleep(int(min(1.0, restante) * 1000))
            restante -= 1.0

    def _revisar(self) -> None:
        from app.airvault.client import ClienteHttp
        from app.airvault.flujo import (
            comprobar_entrega,
            descubrir_partes,
            planificar_partes,
            preparar_partes,
            subir_partes,
        )
        from app.airvault.mapping import FLOTA_CACHE_FILENAME, ResolutorFlota
        from app.airvault.report import (
            escribir_csv_de_partes,
            escribir_html_de_partes,
        )
        from app.airvault.session import abrir_sesion

        estado = self.estado
        config = estado["config"]
        csv = Path(estado["csv"])
        raiz = Path(estado["raiz"])
        carpeta = Path(estado["carpeta_job"])

        self._avisar("Leyendo la corrida", 0, 0)
        entrega = comprobar_entrega(csv)
        resolutor = ResolutorFlota.load(raiz / FLOTA_CACHE_FILENAME)
        trabajos = preparar_partes(
            config, carpeta, csv, estado["nombre_lote"], resolutor=resolutor,
        )
        if len(trabajos) > 1:
            self._avisar(
                f"La corrida va en {len(entrega)} partes, una por lote", 0, 0
            )

        self._avisar("Entrando a AirVault", 0, 0)
        sesion = abrir_sesion(
            config, cookie=estado.get("cookie") or None,
            avisar=lambda texto: self._avisar(texto, 0, 0),
        )
        sesion.comprobar()
        cliente = ClienteHttp(sesion, config)

        subir_partes(trabajos, sesion, avisar=self._avisar)
        descubrir_partes(
            trabajos, cliente, esperar=True, dormir=self._dormir,
            avisar=self._avisar,
        )
        planes = planificar_partes(
            trabajos, cliente, resolutor, avisar=self._avisar
        )
        resolutor.guardar(raiz / FLOTA_CACHE_FILENAME)

        # Un solo reporte para toda la corrida: se aprueba de una vez, no
        # lote por lote.
        partes = [
            (t.manifiesto.nombre_batch, plan)
            for t, (plan, _indexador) in zip(trabajos, planes)
        ]
        escribir_csv_de_partes(partes, carpeta / "revision.csv")
        reporte = escribir_html_de_partes(
            partes, carpeta / "revision.html",
            f"Indexado de {carpeta.name}",
        )
        self.revisado.emit({
            "trabajos": trabajos, "planes": planes, "partes": partes,
            "cliente": cliente, "sesion": sesion, "reporte": reporte,
            "origen": sesion.origen,
        })

    def _indexar(self) -> None:
        from app.airvault.flujo import indexar_partes, verificar_partes

        estado = self.estado
        trabajos = estado["trabajos"]
        resultado = indexar_partes(
            trabajos, estado["planes"], avisar=self._avisar
        )
        self._avisar("Comprobando como quedaron los lotes", 0, 0)
        validas, total, _problemas = verificar_partes(
            trabajos, estado["cliente"]
        )
        self.indexado.emit({
            "resultado": resultado, "validas": validas, "total": total,
            "lotes": len(trabajos),
        })


class AirVaultPanel(QWidget):
    """Panel desplegable que indexa la corrida en AirVault."""

    # La ventana principal las engancha a su barra y a su etiqueta de
    # estado: el panel no dibuja indicadores propios.
    estado_cambiado = Signal(str)
    progreso_cambiado = Signal(int, int)
    desplegado = Signal(bool)

    def __init__(self, raiz: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raiz = Path(raiz)
        self._worker: Optional[TrabajoAirVaultWorker] = None
        self._revision: dict = {}
        self._config = None

        # La flecha no se guarda aqui dentro: la ventana la pone en la
        # misma fila que la de «Opciones avanzadas», de modo que el panel
        # cerrado no le quita ni un pixel de alto a la vista previa.
        self.boton_desplegar = QToolButton()
        self.boton_desplegar.setText("Indexar en AirVault")
        self.boton_desplegar.setCheckable(True)
        self.boton_desplegar.setArrowType(Qt.ArrowType.RightArrow)
        self.boton_desplegar.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.boton_desplegar.setToolTip(
            "Escribe en AirVault los datos de la corrida, sin teclear "
            "página por página en el Web Index."
        )
        self.boton_desplegar.toggled.connect(self._alternar)

        cuerpo = QVBoxLayout(self)
        cuerpo.setContentsMargins(0, 0, 0, 0)
        cuerpo.addLayout(self._fila_corrida())
        cuerpo.addLayout(self._fila_lote())
        cuerpo.addLayout(self._fila_sesion())
        cuerpo.addLayout(self._fila_botones())

        self.resumen = ElidedLabel(TEXTO_SIN_REVISAR)
        self.resumen.setStyleSheet(f"color: {COLOR_AYUDA};")
        cuerpo.addWidget(self.resumen)
        self.setVisible(False)

    # ── construcción ───────────────────────────────────────────────

    def _fila_corrida(self) -> QHBoxLayout:
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Corrida:"))
        self.corrida_edit = QLineEdit()
        self.corrida_edit.setReadOnly(True)
        self.corrida_edit.setPlaceholderText(
            "CSV de la corrida que se va a indexar"
        )
        self.corrida_edit.setToolTip(
            "CSV de la corrida cuyos datos se escriben en AirVault. Se "
            "rellena solo con la corrida que acaba de terminar."
        )
        fila.addWidget(self.corrida_edit)
        self.boton_buscar = QPushButton("Buscar…")
        self.boton_buscar.setToolTip("Elegir el CSV de otra corrida")
        self.boton_buscar.clicked.connect(self._elegir_corrida)
        fila.addWidget(self.boton_buscar)
        return fila

    def _fila_lote(self) -> QHBoxLayout:
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Lote:"))
        self.lote_edit = QLineEdit()
        self.lote_edit.setPlaceholderText("Nombre del lote en AirVault")
        self.lote_edit.setToolTip(
            "Nombre con el que el lote queda en AirVault. Lleva la fecha y "
            "la hora de la corrida para que no se confunda con otro: en la "
            "cola conviven lotes con nombres repetidos."
        )
        fila.addWidget(self.lote_edit)
        return fila

    def _fila_sesion(self) -> QHBoxLayout:
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Sesión:"))
        # El campo queda por si el navegador no puede: el camino normal es
        # que la sesión se resuelva sola.
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
        fila.addWidget(self.cookie_edit)
        return fila

    def _fila_botones(self) -> QHBoxLayout:
        fila = QHBoxLayout()
        self.boton_revisar = QPushButton("Subir y revisar")
        self.boton_revisar.setToolTip(
            "Sube la corrida a AirVault, espera a que aparezca el lote y "
            "calcula qué se escribiría en cada página. No indexa nada."
        )
        self.boton_revisar.clicked.connect(self._revisar)
        fila.addWidget(self.boton_revisar)

        self.boton_indexar = QPushButton("Indexar")
        self.boton_indexar.setEnabled(False)
        self.boton_indexar.setToolTip(
            "Escribe en AirVault los datos aprobados en la revisión. Las "
            "páginas marcadas como bloqueadas no se tocan."
        )
        self.boton_indexar.clicked.connect(self._indexar)
        fila.addWidget(self.boton_indexar)

        self.boton_reporte = QPushButton("Ver reporte…")
        self.boton_reporte.setEnabled(False)
        self.boton_reporte.setToolTip(
            "Abre el detalle página por página de lo que se escribiría"
        )
        self.boton_reporte.clicked.connect(self._abrir_reporte)
        fila.addWidget(self.boton_reporte)
        fila.addStretch()
        return fila

    # ── estado de la corrida ───────────────────────────────────────

    def fijar_corrida(self, csv: Path | str) -> None:
        """Apunta el panel a una corrida y propone el nombre del lote."""
        from app.airvault.naming import nombre_desde_corrida

        ruta = Path(csv)
        self.corrida_edit.setText(str(ruta))
        self.lote_edit.setText(nombre_desde_corrida(ruta))
        self._revision = {}
        self.boton_indexar.setEnabled(False)
        self.boton_reporte.setEnabled(False)
        self.resumen.setText(TEXTO_SIN_REVISAR)

    def _elegir_corrida(self) -> None:
        ruta, _filtro = QFileDialog.getOpenFileName(
            self, "Elegir el CSV de la corrida",
            str(self._raiz / "output"), "CSV (*.csv *.CSV)",
        )
        if ruta:
            self.fijar_corrida(ruta)

    def _alternar(self, desplegado: bool) -> None:
        self.setVisible(desplegado)
        self.boton_desplegar.setArrowType(
            Qt.ArrowType.DownArrow if desplegado else Qt.ArrowType.RightArrow
        )
        self.desplegado.emit(desplegado)

    # ── acciones ───────────────────────────────────────────────────

    def _config_actual(self):
        from app.airvault.config import AIRVAULT_FILENAME, AirVaultConfig

        if self._config is None:
            self._config = AirVaultConfig.load(self._raiz / AIRVAULT_FILENAME)
        return self._config

    def _revisar(self) -> None:
        from app.airvault.flujo import carpeta_de_corrida, carpeta_de_trabajo

        csv = self.corrida_edit.text().strip()
        if not csv:
            self.resumen.setText("Falta elegir la corrida que se va a indexar.")
            return
        if not self.lote_edit.text().strip():
            self.resumen.setText("Falta el nombre del lote en AirVault.")
            return
        job = carpeta_de_corrida(csv).name
        self._lanzar("revisar", {
            "config": self._config_actual(),
            "csv": csv,
            "raiz": self._raiz,
            "carpeta_job": self._raiz / carpeta_de_trabajo(job),
            "nombre_lote": self.lote_edit.text().strip(),
            "cookie": self.cookie_edit.text(),
        })

    def _indexar(self) -> None:
        if not self._revision:
            return
        self._lanzar("indexar", dict(self._revision))

    def _lanzar(self, modo: str, estado: dict) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._habilitar(False)
        worker = TrabajoAirVaultWorker(modo, estado, self)
        worker.paso.connect(self._mostrar_paso)
        worker.revisado.connect(self._al_revisar)
        worker.indexado.connect(self._al_indexar)
        worker.fallo.connect(self._al_fallar)
        worker.finished.connect(lambda: self._habilitar(True))
        self._worker = worker
        worker.start()

    def _habilitar(self, activo: bool) -> None:
        self.boton_revisar.setEnabled(activo)
        self.boton_buscar.setEnabled(activo)
        self.lote_edit.setEnabled(activo)
        self.cookie_edit.setEnabled(activo)
        self.boton_indexar.setEnabled(activo and bool(self._revision))

    # ── respuestas del hilo ────────────────────────────────────────

    def _mostrar_paso(self, texto: str, hechas: int, total: int) -> None:
        self.estado_cambiado.emit(texto)
        self.progreso_cambiado.emit(hechas, total)

    def _al_revisar(self, datos: dict) -> None:
        from app.airvault.report import _resumen_sumado

        partes = datos["partes"]
        resumen = _resumen_sumado(partes)
        self._revision = datos
        self.boton_indexar.setEnabled(True)
        self.boton_reporte.setEnabled(True)
        donde = (
            f"{len(partes)} lotes" if len(partes) > 1
            else f"Lote {partes[0][0] or partes[0][1].batch_id}"
        )
        self.resumen.setText(
            f"{donde}: {resumen['total']} páginas, "
            f"{resumen['escribibles']} se escribirían y "
            f"{resumen['bloqueadas']} quedan bloqueadas. "
            f"Nada se ha escrito todavía."
        )
        self.estado_cambiado.emit("Revisión lista")

    def _al_indexar(self, datos: dict) -> None:
        resultado = datos["resultado"]
        lotes = datos.get("lotes", 1)
        donde = f" en {lotes} lotes" if lotes > 1 else ""
        cuenta = (
            f"Escritas {resultado.escritas}, omitidas {resultado.omitidas} "
            f"y fallidas {resultado.fallidas}. En AirVault quedaron "
            f"{datos['validas']} de {datos['total']} páginas válidas{donde}."
        )
        if resultado.interrumpido:
            self.resumen.setText(
                f"{cuenta} El indexado se cortó: {resultado.interrumpido} "
                f"Lo que falta queda pendiente; al volver a revisar se "
                f"retoma sin repetir lo escrito."
            )
            self.estado_cambiado.emit("El indexado se cortó a medio camino")
            self.progreso_cambiado.emit(0, 0)
            return
        self.resumen.setText(cuenta)
        self.estado_cambiado.emit("Indexado terminado")
        self.progreso_cambiado.emit(0, 0)

    def _al_fallar(self, mensaje: str) -> None:
        self.resumen.setText(mensaje)
        self.estado_cambiado.emit("El indexado no pudo continuar")
        self.progreso_cambiado.emit(0, 0)

    def _abrir_reporte(self) -> None:
        reporte = self._revision.get("reporte")
        if reporte and Path(reporte).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(reporte)))

    # ── cierre ─────────────────────────────────────────────────────

    def hilo(self) -> Optional[QThread]:
        """Hilo del indexado si esta en marcha, para que el cierre lo espere.

        Cerrar la ventana destruyendo un ``QThread`` vivo mata el programa,
        y este puede estar a medio escribir un lote.
        """
        worker = self._worker
        if worker is None:
            return None
        try:
            return worker if worker.isRunning() else None
        except RuntimeError:
            # El objeto C++ ya se destruyo tras ``deleteLater``.
            return None

    def detener(self) -> None:
        """Pide al hilo que pare; lo llama la ventana al cerrarse."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(5000)
