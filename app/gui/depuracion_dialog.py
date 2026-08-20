"""Cuadro «Depurar páginas», compartido por la ventana principal y el visor.

Las dos vistas que muestran a la vez el CSV y su PDF ofrecen lo mismo, así
que el cuadro se construye una sola vez: los mismos textos, las mismas
casillas y el mismo conteo antes de borrar nada.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.gui.responsive import fit_to_screen
from app.validation.depuracion import ResumenDepuracion, contar_depuracion

DEPURAR_TOOLTIP = (
    "Quitar de la corrida las páginas repetidas o en blanco. Se reescriben "
    "el CSV, el JSON y las estadísticas sin ellas; los PDF se rehacen al "
    "exportar."
)


def _texto_conteo(cantidad: int) -> str:
    if cantidad == 1:
        return "1 página"
    return f"{cantidad} páginas"


class DepurarPaginasDialog(QDialog):
    """Elige qué páginas se quitan y enseña cuántas son antes de hacerlo."""

    def __init__(self, reports, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reports = list(reports)
        # El conteo por criterio no cambia mientras el cuadro está abierto:
        # se mide una vez sobre los reportes y las casillas solo eligen
        # cuáles de esos dos números entran en el total.
        self._disponibles = contar_depuracion(self._reports, True, True)
        self.setWindowTitle("Depurar páginas")
        # Como el resto de los cuadros: la pantalla decide el tamaño, que en
        # un portátil bajo el alto pedido deja los botones fuera del borde.
        fit_to_screen(self, 420, 250)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Se quitan de la corrida las páginas que marque. Se reescriben "
            "el CSV, el JSON y las estadísticas sin ellas; los PDF ya "
            "exportados las conservan hasta que vuelva a exportar."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.check_duplicados = QCheckBox(
            f"Duplicados — {_texto_conteo(self._disponibles.duplicadas)}"
        )
        self.check_duplicados.setToolTip(
            "Apariciones repetidas de un mismo log_number. Se conserva la "
            "primera de cada uno; las páginas sin log_number legible no se "
            "consideran repetidas."
        )
        self.check_duplicados.setEnabled(bool(self._disponibles.duplicadas))
        self.check_duplicados.toggled.connect(self._refrescar_total)
        layout.addWidget(self.check_duplicados)

        self.check_blancas = QCheckBox(
            f"Páginas en blanco — {_texto_conteo(self._disponibles.en_blanco)}"
        )
        self.check_blancas.setToolTip(
            "Páginas que el procesamiento marcó como vacías, sin nada que "
            "leer en la región de la plantilla."
        )
        self.check_blancas.setEnabled(bool(self._disponibles.en_blanco))
        self.check_blancas.toggled.connect(self._refrescar_total)
        layout.addWidget(self.check_blancas)

        self.total_label = QLabel()
        self.total_label.setStyleSheet("color: #57606a;")
        self.total_label.setWordWrap(True)
        layout.addWidget(self.total_label)
        layout.addStretch()

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

    def _refrescar_total(self) -> None:
        resumen = self.resumen()
        if resumen.total:
            self.total_label.setText(
                f"Se eliminarán {_texto_conteo(resumen.total)} de la corrida."
            )
        elif not self._disponibles.total:
            self.total_label.setText(
                "La corrida no tiene páginas repetidas ni en blanco."
            )
        else:
            self.total_label.setText("Marque al menos un criterio.")
        self.boton_eliminar.setEnabled(bool(resumen.total))

    def duplicados(self) -> bool:
        return self.check_duplicados.isChecked()

    def en_blanco(self) -> bool:
        return self.check_blancas.isChecked()

    def resumen(self) -> ResumenDepuracion:
        """Lo que se quitaría con lo marcado en este momento."""
        return contar_depuracion(
            self._reports, self.duplicados(), self.en_blanco()
        )
