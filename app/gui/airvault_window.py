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

1. **Subir a AirVault** manda un PDF y espera a identificarlo antes de mandar
   el siguiente, para que AirVault no junte dos partes en el mismo batch.
2. **Comprobar** asigna el ID apenas aparece y confirma si ya está entero.
3. **Indexar** trabaja en paralelo los batches listos mientras el primer
   carril sigue buscando los demás. Se puede desactivar en Automatización.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.airvault.flujo import PAGINAS_POR_BATCH_POR_DEFECTO
from app.gui.csv_utils import find_csv_files, find_run_dirs
from app.gui.responsive import fit_to_screen
from app.gui.widgets import (
    APP_CHROME_QSS,
    DATA_TABLE_QSS,
    PANE_STATUS_COLORS,
    ElidedLabel,
    align_vertical_scrollbar_to_header,
    style_data_table,
)
from app.utils.io import send_to_trash

# Gris con el que la ventana principal escribe las líneas de ayuda.
COLOR_AYUDA = "#57606a"
COLOR_INDEXADO = PANE_STATUS_COLORS["OK"]

# Ejecuciones que lista el historial, las mismas que el visor de CSV: es la
# ventana de trabajo de un turno. Lo de más atrás sigue en output/ y se
# alcanza con «Otra ejecución…».
LIMITE_HISTORIAL = 25

# Cada cuántos minutos se le pregunta a AirVault sin que nadie pulse nada.
# Dos minutos mantiene la cola al dia sin convertir la espera en sondeo
# continuo; el valor sigue siendo configurable en la ventana.
MINUTOS_POR_DEFECTO = 2

# Una respuesta de guardado puede ser aceptada por HTTP y aun dejar la pagina
# en Need Correction durante un instante. Se relee y reenvia en el mismo
# proceso antes de devolver el control a la persona.
INTENTOS_INDEXADO = 3

# Líneas que conserva la bitácora. Con la comprobación automática corriendo
# toda una tarde, sin tope crecería sin fin.
LIMITE_BITACORA = 300

# El nombre distingue las divisiones y REVISAR, así que no puede quedar
# reducido a unas pocas letras. A partir de este ancho se conserva espacio
# para Páginas y Estado; el texto completo sigue disponible en la ayuda.
ANCHO_MINIMO_NOMBRE_BATCH = 220
ANCHO_MAXIMO_NOMBRE_BATCH = 420

