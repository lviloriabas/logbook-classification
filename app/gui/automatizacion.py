"""Hasta dónde llega el botón «Automático» de la ventana principal.

El proceso automático es la cadena entera puesta en un solo botón: leer los
PDF, escribir las salidas y, si se pide, subir la entrega a AirVault y
escribirla allí sin volver a pulsar nada. Qué tramo de esa cadena se recorre
se elige aquí, y la elección sobrevive al cierre del programa porque vive en
el JSON portable, junto al programa y no en el registro de Windows.

Las opciones estaban en la ventana de AirVault, donde solo se veían después
de haber procesado y solo decidían el tramo final. Puestas en la ventana
principal deciden la cadena completa, que es lo que el botón ejecuta.

La lista es un menú que se abre encima de todo, como el del botón derecho,
y no un panel que se despliega dentro de la ventana. Empotrado le quitaba
alto a lo que se está mirando y en una pantalla baja los pasos de abajo
quedaban fuera del borde; un menú se dibuja sobre la ventana, no dentro de
ella, así que cabe entero sin robarle sitio a nada. Se queda abierto entre
clic y clic: son varias casillas y cerrarlo en la primera obligaba a
volver a abrirlo por cada paso.

Tres pasos no se eligen: preprocesar, procesar y exportar. Sin enderezar y
alinear las páginas el OCR no lee, sin OCR no hay datos y sin salidas no hay
nada que subir, así que aparecen marcados y apagados, para que la lista diga
todo lo que va a pasar y no solo la parte opcional.

Los cuatro de AirVault van uno detrás de otro y se marcan juntos: marcar
«Indexar páginas» enciende subir y esperar, porque no se puede indexar lo
que no está arriba; apagar «Subir a AirVault» apaga los tres de abajo, que
sin la carga no tienen sobre qué trabajar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSizePolicy,
    QWidget,
)

from app.airvault.config import (
    AIRVAULT_FILENAME,
    AirVaultConfig,
    guardar_preferencias,
)

# Pasos que la persona elige, en el orden en que ocurren. El primero va
# suelto; los cuatro de AirVault forman la cadena.
DEPURAR = "depurar"
SUBIR = "subir"
ESPERAR = "esperar"
INDEXAR = "indexar"
COMPLETAR = "completar"

CADENA = (SUBIR, ESPERAR, INDEXAR, COMPLETAR)
PASOS = (DEPURAR, *CADENA)

# Los tres que siempre se hacen. No son opciones (no se pueden desmarcar),
# pero sí son pasos, y la cadena que se enseña mientras corre tiene que
# contarlos: sin ellos, «va por 2 de 5» no diría por dónde va de verdad.
#
# «Preprocesar» es la primera parte del procesamiento, no un botón aparte:
# el pipeline recorre el batch entero enderezando y alineando cada página
# (la calibración) antes de leer ninguna. Es un tramo largo —en un libro de
# 50 páginas son unos diez segundos— y hasta ahora la línea de pasos lo
# contaba como si ya estuviera procesando, así que el primer paso parecía
# atascado. Como paso propio se ve dónde está de verdad.
PREPROCESAR = "preprocesar"
PROCESAR = "procesar"
EXPORTAR = "exportar"

# La cadena entera, en el orden en que ocurre. Es el orden del recorrido,
# no el del menú: depurar va entre procesar y exportar porque quita páginas
# de la ejecución antes de que se escriba la entrega.
RECORRIDO = (
    PREPROCESAR, PROCESAR, DEPURAR, EXPORTAR, SUBIR, ESPERAR, INDEXAR,
    COMPLETAR,
)

# Cómo se llama cada paso cuando hay ocho en una sola línea. En el menú
# cada uno tiene sitio para explicarse; aquí no, y lo que importa es
# reconocerlo de un vistazo.
NOMBRES_CORTOS = {
    PREPROCESAR: "Preprocesar",
    PROCESAR: "Procesar",
    DEPURAR: "Depurar",
    EXPORTAR: "Exportar",
    SUBIR: "Subir",
    ESPERAR: "Esperar",
    INDEXAR: "Indexar",
    COMPLETAR: "Completar",
}

# Cómo se llama cada preferencia dentro de ``airvault.json``. «Completar
# batch» conserva el nombre que ya tenía escrito en las instalaciones.
_CLAVES = {
    DEPURAR: "auto_depurar",
    SUBIR: "auto_subir",
    ESPERAR: "auto_esperar",
    INDEXAR: "auto_indexar",
    COMPLETAR: "completar_batch",
}

# Lo que dice cada casilla y qué se explica al pasar por encima.
ETIQUETAS = {
    DEPURAR: "Depurar páginas repetidas y en blanco",
    SUBIR: "Subir a AirVault",
    ESPERAR: "Esperar a que AirVault los deje listos",
    INDEXAR: "Indexar páginas",
    COMPLETAR: "Completar batch",
}

AYUDAS = {
    DEPURAR: (
        "Quita de la ejecución las bitácoras repetidas y las páginas en "
        "blanco antes de exportar, así que los PDF de la entrega salen ya "
        "sin ellas. Se quitan las apariciones sobrantes de cada repetida, "
        "nunca la primera. Sin marcar, la ejecución se exporta entera y "
        "«Depurar» sigue disponible para revisarla a mano."
    ),
    SUBIR: (
        "Manda a Quick Upload todos los batches de la entrega en cuanto la "
        "exportación termina, sin abrir la ventana de AirVault a mano."
    ),
    ESPERAR: (
        "Le pregunta a AirVault cada tantos minutos si ya terminó de "
        "procesar lo subido. Es la misma casilla que «Comprobar cada» en la "
        "ventana de AirVault, donde se elige el intervalo."
    ),
    INDEXAR: (
        "Escribe los datos de cada batch apenas AirVault lo deja entero, sin "
        "esperar a los demás. También borra las páginas separadoras."
    ),
    COMPLETAR: (
        "Al terminar de escribir, da el batch por terminado en AirVault: lo "
        "indexa y lo manda a Web Search. Es la misma casilla que «Completar "
        "batch» en la ventana de AirVault: marcarla en un sitio la marca en "
        "el otro."
    ),
}

# Los tres que no se eligen, con el motivo por el que no se eligen.
FIJOS = (
    (
        "Preprocesar (enderezar y alinear)",
        "Siempre se hace: es la primera parte del procesamiento, la que "
        "endereza y alinea cada página para que el OCR lea donde debe.",
    ),
    (
        "Procesar (OCR)",
        "Siempre se hace: es de donde salen los datos de la entrega.",
    ),
    (
        "Exportar CSV, JSON y PDF",
        "Siempre se hace: es la entrega, y sin ella no hay nada que subir.",
    ),
)


class OpcionesAutomatizacion(QObject):
    """Los pasos elegidos, con memoria portable y una sola copia viva.

    La comparten la ventana principal y todas las de AirVault: «Completar
    batch» y la espera se ven marcadas igual en los dos sitios porque son el
    mismo valor, no dos casillas que había que acordarse de igualar.
    """

    cambiado = Signal(str, bool)

    def __init__(self, raiz: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ruta = Path(raiz) / AIRVAULT_FILENAME
        config = AirVaultConfig.load(self._ruta)
        self._valores = {
            DEPURAR: bool(config.auto_depurar),
            SUBIR: bool(config.auto_subir),
            ESPERAR: bool(config.auto_esperar),
            INDEXAR: bool(config.auto_indexar),
            # Sin preferencia guardada no se inventa ninguna ni se escribe
            # el archivo: quien nunca tocó la casilla la encuentra apagada.
            COMPLETAR: bool(config.completar_batch),
        }

    # ── consulta ───────────────────────────────────────────────────

    def valor(self, paso: str) -> bool:
        return self._valores[paso]

    @property
    def depurar(self) -> bool:
        return self._valores[DEPURAR]

    @property
    def subir(self) -> bool:
        return self._valores[SUBIR]

    @property
    def esperar(self) -> bool:
        return self._valores[ESPERAR]

    @property
    def indexar(self) -> bool:
        return self._valores[INDEXAR]

    @property
    def completar(self) -> bool:
        return self._valores[COMPLETAR]

    # ── cambio ─────────────────────────────────────────────────────

    def fijar(self, paso: str, marcado: bool) -> None:
        """Cambia un paso, arrastra la cadena y lo deja guardado."""
        marcado = bool(marcado)
        if self._valores[paso] == marcado:
            return
        cambios = {paso: marcado}
        if paso in CADENA:
            posicion = CADENA.index(paso)
            arrastrados = (
                # Marcar uno enciende todo lo que hace falta antes.
                CADENA[:posicion] if marcado
                # Apagarlo apaga lo que ya no tiene sobre qué trabajar.
                else CADENA[posicion + 1:]
            )
            cambios.update({
                otro: marcado for otro in arrastrados
                if self._valores[otro] != marcado
            })
        self._valores.update(cambios)
        guardar_preferencias(
            self._ruta,
            **{_CLAVES[nombre]: valor for nombre, valor in cambios.items()},
        )
        for nombre, valor in cambios.items():
            self.cambiado.emit(nombre, valor)


class MenuAutomatizacion(QMenu):
    """La lista de pasos, encima de la ventana y no dentro de ella.

    Se abre pegada al botón que la pide, como el menú del botón derecho, y
    no se cierra al marcar: se marcan los pasos que hagan falta y se sale
    con un clic fuera o con Escape.
    """

    TITULO = "Hasta dónde continuar automáticamente"

    def __init__(
        self,
        opciones: OpcionesAutomatizacion,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._opciones = opciones
        # Sin esto las explicaciones de cada paso no salen: un menú no
        # enseña las ayudas de sus acciones a menos que se le pida.
        self.setToolTipsVisible(True)

        self.addSection(self.TITULO)
        for texto, ayuda in FIJOS:
            fijo = self.addAction(texto)
            fijo.setCheckable(True)
            fijo.setChecked(True)
            fijo.setEnabled(False)
            fijo.setToolTip(ayuda)

        self._acciones: dict[str, object] = {}
        for paso in PASOS:
            if paso in (DEPURAR, SUBIR):
                # Lo suelto y la cadena de AirVault, cada uno en su bloque.
                self.addSeparator()
            accion = self.addAction(ETIQUETAS[paso])
            accion.setCheckable(True)
            accion.setChecked(opciones.valor(paso))
            accion.setToolTip(AYUDAS[paso])
            accion.toggled.connect(
                lambda marcado, paso=paso: self._opciones.fijar(paso, marcado)
            )
            self._acciones[paso] = accion

        opciones.cambiado.connect(self._refrescar)

    def accion(self, paso: str):
        """La entrada de un paso; la usan las pruebas y las ventanas."""
        return self._acciones[paso]

    def abrir_sobre(self, boton: QWidget) -> None:
        """Despliega la lista pegada al botón que la pidió.

        ``popup`` la coloca sola dentro de la pantalla: si abajo no queda
        alto, la sube por encima del botón en vez de recortarla.
        """
        self.popup(boton.mapToGlobal(boton.rect().bottomLeft()))

    # ── que no se cierre en el primer clic ─────────────────────────

    def mouseReleaseEvent(self, event) -> None:
        """Marca sin cerrar: son varias casillas, no una orden."""
        accion = self.activeAction()
        if accion is not None and accion.isEnabled() and accion.isCheckable():
            accion.trigger()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        """Lo mismo con el teclado, para no obligar a usar el ratón."""
        teclas = (
            Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter,
        )
        accion = self.activeAction()
        if (
            event.key() in teclas
            and accion is not None
            and accion.isEnabled()
            and accion.isCheckable()
        ):
            accion.trigger()
            return
        super().keyPressEvent(event)

    def _refrescar(self, paso: str, marcado: bool) -> None:
        """Refleja lo que cambió en otro sitio sin volver a anunciarlo."""
        accion = self._acciones.get(paso)
        if accion is None or accion.isChecked() == marcado:
            return
        with QSignalBlocker(accion):
            accion.setChecked(marcado)


# ── hasta dónde llegó ──────────────────────────────────────────────
#
# El menú dice hasta dónde se va a llegar. Esto dice hasta dónde se llegó,
# que no es lo mismo y hasta ahora no se veía en ninguna parte: la ventana
# principal soltaba la cadena al exportar y lo que seguía pasaba en la de
# AirVault, así que quien miraba la principal no sabía si la entrega había
# terminado de subirse, se había quedado esperando o se había cortado.

PENDIENTE = "pendiente"
EN_CURSO = "en_curso"
HECHO = "hecho"
CORTADO = "cortado"
# El paso no está marcado en «Automatización…»: no se va a hacer, y no
# tenerlo en cuenta al contar es lo que hace que «5 de 5» signifique algo.
OMITIDO = "omitido"


class CadenaAutomatica(QWidget):
    """Los ocho pasos en una línea, cada uno con lo que le pasó.

    Va debajo de la barra de progreso porque cuenta lo mismo que ella pero
    a otra escala: la barra dice cuánto falta del paso en curso, y esta,
    cuántos pasos faltan de la entrega. Los pasos apagados son los que no
    se eligieron; los demás van cambiando de color según ocurren.

    Los cuatro últimos ocurren en la ventana de AirVault, así que los marca
    esa ventana a través de la principal. Sin eso, la línea se quedaba en
    «Exportar» y no había forma de saber desde aquí si el batch llegó.
    """

    # Gris del texto de ayuda, el verde y el rojo de los estados de panel, y
    # el azul de la selección. No se estrena ningún color.
    _COLORES = {
        PENDIENTE: "#8b949e",
        OMITIDO: "#5a5f66",
        EN_CURSO: "#2f81f7",
        HECHO: "#3fb950",
        CORTADO: "#f85149",
    }

    _AYUDAS = {
        PENDIENTE: "todavía no empezó",
        OMITIDO: "no está marcado en «Automatización…»; no se hace",
        EN_CURSO: "en curso",
        HECHO: "terminado",
        CORTADO: "se cortó aquí",
    }

    def __init__(
        self,
        opciones: OpcionesAutomatizacion,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._opciones = opciones
        self._estados: dict[str, str] = {}
        self._etiquetas: dict[str, QLabel] = {}

        fila = QHBoxLayout(self)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(6)
        # Ocho nombres seguidos suman más ancho que muchos de los cuadros
        # de arriba, y el ancho mínimo de la ventana es el del contenido más
        # ancho que tenga: sin esto, esta línea decidía sola que la ventana
        # no se puede abrir en una pantalla de 1024. Es un rótulo, no un
        # control: si no cabe entero, se recorta por la derecha y el resto
        # sigue en el tooltip de cada paso.
        fila.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        for indice, paso in enumerate(RECORRIDO):
            if indice:
                flecha = QLabel("›")
                flecha.setStyleSheet(f"color: {self._COLORES[OMITIDO]};")
                fila.addWidget(flecha)
            etiqueta = QLabel(NOMBRES_CORTOS[paso])
            etiqueta.setAccessibleName(f"Paso {NOMBRES_CORTOS[paso]}")
            fila.addWidget(etiqueta)
            self._etiquetas[paso] = etiqueta
        fila.addStretch(1)

        opciones.cambiado.connect(lambda *_args: self.reiniciar())
        self.reiniciar()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        """Alto sí, ancho no: el ancho lo deciden los cuadros de arriba."""
        return QSize(0, super().minimumSizeHint().height())

    # ── consulta ───────────────────────────────────────────────────

    def elegido(self, paso: str) -> bool:
        """Si ese paso se va a hacer con las opciones de ahora."""
        if paso in (PREPROCESAR, PROCESAR, EXPORTAR):
            return True
        return self._opciones.valor(paso)

    def estado(self, paso: str) -> str:
        return self._estados[paso]

    def resumen(self) -> str:
        """Una línea con hasta dónde llegó, para la bitácora y las pruebas."""
        elegidos = [paso for paso in RECORRIDO if self.elegido(paso)]
        hechos = sum(1 for paso in elegidos if self._estados[paso] == HECHO)
        cortado = next(
            (paso for paso in RECORRIDO if self._estados[paso] == CORTADO),
            "",
        )
        if cortado:
            return (
                f"Automático: se cortó en «{NOMBRES_CORTOS[cortado]}» "
                f"({hechos} de {len(elegidos)} pasos)"
            )
        if hechos == len(elegidos):
            return "Automático: completo"
        en_curso = next(
            (paso for paso in RECORRIDO if self._estados[paso] == EN_CURSO),
            "",
        )
        if en_curso:
            return (
                f"Automático: {NOMBRES_CORTOS[en_curso].lower()} "
                f"({hechos} de {len(elegidos)} pasos)"
            )
        return f"Automático: {hechos} de {len(elegidos)} pasos"

    # ── cambio ─────────────────────────────────────────────────────

    def reiniciar(self) -> None:
        """Deja la línea como al empezar, con lo elegido de ahora."""
        self._estados = {
            paso: PENDIENTE if self.elegido(paso) else OMITIDO
            for paso in RECORRIDO
        }
        self._repintar()

    def marcar(self, paso: str, estado: str) -> None:
        """Anota lo que le pasó a un paso y arrastra lo que implica.

        Empezar un paso da por terminados los anteriores que estaban en
        curso: la cadena avanza en un solo sentido y no hay dos pasos
        corriendo a la vez, así que enterarse del cuarto es enterarse de
        que el tercero acabó. Hace falta porque los pasos de AirVault
        llegan desde otra ventana y alguno puede perderse por el camino.
        """
        if paso not in self._estados:
            return
        if estado == EN_CURSO:
            for anterior in RECORRIDO[: RECORRIDO.index(paso)]:
                if self._estados[anterior] == EN_CURSO:
                    self._estados[anterior] = HECHO
        self._estados[paso] = estado
        self._repintar()

    def cortar(self) -> None:
        """La cadena se soltó: el paso que estuviera en curso se cortó."""
        for paso in RECORRIDO:
            if self._estados[paso] == EN_CURSO:
                self._estados[paso] = CORTADO
        self._repintar()

    def _repintar(self) -> None:
        for paso, etiqueta in self._etiquetas.items():
            estado = self._estados[paso]
            peso = "600" if estado in (EN_CURSO, CORTADO) else "400"
            etiqueta.setStyleSheet(
                f"color: {self._COLORES[estado]}; font-weight: {peso};"
            )
            etiqueta.setToolTip(
                f"{NOMBRES_CORTOS[paso]}: {self._AYUDAS[estado]}"
            )
