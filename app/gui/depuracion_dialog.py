"""Cuadro «Depurar páginas», compartido por la ventana principal y el visor.

Las dos vistas que muestran a la vez el CSV y su PDF ofrecen lo mismo, así
que el cuadro se construye una sola vez: los mismos textos, las mismas
casillas y el mismo conteo antes de borrar nada.

Cada criterio trae además la lista de lo que se llevaría. En los duplicados
se enseña el grupo entero de cada bitácora repetida, no solo las apariciones
sobrantes: para decidir cuál se va hay que ver también la que se queda, y a
veces la buena es la segunda.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.responsive import fit_to_screen
from app.validation.depuracion import (
    PaginaDepurable,
    ResumenDepuracion,
    contar_depuracion,
    grupos_duplicados,
    paginas_en_blanco,
)

DEPURAR_TOOLTIP = (
    "Quitar de la ejecución las páginas repetidas o en blanco. Se reescriben "
    "el CSV, el JSON y las estadísticas sin ellas; los PDF se rehacen al "
    "exportar."
)

# La clave de cada página viaja en el propio elemento del árbol: es lo que
# después se le pasa a ``depurar_claves`` y no depende de en qué fila quedó.
_CLAVE = Qt.ItemDataRole.UserRole


def _texto_conteo(cantidad: int) -> str:
    if cantidad == 1:
        return "1 página"
    return f"{cantidad} páginas"


def _etiqueta(pagina: PaginaDepurable) -> str:
    """Cómo se nombra una página en las listas del cuadro."""
    return f"{pagina.archivo}, página {pagina.pagina}"


class DepurarPaginasDialog(QDialog):
    """Elige qué páginas se quitan y enseña cuáles son antes de hacerlo."""

    def __init__(self, reports, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reports = list(reports)
        # El conteo por criterio no cambia mientras el cuadro está abierto:
        # se mide una vez sobre los reportes y las casillas solo eligen
        # cuáles de esas páginas entran en el total.
        self._disponibles = contar_depuracion(self._reports, True, True)
        self._grupos = grupos_duplicados(self._reports)
        self._blancas = paginas_en_blanco(self._reports)
        self.setWindowTitle("Depurar páginas")
        # Como el resto de los cuadros: la pantalla decide el tamaño, que en
        # un portátil bajo el alto pedido deja los botones fuera del borde.
        fit_to_screen(self, 560, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Se quitan de la ejecución las páginas que marque. Se reescriben "
            "el CSV, el JSON y las estadísticas sin ellas; los PDF ya "
            "exportados las conservan hasta que vuelva a exportar."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.check_duplicados = QCheckBox(
            f"Duplicados: {_texto_conteo(self._disponibles.duplicadas)}"
        )
        self.check_duplicados.setToolTip(
            "Apariciones repetidas de un mismo log_number. Se marca la "
            "segunda y las siguientes; abajo puede conservar otra en su "
            "lugar. Las páginas sin log_number legible no se consideran "
            "repetidas."
        )
        self.check_duplicados.setEnabled(bool(self._disponibles.duplicadas))
        self.check_duplicados.toggled.connect(self._marcar_duplicados)
        layout.addWidget(self.check_duplicados)

        self.arbol_duplicados = self._nuevo_arbol(
            "Cada bitácora repetida con todas sus apariciones. Desmarque la "
            "que quiera conservar y marque la que sobra."
        )
        self._llenar_duplicados()
        layout.addWidget(self.arbol_duplicados, 1)

        self.check_blancas = QCheckBox(
            f"Páginas en blanco: {_texto_conteo(self._disponibles.en_blanco)}"
        )
        self.check_blancas.setToolTip(
            "Páginas que el procesamiento marcó como vacías, sin nada que "
            "leer en la región de la plantilla."
        )
        self.check_blancas.setEnabled(bool(self._disponibles.en_blanco))
        self.check_blancas.toggled.connect(self._marcar_blancas)
        layout.addWidget(self.check_blancas)

        self.arbol_blancas = self._nuevo_arbol(
            "Las páginas que quedaron vacías. Desmarque la que prefiera "
            "conservar."
        )
        self._llenar_blancas()
        layout.addWidget(self.arbol_blancas, 1)

        self.total_label = QLabel()
        self.total_label.setStyleSheet("color: #57606a;")
        self.total_label.setWordWrap(True)
        layout.addWidget(self.total_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.boton_eliminar = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.boton_eliminar.setText("Eliminar")
        self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        ).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # Ninguna marcada de entrada: el cuadro se abre sin nada que borrar y
        # es quien lo abre el que elige, no el que descubre lo ya marcado.
        self._refrescar_total()

    def _nuevo_arbol(self, ayuda: str) -> QTreeWidget:
        arbol = QTreeWidget()
        arbol.setHeaderHidden(True)
        arbol.setToolTip(ayuda)
        arbol.setUniformRowHeights(True)
        arbol.setRootIsDecorated(True)
        arbol.itemChanged.connect(self._al_cambiar_marca)
        return arbol

    def _nueva_hoja(
        self, pagina: PaginaDepurable, texto: str, padre
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(padre, [texto])
        item.setData(0, _CLAVE, pagina.clave)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        return item

    def _llenar_duplicados(self) -> None:
        for numero, paginas in self._grupos:
            cabeza = QTreeWidgetItem(
                self.arbol_duplicados,
                [f"{numero:07d}: {len(paginas)} apariciones"],
            )
            # La cabecera agrupa, no se elige: marcarla no querría decir
            # nada, porque borrar el grupo entero pierde la bitácora.
            cabeza.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cabeza.setExpanded(True)
            for orden, pagina in enumerate(paginas):
                sufijo = " (primera)" if orden == 0 else ""
                self._nueva_hoja(pagina, _etiqueta(pagina) + sufijo, cabeza)

    def _llenar_blancas(self) -> None:
        for pagina in self._blancas:
            self._nueva_hoja(pagina, _etiqueta(pagina), self.arbol_blancas)

    def _hojas(self, arbol: QTreeWidget):
        """Recorre las páginas de un árbol, estén donde estén."""
        for indice in range(arbol.topLevelItemCount()):
            item = arbol.topLevelItem(indice)
            if item.data(0, _CLAVE) is not None:
                yield item
            for hijo in range(item.childCount()):
                yield item.child(hijo)

    def _marcar(self, arbol: QTreeWidget, marcadas) -> None:
        """Deja marcadas exactamente ``marcadas`` sin disparar un refresco por hoja."""
        arbol.blockSignals(True)
        try:
            for item in self._hojas(arbol):
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if item.data(0, _CLAVE) in marcadas
                    else Qt.CheckState.Unchecked,
                )
        finally:
            arbol.blockSignals(False)
        self._refrescar_total()

    def _marcar_duplicados(self, activado: bool) -> None:
        """Al encender el criterio se marcan las apariciones sobrantes."""
        sobrantes = {
            pagina.clave
            for _numero, paginas in self._grupos
            for pagina in paginas
            if pagina.duplicada
        }
        self._marcar(self.arbol_duplicados, sobrantes if activado else set())

    def _marcar_blancas(self, activado: bool) -> None:
        self._marcar(
            self.arbol_blancas,
            {pagina.clave for pagina in self._blancas} if activado else set(),
        )

    def _al_cambiar_marca(self, _item, _columna: int) -> None:
        self._refrescar_total()

    def _refrescar_total(self) -> None:
        resumen = self.resumen()
        if resumen.total:
            self.total_label.setText(
                f"Se eliminarán {_texto_conteo(resumen.total)} de la ejecución."
            )
        elif not self._disponibles.total:
            self.total_label.setText(
                "La ejecución no tiene páginas repetidas ni en blanco."
            )
        else:
            self.total_label.setText("Marque al menos un criterio.")
        self.boton_eliminar.setEnabled(bool(resumen.total))

    def _claves_de(self, arbol: QTreeWidget) -> set:
        return {
            item.data(0, _CLAVE)
            for item in self._hojas(arbol)
            if item.checkState(0) == Qt.CheckState.Checked
        }

    def hay_depurables(self) -> bool:
        """Si la ejecución trae algo que se pudiera quitar.

        Distingue las dos maneras de salir sin borrar nada: que no hubiera
        páginas repetidas ni vacías, o que las hubiera y no se marcara
        ninguna. Quien abre el cuadro merece leer cuál de las dos fue.
        """
        return bool(self._disponibles.total)

    def duplicados(self) -> bool:
        return self.check_duplicados.isChecked()

    def en_blanco(self) -> bool:
        return self.check_blancas.isChecked()

    def claves(self) -> set:
        """Las páginas elegidas, sin repetir la que cae por los dos criterios."""
        return self._claves_de(self.arbol_duplicados) | self._claves_de(
            self.arbol_blancas
        )

    def resumen(self) -> ResumenDepuracion:
        """Lo que se quitaría con lo marcado en este momento."""
        duplicadas = self._claves_de(self.arbol_duplicados)
        blancas = self._claves_de(self.arbol_blancas)
        return ResumenDepuracion(
            duplicadas=len(duplicadas),
            en_blanco=len(blancas),
            total=len(duplicadas | blancas),
        )