TEXTO_SIN_SUBIR = (
    "Sin subir. «Subir a AirVault» manda cada PDF por separado; cuando "
    "AirVault le asigna un ID, el indexado automático empieza en paralelo."
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
        # pero nada que diga qué página del batch es cuál.
        return "Falta reexportar", False
    return ("1 archivo" if len(pdfs) == 1 else f"{len(pdfs)} archivos"), True


class TrabajoCancelado(BaseException):
    """Alguien pulsó Cancelar; el trabajo se deshace y se sale.

    Hereda de ``BaseException`` a propósito: el recorrido del indexado
    atrapa ``Exception`` en varios sitios para anotar la página que falló y
    seguir, y una cancelación no puede quedarse ahí anotada como si fuera
    el error de una página. Así atraviesa todo hasta el hilo, pasando por
    los ``finally`` que sueltan los batches en AirVault.
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
    subidas_actualizadas = Signal(object)
    batch_encontrado = Signal(object)
    batch_indexado = Signal(object)
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
            "subir_pendientes": self._subir_pendientes,
            "comprobar": self._comprobar,
            "indexar": self._indexar,
            "completar": self._completar,
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

        AirVault puede tardar minutos en sacar el batch de su cola. Dormir
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

    def _notificar_subidas(self, trabajos) -> None:
        """Publica el estado local antes de empezar a buscar los IDs."""
        actuales = self.estado.get("trabajos") or trabajos
        self.subidas_actualizadas.emit({"trabajos": list(actuales)})

    # ── la conexión ────────────────────────────────────────────────

    def _conectar(self):
        """Devuelve el cliente de AirVault, abriendo sesión si hace falta.

        La comprobación periódica reusa el mismo: la sesión se renueva sola
        cuando caduca, así que volver a abrirla cada dos minutos serían
        dos viajes al navegador para nada.
        """
        from app.airvault.client import ClienteHttp
        from app.airvault.session import abrir_sesion, comprobar_o_renovar

        cliente = self.estado.get("cliente")
        if cliente is not None:
            self._detectar_pendientes()
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
        self._detectar_pendientes()
        return cliente

    def _detectar_pendientes(self) -> None:
        """Agrega manifiestos propios de batches subidos anteriormente."""
        from app.airvault.flujo import (
            CARPETA_TRABAJOS,
            cargar_trabajos_pendientes,
        )

        actuales = list(self.estado.get("trabajos") or [])
        conocidos = {str(t.carpeta.resolve()).casefold() for t in actuales}
        encontrados = cargar_trabajos_pendientes(
            self.estado["config"], Path(self.estado["raiz"]) / CARPETA_TRABAJOS,
        )
        nuevos = [
            t for t in encontrados
            if str(t.carpeta.resolve()).casefold() not in conocidos
        ]
        if nuevos:
            actuales.extend(nuevos)
            self._avisar(
                f"Se recuperaron {len(nuevos)} batches pendientes de "
                "ejecuciones anteriores", 0, 0,
            )
        self.estado["trabajos"] = actuales

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
            paginas_por_batch=estado["paginas_por_batch"],
            avisar=self._avisar,
            compresion=estado.get("compresion", False),
        )
        estado["trabajos"] = trabajos
        for trabajo in trabajos:
            manifiesto = trabajo.manifiesto
            self._avisar(
                f"Batch «{manifiesto.nombre_batch}»: "
                f"{len(manifiesto.bitacoras())} bitácoras y "
                f"{len(manifiesto.separadores())} separadores", 0, 0,
            )

        cliente = self._conectar()
        futuros: list[Future] = []
        en_cola: set[str] = set()
        ejecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="airvault-indexado"
        )
        cliente_indice = self._cliente_paralelo(cliente)

        def al_encontrar(trabajo, todos) -> None:
            """Publica el ID y pone el batch listo en el carril de escritura."""
            from app.airvault.flujo import comprobar_partes

            remoto = comprobar_partes(
                [trabajo], cliente, avisar=self._avisar
            )[0]
            self.batch_encontrado.emit({
                "trabajos": list(todos), "estado": remoto,
            })
            clave = str(trabajo.carpeta)
            if (
                not estado.get("indexar_al_encontrar")
                or trabajo.manifiesto.solo_subir
                or not remoto.se_puede_indexar
                or clave in en_cola
            ):
                return
            en_cola.add(clave)
            futuros.append(ejecutor.submit(
                self._indexar_batch_encontrado,
                trabajo, cliente_indice, raiz,
            ))

        try:
            subir_partes(
                trabajos, estado["sesion"], avisar=self._avisar,
                cliente=cliente, dormir=self._dormir,
                al_finalizar_subidas=self._notificar_subidas,
                al_encontrar=al_encontrar,
            )
            # La escritura corre mientras este hilo sube y busca las partes
            # siguientes. Antes de anunciar el final se espera solo lo que
            # todavía siga en el carril de indexado.
            for futuro in futuros:
                futuro.result()
        finally:
            ejecutor.shutdown(wait=True, cancel_futures=True)
        self.subido.emit({
            "trabajos": estado["trabajos"], "cliente": cliente,
            "sesion": estado.get("sesion"),
        })

    def _cliente_paralelo(self, cliente):
        """Cliente independiente para indexar sin compartir Session con Upload."""
        from app.airvault.client import ClienteHttp

        sesion = self.estado.get("sesion")
        clonar = getattr(sesion, "clonar", None)
        if not callable(clonar):
            # Clientes falsos y adaptadores antiguos ya son objetos aislados
            # en sus pruebas; conservarlos mantiene ese contrato.
            return cliente
        return ClienteHttp(clonar(), self.estado["config"])

    def _indexar_batch_encontrado(self, trabajo, cliente, raiz: Path) -> dict:
        """Planifica e indexa un batch mientras se buscan los siguientes."""
        from app.airvault.mapping import FLOTA_CACHE_FILENAME, ResolutorFlota

        self._avisar(
            f"Batch {trabajo.manifiesto.batch_id} encontrado; preparando su "
            "indexado en paralelo", 0, 0,
        )
        resolutor = ResolutorFlota.load(raiz / FLOTA_CACHE_FILENAME)
        plan = trabajo.planificar(
            cliente, resolutor, avisar=self._avisar
        )
        self.estado.setdefault("planes", {})[str(trabajo.carpeta)] = plan
        resolutor.guardar(raiz / FLOTA_CACHE_FILENAME)
        datos = self._ejecutar_indexado(
            [trabajo], [plan], cliente,
            completar=bool(self.estado.get("completar")),
        )
        datos["trabajo"] = trabajo
        self.batch_indexado.emit(datos)
        return datos

    def _subir_pendientes(self) -> None:
        """Reanuda cargas locales sin volver a preparar la ejecución actual."""
        from app.airvault.flujo import subir_partes

        estado = self.estado
        cliente = self._conectar()
        subir_partes(
            estado["pendientes_subida"], estado["sesion"],
            avisar=self._avisar, cliente=cliente, dormir=self._dormir,
            al_finalizar_subidas=self._notificar_subidas,
        )
        self.subido.emit({
            "trabajos": estado["trabajos"], "cliente": cliente,
            "sesion": estado.get("sesion"),
        })

    # ── comprobar ──────────────────────────────────────────────────

    def _comprobar(self) -> None:
        """Pregunta a AirVault y planifica lo que ya esté listo.

        Planificar es solo leer: abre el batch, lee sus páginas, calcula qué
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
        planes: Dict[str, tuple] = estado.setdefault("planes", {})

        cliente = self._conectar()
        trabajos = estado["trabajos"]
        estados = comprobar_partes(trabajos, cliente, avisar=self._avisar)

        resolutor = ResolutorFlota.load(raiz / FLOTA_CACHE_FILENAME)
        nuevos = 0
        for parte in estados:
            clave = str(parte.trabajo.carpeta)
            if clave in planes or not parte.se_puede_indexar:
                continue
            self._avisar(
                f"Leyendo el batch {parte.batch_id} para ver qué se "
                f"escribiría", 0, 0,
            )
            planes[clave] = parte.trabajo.planificar(
                cliente, resolutor, avisar=self._avisar
            )
            nuevos += 1
        if nuevos:
            resolutor.guardar(raiz / FLOTA_CACHE_FILENAME)

        # Un solo reporte para toda la ejecución: se aprueba de una vez, no
        # batch por batch.
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
        estado = self.estado
        cliente = estado["cliente"]
        trabajos = list(estado["listos"])
        planes = [estado["planes"][str(t.carpeta)] for t in trabajos]
        self.indexado.emit(self._ejecutar_indexado(
            trabajos, planes, cliente,
            completar=bool(estado.get("completar")),
        ))

    def _ejecutar_indexado(
        self, trabajos, planes, cliente, completar: bool = False,
    ) -> dict:
        """Escribe y verifica uno o varios batches con el mismo reintento."""
        from app.airvault.flujo import (
            cerrar_partes,
            completar_partes,
            indexar_partes,
            planificar_partes,
            verificar_partes,
        )
        from app.airvault.indexer import Resultado

        cierres: list = []
        resultado = Resultado()
        validas = total = 0
        try:
            for intento in range(1, INTENTOS_INDEXADO + 1):
                parcial = indexar_partes(
                    trabajos, planes, avisar=self._avisar
                )
                for atributo in (
                    "escritas", "omitidas", "fallidas",
                    "separadores_borrados", "separadores_pendientes",
                ):
                    setattr(
                        resultado, atributo,
                        getattr(resultado, atributo) + getattr(parcial, atributo),
                    )
                resultado.detalles.extend(parcial.detalles)
                resultado.interrumpido = parcial.interrumpido
                self._avisar(
                    "Comprobando cómo quedaron los batches", 0, 0
                )
                validas, total, _problemas = verificar_partes(
                    trabajos, cliente
                )
                if validas == total or parcial.interrumpido:
                    break
                if intento < INTENTOS_INDEXADO:
                    self._avisar(
                        f"Reintentando páginas amarillas "
                        f"({intento + 1}/{INTENTOS_INDEXADO})", 0, 0,
                    )
                    resolutor = planes[0][1].resolutor if planes else None
                    planes = planificar_partes(
                        trabajos, cliente, resolutor=resolutor,
                        avisar=self._avisar,
                    )
                    for trabajo, plan in zip(trabajos, planes):
                        self.estado.setdefault("planes", {})[
                            str(trabajo.carpeta)
                        ] = plan
            if completar and validas == total:
                cierres = completar_partes(
                    trabajos, cliente, avisar=self._avisar
                )
        finally:
            # Escribir toma el batch y lo suelta al terminar; esto es la red
            # de seguridad para cuando algo se corta por el medio. Un batch
            # que queda tomado no da error: cuelga la próxima vez que
            # alguien lo abra.
            cerrar_partes(trabajos, cliente)
        return {
            "resultado": resultado, "validas": validas, "total": total,
            "lotes": len(trabajos), "cierres": cierres,
            "incompleto": validas != total,
        }

    def _completar(self) -> None:
        """Cierra batches ya verificados sin reescribir sus paginas."""
        from app.airvault.flujo import cerrar_partes, completar_partes
        from app.airvault.indexer import Resultado

        estado = self.estado
        cliente = self._conectar()
        trabajos = list(estado["por_completar"])
        try:
            cierres = completar_partes(
                trabajos, cliente, avisar=self._avisar
            )
        finally:
            cerrar_partes(trabajos, cliente)
        total = sum(len(t.manifiesto.bitacoras()) for t in trabajos)
        self.indexado.emit({
            "resultado": Resultado(), "validas": total, "total": total,
            "lotes": len(trabajos), "cierres": cierres,
            "incompleto": False,
        })


def _soltar(trabajos, cliente) -> None:
    """Suelta los batches y se calla si no puede: es limpieza, no trabajo."""
    from app.airvault.flujo import cerrar_partes

    try:
        cerrar_partes(trabajos, cliente)
    except Exception:  # noqa: BLE001 - soltando no se avisa de nada
        pass


class SoltarLotesWorker(QThread):
    """Suelta los batches fuera del hilo de la ventana.

    Es una petición por batch contra un servidor que puede tardar un minuto
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
        # El que pregunta solo por los batches cada tantos minutos.
        self._vigilante: Optional[QTimer] = None
        # Encadena una comprobacion en cuanto termine lo que esta en vuelo:
        # subir e indexar dejan la lista desactualizada.
        self._comprobar_al_terminar = False
        self._indexar_al_terminar = False
        self._indexado_incompleto = False
        # Cerrar con trabajo en vuelo no bloquea: se pide la cancelación y
        # la ventana se va en cuanto el hilo suelta lo que tenía tomado.
        self._cerrar_al_terminar = False
        # Hilos que están soltando batches en AirVault, para que Qt no los
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
        cuerpo.addWidget(self._titulo("Batches en AirVault"))
        cuerpo.addWidget(self._lotes(), 1)
        cuerpo.addLayout(self._fila_vigilancia())
        cuerpo.addWidget(self._menu_automatizacion())
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
        self._ajustar_tabla(tabla)
        self.historial = tabla
        return tabla

    def _campos(self) -> QGridLayout:
        """Los datos de la carga, en rejilla para que se alineen.

        En filas sueltas cada etiqueta medía lo suyo y los controles
        empezaban en sitios distintos.
        """
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        etiquetas = (
            "Ejecución:", "Batch:", "Máximo por batch:", "Sesión:"
        )
        for fila, etiqueta in enumerate(etiquetas):
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

        self.boton_eliminar_registro = QPushButton("Eliminar registro")
        self.boton_eliminar_registro.setEnabled(False)
        self.boton_eliminar_registro.setToolTip(
            "Envía a la Papelera el estado local de AirVault de esta "
            "ejecución para empezar nuevamente. No borra el CSV, los PDF "
            "exportados ni los batches que ya existan en AirVault."
        )
        self.boton_eliminar_registro.clicked.connect(
            self._eliminar_registro
        )
        grid.addWidget(self.boton_eliminar_registro, 0, 3)

        self.lote_edit = QLineEdit()
        self.lote_edit.setPlaceholderText("Nombre del batch en AirVault")
        self.lote_edit.setToolTip(
            "Nombre con el que el batch queda en AirVault. Lleva la fecha y "
            "la hora de la ejecución para que no se confunda con otro: en la "
            "cola conviven batches con nombres repetidos."
        )
        grid.addWidget(self.lote_edit, 1, 1, 1, 2)

        self.limite_batch_spin = QSpinBox()
        self.limite_batch_spin.setRange(10, 5000)
        self.limite_batch_spin.setSingleStep(50)
        self.limite_batch_spin.setValue(PAGINAS_POR_BATCH_POR_DEFECTO)
        self.limite_batch_spin.setSuffix(" pág.")
        self.limite_batch_spin.setFixedHeight(
            self.lote_edit.sizeHint().height()
        )
        self.limite_batch_spin.setToolTip(
            "Cantidad máxima de páginas que se envía en cada batch de "
            "Quick Upload, contando los separadores. Los PDF más grandes "
            "se reparten automáticamente sin modificar la entrega original."
        )
        grid.addWidget(self.limite_batch_spin, 2, 1)

        self.compresion_check = QCheckBox("Compresión")
        self.compresion_check.setToolTip(
            "Reduce los PDF que se envían a AirVault a 200 DPI con calidad "
            "moderada. No modifica los PDF exportados de la ejecución."
        )
        grid.addWidget(self.compresion_check, 2, 2, 1, 2)

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
        grid.addWidget(self.cookie_edit, 3, 1, 1, 2)
        return grid

    def _lotes(self) -> QTableWidget:
        """En qué va cada batch de esta ejecución dentro de AirVault.

        Una entrega puede ser varios batches —las partes, y el de REVISAR—, y
        no llegan a estar listos a la vez: AirVault los procesa en su cola.
        Aquí se ve cuál ya se puede indexar y cuál sigue esperando, en vez
        de una sola línea de estado que solo puede decir una cosa.
        """
        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(
            ["ID", "Batch", "Páginas", "Estado"]
        )
        tabla.setToolTip(
            "Batches de la ejecución elegida y pendientes recuperados de "
            "ejecuciones anteriores. Van pasando a «Listo para indexar» "
            "según el servidor termina de procesarlos."
        )
        tabla.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tabla.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabla.setAlternatingRowColors(True)
        tabla.verticalHeader().setVisible(False)
        cabecera = tabla.horizontalHeader()
        cabecera.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        cabecera.setSectionResizeMode(
            1, QHeaderView.ResizeMode.Interactive
        )
        cabecera.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        cabecera.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        tabla.setColumnWidth(1, ANCHO_MINIMO_NOMBRE_BATCH)
        self._ajustar_tabla(tabla)
        self.lotes = tabla
        return tabla

    @staticmethod
    def _ajustar_tabla(tabla: QTableWidget) -> None:
        """Iguala las dos tablas y deja la barra debajo de la cabecera."""
        style_data_table(tabla)
        tabla.setMinimumHeight(132)
        tabla.setMaximumHeight(176)
        align_vertical_scrollbar_to_header(tabla)

    def _fila_vigilancia(self) -> QHBoxLayout:
        """Cada cuánto se pregunta automáticamente a AirVault."""
        fila = QHBoxLayout()
        self.auto_check = QCheckBox("Comprobar cada")
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip(
            "Le pregunta a AirVault cada tantos minutos si ya terminó de "
            "procesar lo subido, y deja de preguntar cuando no queda nada "
            "por esperar. Se puede apagar y usar «Revisar en AirVault»."
        )
        self.auto_check.toggled.connect(self._ajustar_vigilancia)
        self.auto_check.toggled.connect(self._sincronizar_espera_visible)
        fila.addWidget(self.auto_check)

        self.minutos_spin = QSpinBox()
        self.minutos_spin.setRange(1, 60)
        self.minutos_spin.setValue(MINUTOS_POR_DEFECTO)
        self.minutos_spin.setSuffix(" min")
        self.minutos_spin.setToolTip(
            "Un batch tarda minutos, a veces mucho más, en salir de la cola "
            "de AirVault. Preguntar más seguido no lo apura."
        )
        self.minutos_spin.valueChanged.connect(self._ajustar_vigilancia)
        fila.addWidget(self.minutos_spin)

        self.boton_automatizacion = QPushButton("Automatización…")
        self.boton_automatizacion.setCheckable(True)
        self.boton_automatizacion.setToolTip(
            "Muestra los pasos que se pueden encadenar automáticamente y "
            "las acciones para continuar o reiniciar un proceso incompleto."
        )
        fila.addWidget(self.boton_automatizacion)
        fila.addStretch()
        return fila

    def _menu_automatizacion(self) -> QGroupBox:
        """Opciones avanzadas, escondidas hasta que se pidan."""
        panel = QGroupBox("Hasta dónde continuar automáticamente")
        contenido = QVBoxLayout(panel)

        self.auto_subir_check = QCheckBox("Subir todos los batches")
        self.auto_subir_check.setChecked(True)
        self.auto_subir_check.setEnabled(False)
        self.auto_esperar_check = QCheckBox(
            "Esperar hasta que AirVault los deje listos"
        )
        self.auto_esperar_check.setChecked(True)
        self.auto_indexar_check = QCheckBox("Indexar páginas")
        self.auto_indexar_check.setChecked(True)
        self.auto_completar_check = QCheckBox("Completar batches")
        for control in (
            self.auto_esperar_check,
            self.auto_indexar_check,
            self.auto_completar_check,
        ):
            control.toggled.connect(self._ajustar_pasos_automaticos)
        for control in (
            self.auto_subir_check, self.auto_esperar_check,
            self.auto_indexar_check, self.auto_completar_check,
        ):
            contenido.addWidget(control)

        acciones = QHBoxLayout()
        self.boton_continuar = QPushButton("Continuar pendiente")
        self.boton_continuar.setToolTip(
            "Consulta AirVault y continúa desde el primer paso que no haya "
            "terminado, sin repetir páginas que ya estén en verde."
        )
        self.boton_continuar.clicked.connect(self._continuar_pendiente)
        acciones.addWidget(self.boton_continuar)
        self.boton_reiniciar = QPushButton("Reiniciar paso incompleto")
        self.boton_reiniciar.setToolTip(
            "Reinicia el estado local del batch seleccionado; si no hay una "
            "fila seleccionada, reinicia todos los incompletos. No borra "
            "nada en AirVault."
        )
        self.boton_reiniciar.clicked.connect(self._reiniciar_incompleto)
        acciones.addWidget(self.boton_reiniciar)
        acciones.addStretch()
        contenido.addLayout(acciones)

        panel.setVisible(False)
        self.boton_automatizacion.toggled.connect(panel.setVisible)
        self.menu_automatizacion = panel
        self._ajustar_pasos_automaticos()
        return panel

    def _ajustar_pasos_automaticos(self) -> None:
        """Mantiene la cadena secuencial y apaga dependencias imposibles."""
        espera = self.auto_esperar_check.isChecked()
        self.auto_indexar_check.setEnabled(espera)
        if not espera:
            self.auto_indexar_check.setChecked(False)
        indexa = espera and self.auto_indexar_check.isChecked()
        self.auto_completar_check.setEnabled(indexa)
        if not indexa:
            self.auto_completar_check.setChecked(False)
        self.auto_check.setChecked(espera)
        if self.auto_completar_check.isChecked():
            self.completar_check.setChecked(True)

    def _sincronizar_espera_visible(self, marcada: bool) -> None:
        """La opción visible y el paso oculto representan la misma espera."""
        if hasattr(self, "auto_esperar_check"):
            self.auto_esperar_check.setChecked(marcada)

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

        Un batch tarda lo suyo y pasa por etapas muy distintas —subir,
        esperar a que AirVault lo procese, leer el batch, escribir—. Con una
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
            "Al terminar de escribir, da el batch por terminado en AirVault y "
            "lo saca de la cola del Web Index. AirVault solo lo acepta con "
            "todas las páginas en verde: si a alguna le falta un campo "
            "obligatorio, el batch se queda en la cola y se dice cuáles son. "
            "Sin marcar, el batch queda ahí para revisarlo."
        )
        fila.addWidget(self.completar_check)
        fila.addStretch()

        self.boton_subir = QPushButton("Subir a AirVault")
        self.boton_subir.setObjectName("primaryButton")
        self.boton_subir.setEnabled(False)
        self.boton_subir.setToolTip(
            "Busca en AirVault todos los batches de la entrega, incluidos "
            "el principal sin número, sus divisiones y REVISAR. Los reconoce "
            "por el nombre esperado, actualiza su ID y sube solamente los "
            "que falten. Identifica cada uno antes de subir el siguiente; "
            "con la automatización activa, indexa en paralelo los que ya "
            "estén enteros."
        )
        self.boton_subir.clicked.connect(self._subir)

        self.boton_revisar = QPushButton("Revisar en AirVault")
        self.boton_revisar.setEnabled(False)
        self.boton_revisar.setToolTip(
            "Busca por título cada batch de la lista en AirVault. Los que "
            "ya estén listos quedan en blanco y se habilitan para indexar; "
            "los que ya se hayan indexado quedan en verde. No escribe nada."
        )
        self.boton_revisar.clicked.connect(self._comprobar)
        # Conserva el nombre interno que usa la comprobación automática y
        # el código que habilita los controles mientras trabaja el hilo.
        self.boton_comprobar = self.boton_revisar

        self.boton_indexar = QPushButton("Indexar")
        self.boton_indexar.setEnabled(False)
        self.boton_indexar.setToolTip(
            "Escribe en AirVault los datos de los batches que ya están listos. "
            "También borra las páginas separadoras del batch automático. "
            "Las páginas marcadas como bloqueadas no se escriben."
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
        # él, una sesión que no llega o un batch que AirVault no suelta
        # dejaban la ventana sin nada que pulsar durante minutos.
        self.boton_cancelar = QPushButton("Cancelar")
        self.boton_cancelar.setEnabled(False)
        self.boton_cancelar.setToolTip(
            "Detiene lo que esté en marcha y desbloquea los batches que la "
            "aplicación haya abierto en AirVault. Lo ya escrito se conserva "
            "y al volver a comprobar se retoma sin repetirlo."
        )
        self.boton_cancelar.clicked.connect(self._cancelar)

        self.boton_cerrar = QPushButton("Cerrar")
        self.boton_cerrar.clicked.connect(self.close)

        for boton in (
            self.boton_reporte, self.boton_subir, self.boton_revisar,
            self.boton_indexar, self.boton_cancelar, self.boton_cerrar,
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
        """Apunta la ventana a una ejecución y propone el nombre del batch."""
        from app.airvault.flujo import carpeta_de_corrida, carpeta_de_trabajo
        from app.airvault.naming import nombre_desde_corrida

        # Cambiar de ejecución tira lo hecho, y con ello los batches que
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
        limites = {
            t.manifiesto.paginas_por_batch for t in self._trabajos
            if t.manifiesto.paginas_por_batch > 0
        }
        if len(limites) == 1:
            self.limite_batch_spin.setValue(limites.pop())
        compresiones = {t.manifiesto.compresion for t in self._trabajos}
        if len(compresiones) == 1:
            self.compresion_check.setChecked(compresiones.pop())
        self.limite_batch_spin.setEnabled(not bool(self._trabajos))
        self.compresion_check.setEnabled(not bool(self._trabajos))
        # La conexion sobrevive al cambio de ejecucion: es el mismo
        # servidor, y volver a abrirla es volver a arrancar el navegador.
        self._estado = {
            clave: self._estado[clave]
            for clave in ("cliente", "sesion") if clave in self._estado
        }
        self._estados = [estado_local(t) for t in self._trabajos]
        self._pintar_lotes()
        self.boton_indexar.setEnabled(bool(self.corrida_edit.text().strip()))
        self.boton_revisar.setEnabled(bool(self._trabajos))
        self.boton_eliminar_registro.setEnabled(
            bool(self._rutas_del_registro())
        )
        self.boton_reiniciar.setEnabled(bool(self._trabajos))
        self._ajustar_vigilancia()

    def _carpeta_del_registro(self) -> Optional[Path]:
        """Carpeta local exacta de la ejecución elegida, si es segura."""
        from app.airvault.flujo import (
            CARPETA_TRABAJOS,
            carpeta_de_corrida,
            carpeta_de_trabajo,
        )

        csv = self.corrida_edit.text().strip()
        if not csv:
            return None
        raiz_trabajos = (self._raiz / CARPETA_TRABAJOS).resolve()
        carpeta = (
            self._raiz
            / carpeta_de_trabajo(carpeta_de_corrida(csv).name)
        ).resolve()
        if (
            carpeta == raiz_trabajos
            or not carpeta.is_relative_to(raiz_trabajos)
        ):
            return None
        return carpeta

    def _rutas_del_registro(self) -> list[Path]:
        """Manifiestos locales de la ejecución elegida, nunca de otra."""
        from app.airvault.manifest import MANIFIESTO_FILENAME

        carpeta = self._carpeta_del_registro()
        if carpeta is None:
            return []
        return sorted(
            ruta for ruta in carpeta.rglob(MANIFIESTO_FILENAME)
            if ruta.is_file() and ruta.resolve().is_relative_to(carpeta)
        )

    def _eliminar_registro(self) -> None:
        """Reinicia la ejecución borrando solo su memoria local de AirVault."""
        rutas = self._rutas_del_registro()
        if not rutas:
            QMessageBox.information(
                self,
                "Eliminar registro",
                "Esta ejecución no tiene un registro local de AirVault.",
            )
            return
        cuenta = f"{len(rutas)} batch" + (
            "es" if len(rutas) != 1 else ""
        )
        respuesta = QMessageBox.warning(
            self,
            "Eliminar registro de AirVault",
            "Se enviará a la Papelera el registro local de esta ejecución "
            f"({cuenta}).\n\n"
            "No se borrarán el CSV, los PDF ni los batches existentes en "
            "AirVault. Al volver a subir, se reconstruirá el proceso y se "
            "buscarán otra vez por título.\n\n¿Desea continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if respuesta != QMessageBox.StandardButton.Yes:
            return

        carpeta = self._carpeta_del_registro()
        if carpeta is None:
            return
        movidos, fallidos = send_to_trash(rutas)
        if movidos:
            self._parar_vigilancia()
            self._indexado_incompleto = False
            self.boton_reporte.setEnabled(False)
            csv = Path(self.corrida_edit.text())
            self._cargar_trabajos(carpeta, csv)
            self.estado_label.setText("Registro local eliminado")
            self.resumen.setText(
                "Se eliminó el registro local de AirVault. La ejecución "
                "puede iniciarse nuevamente; los batches remotos no se "
                "modificaron."
            )
            self._anotar("Registro local de AirVault eliminado")

        if fallidos:
            detalle = "\n".join(
                f"- {ruta.name}: {error}" for ruta, error in fallidos
            )
            QMessageBox.warning(
                self,
                "Registro eliminado parcialmente",
                f"Se eliminaron {len(movidos)} de {len(rutas)} registros.\n\n"
                "No se pudieron eliminar:\n" + detalle,
            )

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

    # ── la lista de batches ──────────────────────────────────────────

    def _pintar_lotes(self) -> None:
        """Vuelca en la tabla en qué va cada batch."""
        from app.airvault.flujo import COMPLETADO, INDEXADO, SIN_SUBIR
        from app.airvault.model import EstadoEtapa, EstadoRegistro

        tabla = self.lotes
        tabla.setRowCount(0)
        for parte in self._estados:
            fila = tabla.rowCount()
            tabla.insertRow(fila)
            nombre = parte.nombre or "(sin nombre)"
            esperadas = len(parte.trabajo.manifiesto.registros)
            celdas = (
                parte.batch_id, nombre, str(esperadas), str(parte),
            )
            etapa_indexar = getattr(
                parte.trabajo.manifiesto, "etapas", {}
            ).get("indexar")
            ya_indexado = parte.estado in (INDEXADO, COMPLETADO) or bool(
                etapa_indexar
                and etapa_indexar.estado in (
                    EstadoEtapa.HECHA, EstadoEtapa.ERROR,
                )
            ) or any(
                getattr(registro, "estado", None) is EstadoRegistro.ESCRITA
                for registro in parte.trabajo.manifiesto.registros
            )
            for columna, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if columna == 1:
                    item.setToolTip(nombre)
                if columna == 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if ya_indexado:
                    item.setForeground(QColor(COLOR_INDEXADO))
                elif parte.estado == SIN_SUBIR:
                    # Gris significa una sola cosa: el archivo aun no fue
                    # aceptado por Quick Upload. Desde que se sube queda en
                    # blanco, incluso mientras AirVault lo procesa.
                    item.setForeground(Qt.GlobalColor.gray)
                tabla.setItem(fila, columna, item)
        ancho_nombre = min(
            max(
                tabla.sizeHintForColumn(1) + 16,
                ANCHO_MINIMO_NOMBRE_BATCH,
            ),
            ANCHO_MAXIMO_NOMBRE_BATCH,
        )
        tabla.setColumnWidth(1, ancho_nombre)

    def _listos(self) -> list:
        """Partes que ya se pueden escribir y tienen su plan calculado."""
        planes = self._estado.get("planes") or {}
        return [
            parte.trabajo for parte in self._estados
            if parte.se_puede_indexar and str(parte.trabajo.carpeta) in planes
        ]

    def _por_completar(self) -> list:
        """Batches verificados que pueden cerrarse sin volver a escribir."""
        from app.airvault.flujo import INDEXADO

        return [
            parte.trabajo for parte in self._estados
            if parte.estado == INDEXADO and not parte.trabajo.manifiesto.solo_subir
        ]

    def _falta_esperar(self) -> bool:
        """Si queda algún batch que AirVault todavía no ha terminado."""
        from app.airvault.flujo import LISTO

        return (
            self._indexado_incompleto
            and self.auto_indexar_check.isChecked()
        ) or any(
            not parte.se_acabo and parte.estado != LISTO
            for parte in self._estados
        )

    # ── la comprobación periódica ──────────────────────────────────

    def _ajustar_vigilancia(self) -> None:
        """Arranca o para la comprobación automática según haga falta.

        Se pregunta mientras quede algo que esperar. Cuando todos los batches
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
            self.resumen.setText("Falta el nombre del batch en AirVault.")
            return None
        job = carpeta_de_corrida(csv).name
        self._estado.update({
            "config": self._config_actual(),
            "csv": csv,
            "raiz": self._raiz,
            "carpeta_job": self._raiz / carpeta_de_trabajo(job),
            "nombre_lote": self.lote_edit.text().strip(),
            "cookie": self.cookie_edit.text(),
            "paginas_por_batch": self.limite_batch_spin.value(),
            "compresion": self.compresion_check.isChecked(),
            "indexar_al_encontrar": self.auto_indexar_check.isChecked(),
            "completar": self.completar_check.isChecked(),
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
        self._lanzar("comprobar", estado)

    def _indexar(self) -> None:
        # Una ejecucion parcial no puede adelantarse: aunque ya haya un batch
        # listo, primero se terminan todas las cargas. Este es tambien el
        # camino de recuperacion para trabajos que quedaron a medias.
        if any(
            not trabajo.manifiesto.etapa_hecha("subir")
            for trabajo in self._trabajos
        ):
            self._continuar_pendiente()
            return
        listos = self._listos()
        if not listos:
            por_completar = self._por_completar()
            if self.completar_check.isChecked() and por_completar:
                self._estado["por_completar"] = por_completar
                self._lanzar("completar", self._estado)
                return
            if self.corrida_edit.text().strip():
                # Conecta y detecta tambien batches que esta aplicacion subio
                # en ejecuciones anteriores. Despues decide si hay que
                # reindexar, continuar o solamente comprobar su estado.
                self._indexar_al_terminar = True
                self._comprobar()
            return
        self._estado["listos"] = listos
        self._estado["completar"] = self.completar_check.isChecked()
        self._lanzar("indexar", self._estado)

    def _continuar_pendiente(self) -> None:
        """Retoma el primer paso necesario sin duplicar trabajo terminado."""
        from app.airvault.model import EstadoEtapa

        estado = self._base_del_estado()
        if estado is None:
            return
        if not self._trabajos:
            self._indexar_al_terminar = self.auto_indexar_check.isChecked()
            self._comprobar()
            return
        pendientes_subida = [
            trabajo for trabajo in self._trabajos
            if not trabajo.manifiesto.etapa_hecha("subir")
        ]
        if pendientes_subida:
            # EN_CURSO puede significar que AirVault acepto el archivo y la
            # respuesta se perdio. Primero se consulta; solo «Reiniciar»
            # autoriza una nueva carga cuando esa duda existe.
            if any(
                trabajo.manifiesto.etapas.get("subir") is not None
                and trabajo.manifiesto.etapa("subir").estado
                is EstadoEtapa.EN_CURSO
                for trabajo in pendientes_subida
            ):
                self._indexar_al_terminar = True
                self._comprobar()
                return
            estado["pendientes_subida"] = pendientes_subida
            self._lanzar("subir_pendientes", estado)
            return
        if self._listos():
            self._indexar()
            return
        self._indexar_al_terminar = True
        self._comprobar()

    def _reiniciar_incompleto(self) -> None:
        """Reabre el paso local incompleto del batch elegido o de todos."""
        from app.airvault.flujo import (
            estado_local,
            reiniciar_trabajos_incompletos,
        )

        filas = self.lotes.selectionModel().selectedRows()
        objetivos = (
            [self._estados[filas[0].row()].trabajo]
            if filas and filas[0].row() < len(self._estados)
            else list(self._trabajos)
        )
        reiniciados = reiniciar_trabajos_incompletos(objetivos)
        if not reiniciados:
            self.resumen.setText("No hay ningún paso incompleto que reiniciar.")
            return
        claves = {str(trabajo.carpeta) for trabajo, _paso in reiniciados}
        planes = self._estado.get("planes") or {}
        self._estado["planes"] = {
            clave: plan for clave, plan in planes.items() if clave not in claves
        }
        self._estados = [estado_local(t) for t in self._trabajos]
        self._pintar_lotes()
        pasos = ", ".join(sorted({paso for _trabajo, paso in reiniciados}))
        self.resumen.setText(
            f"Se reinició el paso incompleto ({pasos}) en "
            f"{len(reiniciados)} batch"
            + ("es." if len(reiniciados) != 1 else ".")
        )
        self.boton_indexar.setEnabled(True)

    def _lanzar(self, modo: str, estado: dict) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._habilitar(False)
        worker = TrabajoAirVaultWorker(modo, estado, self)
        worker.paso.connect(self._mostrar_paso)
        worker.subidas_actualizadas.connect(self._al_actualizar_subidas)
        worker.batch_encontrado.connect(self._al_batch_encontrado)
        worker.batch_indexado.connect(self._al_batch_indexado)
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
        self.boton_eliminar_registro.setEnabled(
            activo and bool(self._rutas_del_registro())
        )
        self.lote_edit.setEnabled(activo)
        self.cookie_edit.setEnabled(activo)
        self.limite_batch_spin.setEnabled(activo and not self._trabajos)
        self.compresion_check.setEnabled(activo and not self._trabajos)
        self.boton_comprobar.setEnabled(activo and bool(self._trabajos))
        self.boton_automatizacion.setEnabled(activo)
        self.boton_continuar.setEnabled(activo)
        self.boton_reiniciar.setEnabled(activo and bool(self._trabajos))
        # Cerrar y Cancelar nunca se apagan a la vez: mientras hay trabajo
        # en vuelo tiene que haber siempre algo que pulsar, o la ventana se
        # queda muda durante una espera de minutos.
        self.boton_cancelar.setEnabled(not activo)
        self.boton_indexar.setEnabled(
            activo and (
                bool(self._listos()) or bool(self._trabajos)
                or (
                    self.completar_check.isChecked()
                    and bool(self._por_completar())
                )
                or bool(self.corrida_edit.text().strip())
            )
        )

    # ── el reloj del paso ──────────────────────────────────────────

    def _arrancar_reloj(self) -> None:
        """Deja a la vista que el trabajo sigue vivo mientras espera.

        La barra sola no basta: en las etapas sin cuenta —entrar a
        AirVault, esperar a que el batch salga de la cola— no se mueve, y
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
        self._al_actualizar_subidas(datos)
        self.limite_batch_spin.setEnabled(False)
        self.compresion_check.setEnabled(False)
        cuantos = len(self._trabajos)
        lotes = "el batch" if cuantos == 1 else f"los {cuantos} batches"
        self.resumen.setText(
            f"Subida terminada. AirVault tiene que procesar {lotes} antes "
            f"de poder indexarlos, y eso tarda: se va preguntando solo y "
            f"la lista de arriba dice en qué van."
        )
        self.estado_label.setText("Subida terminada")
        self._anotar(
            "Subida terminada; se confirma cada batch en AirVault"
        )
        # «Subir» no confia en la marca local: en cada clic consulta la cola
        # remota, recupera el ID que falte y solo entonces deja indexar. La
        # opcion de espera automatica decide si se seguira preguntando cuando
        # AirVault aun lo procese, pero nunca elimina esta primera comprobacion.
        self._comprobar_al_terminar = True

    def _al_actualizar_subidas(self, datos: dict) -> None:
        """Refleja en la tabla las cargas antes de buscar sus IDs."""
        from app.airvault.flujo import estado_local

        self._trabajos = list(datos["trabajos"])
        self._estado["trabajos"] = self._trabajos
        self._estados = [estado_local(t) for t in self._trabajos]
        self._pintar_lotes()

    def _al_batch_encontrado(self, datos: dict) -> None:
        """Muestra el ID apenas se resuelve, sin esperar las otras búsquedas."""
        from app.airvault.flujo import estado_local

        self._trabajos = list(datos["trabajos"])
        self._estado["trabajos"] = self._trabajos
        remoto = datos["estado"]
        self._estados = [estado_local(t) for t in self._trabajos]
        clave = str(remoto.trabajo.carpeta)
        for indice, parte in enumerate(self._estados):
            if str(parte.trabajo.carpeta) == clave:
                self._estados[indice] = remoto
                break
        self._pintar_lotes()
        self._anotar(
            f"Batch {remoto.batch_id} asignado a «{remoto.nombre}»"
        )

    def _al_batch_indexado(self, datos: dict) -> None:
        """Pinta en verde un batch terminado por el carril paralelo."""
        from app.airvault.flujo import estado_local

        trabajo = datos["trabajo"]
        self._estados = [estado_local(t) for t in self._trabajos]
        self._pintar_lotes()
        resultado = datos["resultado"]
        self._anotar(
            f"Batch {trabajo.manifiesto.batch_id} indexado: "
            f"{resultado.escritas} escritas, {resultado.fallidas} fallidas"
        )
        self.resumen.setText(
            f"El batch {trabajo.manifiesto.nombre_batch} ya se indexó "
            f"({datos['validas']} de {datos['total']} páginas válidas). "
            "La búsqueda y subida de los demás continúa en paralelo."
        )

    def _al_comprobar(self, datos: dict) -> None:
        from app.airvault.flujo import LISTO

        self._estado["planes"] = datos["planes"]
        self._trabajos = list(self._estado.get("trabajos") or self._trabajos)
        self._estados = datos["estados"]
        self._indexado_incompleto = False
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
        if self.auto_indexar_check.isChecked() and (
            self._listos()
            or (
                self.auto_completar_check.isChecked()
                and self._por_completar()
            )
        ):
            self._indexar_al_terminar = True

    def _resumen_de_listos(self, partes) -> str:
        """Cuántas páginas se escribirían y cuántas quedan bloqueadas."""
        from app.airvault.report import _resumen_sumado

        if not partes:
            return "Hay batches listos; falta calcular qué se les escribiría."
        resumen = _resumen_sumado(partes)
        donde = (
            f"{len(partes)} batches" if len(partes) > 1
            else f"Batch {partes[0][0] or partes[0][1].batch_id}"
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
        donde = f" en {lotes} batches" if lotes > 1 else ""
        cuenta = (
            f"Escritas {resultado.escritas}, omitidas {resultado.omitidas} "
            f"y fallidas {resultado.fallidas}. En AirVault quedaron "
            f"{datos['validas']} de {datos['total']} páginas válidas{donde}."
        )
        separadores_borrados = getattr(resultado, "separadores_borrados", 0)
        separadores_pendientes = getattr(
            resultado, "separadores_pendientes", 0
        )
        if separadores_borrados:
            cuenta += (
                f" Se borraron {separadores_borrados} páginas "
                f"separadoras del indexado automático."
            )
        if separadores_pendientes:
            cuenta += (
                f" No se pudieron borrar "
                f"{separadores_pendientes} páginas separadoras "
                f"en AirVault."
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
        if datos.get("incompleto"):
            self._indexado_incompleto = True
            self.resumen.setText(
                cuenta
                + " Aún hay páginas amarillas. Se reintentaron en esta "
                "ejecución y el proceso queda disponible para continuar "
                "sin repetir las páginas verdes."
            )
            self.estado_label.setText("Indexado incompleto")
            self._limpiar_progreso()
            self._ajustar_vigilancia()
            return
        self._indexado_incompleto = False
        self.resumen.setText(cuenta + self._cuenta_de_cierres(datos))
        self.estado_label.setText("Indexado terminado")
        self._limpiar_progreso()
        # Vuelve a preguntar para que la lista quede diciendo cómo acabó
        # cada batch, en vez de con lo que se sabía antes de escribir.
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
                f" Se completaron {len(cerrados)} batches en AirVault y "
                "salieron de la cola de Web Index."
                if len(cerrados) > 1 else
                " El batch se completó en AirVault y salió de la cola de "
                "Web Index."
            )
            quitadas = sum(len(r.quitadas) for _t, r in cierres if r.completado)
            if quitadas:
                # Se dice porque es un cambio en el batch: esas páginas ya no
                # están, y quien lo abra en AirVault no las va a encontrar.
                texto += (
                    f" Se quitaron {quitadas} páginas separadoras, que no son "
                    f"bitácoras y no dejan completar el batch."
                )
            partes.append(texto)
        for trabajo, resultado in colgados:
            partes.append(
                f" El batch {trabajo.manifiesto.nombre_batch} no se pudo "
                f"completar: {resultado.detalle}"
            )
        return "".join(partes)

    def _al_fallar(self, mensaje: str) -> None:
        preparados = self._estado.get("trabajos") or []
        if preparados and not self._trabajos:
            # Preparar los PDF ocurre antes de conectar. Si la sesion o la
            # subida falla despues, se conserva ese reparto para reanudarlo
            # con el mismo limite y no mezclar manifiestos de dos repartos.
            from app.airvault.flujo import estado_local

            self._trabajos = list(preparados)
            self._estados = [estado_local(t) for t in self._trabajos]
            self._pintar_lotes()
            self.limite_batch_spin.setEnabled(False)
            self.compresion_check.setEnabled(False)
        self.resumen.setText(mensaje)
        self.estado_label.setText("El indexado no pudo continuar")
        self._anotar(f"Se detuvo: {mensaje}")
        # Un fallo con la comprobación automática puesta se repetiría en el
        # siguiente intervalo con el mismo error: se para y se vuelve a pulsar.
        self._parar_vigilancia()
        self._limpiar_progreso()

    def _al_cancelar(self) -> None:
        """Lo paró quien lo lanzó: se dice y se sueltan los batches."""
        self.estado_label.setText("Cancelado")
        self._anotar(
            "Cancelado; se desbloquean los batches abiertos en AirVault"
        )
        self._parar_vigilancia()
        self._soltar_lotes()
        self.resumen.setText(
            "El trabajo se canceló. Los batches quedaron desbloqueados en "
            "AirVault y lo que ya se hubiera escrito se conserva: al volver a "
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
            return
        if getattr(self, "_indexar_al_terminar", False):
            self._indexar_al_terminar = False
            if self._listos() or (
                self.completar_check.isChecked() and self._por_completar()
            ):
                self._indexar()

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
        puede estar a medio escribir un batch.
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
        se va sola en cuanto el hilo suelta los batches que tuviera tomados,
        que es lo que no se puede dejar a medias.

        Lo comprobado sí sobrevive a un cierre sin trabajo en vuelo: los
        manifiestos guardan en qué quedó cada batch y al reabrir se retoma.

        Cerrarla **no** apaga la comprobación automática. Esperar a que
        AirVault procese un batch puede llevar horas, y lo normal es cerrar
        esta ventana y seguir procesando en la principal; al volver, la
        lista ya está al día. El que sí la apaga es el cierre del programa.
        """
        if self.hilo() is not None:
            self._cerrar_al_terminar = True
            self._cancelar()
            self.resumen.setText(
                "Cancelando el trabajo en marcha. La ventana se cierra en "
                "cuanto AirVault quite el bloqueo de edición de los batches "
                "que había abierto."
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
        # Aquí sí se espera: el programa se está cerrando y un batch que
        # queda tomado deja colgada la próxima apertura.
        self._soltar_lotes(esperar=True)
        # Y a los que ya estuvieran soltando en su propio hilo: destruirlos
        # a media petición deja el batch tomado, que es lo que se venía a
        # evitar.
        for hilo in list(self._soltando):
            hilo.wait(5000)

    def _soltar_lotes(self, esperar: bool = False) -> None:
        """Suelta en AirVault los batches que hubieran quedado tomados.

        Con el recorrido normal no queda ninguno: leer el batch lo suelta en
        cuanto termina y escribirlo también. Esto es para lo que se corta
        por el medio —un cierre, una cancelación, un fallo de red—, porque
        un batch tomado no da error: deja colgada la próxima vez que alguien
        lo abra.

        Va en un hilo aparte salvo al cerrar el programa. Soltar es una
        petición por batch contra un servidor que puede tardar un minuto en
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
