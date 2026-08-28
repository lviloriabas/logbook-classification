"""Opciones de salida compartidas por la ventana principal y el visor de CSV.

Las dos ventanas exportan la misma ejecución con los mismos criterios, así que
el cuadro «Salidas» se construye una sola vez: el formato del PDF, los
criterios de separación y la fecha representada en el CSV son exactamente los
mismos textos y las mismas casillas en ambas.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QSpinBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.airvault.config import (
    AIRVAULT_FILENAME,
    AirVaultConfig,
    guardar_paginas_por_batch,
)
from app.reports.csv_reporter import CSV_DATE_MONTH_END, CSV_DATE_SPECIFIC
from app.gui.widgets import SpinBoxWithButtons, fit_combo_to_items


class ExportOptionsGroup(QGroupBox):
    """Cuadro «Salidas»: formato, separación y fecha del CSV."""

    def __init__(
        self,
        parent: QWidget | None = None,
        raiz: Path | str | None = None,
    ) -> None:
        super().__init__("Salidas", parent)
        self._ruta_preferencias = (
            Path(raiz) if raiz is not None else Path.cwd()
        ) / AIRVAULT_FILENAME
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(4)

        formato_row = QHBoxLayout()
        formato_row.setSpacing(10)
        formato_label = QLabel("Salida")
        formato_label.setStyleSheet("font-weight: 600;")
        formato_row.addWidget(formato_label)
        formato_row.addSpacing(8)
        self.modo_grupo = QButtonGroup(self)
        self.radio_varios = QRadioButton("Varios PDF")
        self.radio_varios.setToolTip("Genera un PDF por cada matrícula/mes marcado")
        self.radio_unico = QRadioButton("Un solo PDF")
        self.radio_unico.setToolTip(
            "Un solo PDF con el nombre de la ejecución, con páginas "
            "separadoras según lo marcado abajo"
        )
        self.modo_grupo.addButton(self.radio_varios)
        self.modo_grupo.addButton(self.radio_unico)
        # La entrega habitual es un único PDF separado por matrícula: es lo
        # que se marcaba a mano en cada ejecución. El mes no entra porque
        # subdivide de más una entrega que ya va por avión.
        self.radio_unico.setChecked(True)
        formato_row.addWidget(self.radio_varios)
        formato_row.addWidget(self.radio_unico)

        formato_row.addSpacing(8)
        self.partes_check = QCheckBox("Repartir en")
        self.partes_check.setToolTip(
            "Reparte la entrega en varios PDF, uno por batch. El corte "
            "respeta las secciones para no separar las bitácoras de un avión."
        )
        formato_row.addWidget(self.partes_check)
        self.partes_spin = QSpinBox()
        self.partes_spin.setRange(10, 5000)
        self.partes_spin.setSingleStep(50)
        guardadas = AirVaultConfig.load(
            self._ruta_preferencias
        ).paginas_por_batch
        if guardadas is not None:
            self.partes_spin.setValue(guardadas)
        self.partes_spin.setSuffix(" pág.")
        self.partes_spin.setEnabled(False)
        # Igualado en alto a los redondeles de al lado: con su alto natural
        # el cuadro crecía 8 px y la ventana no cabía en un escritorio de
        # 1280x720 con el escalado de Windows al 150%.
        self.partes_spin.setFixedHeight(self.radio_unico.sizeHint().height())
        self.partes_spin.setToolTip(
            "Páginas como máximo en cada parte, contando las separadoras"
        )
        self.partes_check.toggled.connect(self.partes_spin.setEnabled)
        self.partes_spin.valueChanged.connect(
            lambda cantidad: guardar_paginas_por_batch(
                self._ruta_preferencias, cantidad
            )
        )
        self.partes_control = SpinBoxWithButtons(self.partes_spin)
        formato_row.addWidget(self.partes_control)

        # Repartir solo tiene sentido sobre el PDF único: «Varios PDF» ya
        # escribe un archivo por matrícula.
        self.radio_unico.toggled.connect(self.partes_check.setEnabled)
        self.radio_unico.toggled.connect(
            lambda activo: self.partes_spin.setEnabled(
                activo and self.partes_check.isChecked()
            )
        )
        formato_row.addStretch()
        layout.addLayout(formato_row)

        sep_row = QHBoxLayout()
        sep_row.setSpacing(10)
        sep_label = QLabel("Separar")
        sep_label.setStyleSheet("font-weight: 600;")
        sep_row.addWidget(sep_label)
        sep_row.addSpacing(8)
        self.matricula_check = QCheckBox("Matrícula")
        self.matricula_check.setChecked(True)
        self.matricula_check.setToolTip(
            "Varios PDF: un archivo por matrícula. "
            "Un solo PDF: página separadora por matrícula."
        )
        sep_row.addWidget(self.matricula_check)
        self.mes_check = QCheckBox("Mes")
        self.mes_check.setToolTip(
            "Varios PDF: un archivo por mes. Un solo PDF: página separadora por mes."
        )
        sep_row.addWidget(self.mes_check)

        # El nombre es el del apartado que sale en el PDF: lo que se marca
        # es una sospecha de firma distinta, no una discrepancia confirmada.
        self.discrepancias_check = QCheckBox("Posibles discrepancias")
        self.discrepancias_check.setChecked(True)
        self.discrepancias_check.setToolTip(
            "Un solo PDF: agrega al final una sección 'Posibles "
            "discrepancias'. Varios PDF: genera discrepancias.pdf."
        )
        sep_row.addWidget(self.discrepancias_check)

        self.errores_check = QCheckBox("Errores")
        self.errores_check.setToolTip(
            "Genera errores.pdf con las páginas sin matrícula, fecha o "
            "número de bitácora, para indexarlas a mano."
        )
        sep_row.addWidget(self.errores_check)
        sep_row.addStretch()
        layout.addLayout(sep_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(10)
        date_label = QLabel("Fecha del CSV")
        date_label.setStyleSheet("font-weight: 600;")
        date_row.addWidget(date_label)
        date_row.addSpacing(8)
        self.csv_date_mode_combo = QComboBox()
        self.csv_date_mode_combo.addItem(
            "Día específico (si falta, fin de mes)", CSV_DATE_SPECIFIC
        )
        self.csv_date_mode_combo.addItem(
            "Último día del mes", CSV_DATE_MONTH_END
        )
        self.csv_date_mode_combo.setToolTip(
            "Cambia la fecha representada en el CSV sin volver a ejecutar OCR. "
            "El resultado OCR original se conserva."
        )
        fit_combo_to_items(self.csv_date_mode_combo)
        date_row.addWidget(self.csv_date_mode_combo)
        date_row.addStretch()
        layout.addLayout(date_row)

    def separar_por(self) -> list[str] | None:
        """Devuelve las claves para generar_pdfs según las casillas."""
        separator = []
        if self.matricula_check.isChecked():
            separator.append("avion")
        if self.mes_check.isChecked():
            separator.append("mes")
        return separator or None

    def csv_date_mode(self) -> str:
        """Política reversible usada únicamente al representar el CSV."""
        return self.csv_date_mode_combo.currentData() or CSV_DATE_SPECIFIC
