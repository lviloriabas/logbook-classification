"""Opciones de salida compartidas por la ventana principal y el visor de CSV.

Las dos ventanas exportan la misma corrida con los mismos criterios, así que
el cuadro «Salidas» se construye una sola vez: el formato del PDF, los
criterios de separación y la fecha representada en el CSV son exactamente los
mismos textos y las mismas casillas en ambas.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.reports.csv_reporter import CSV_DATE_MONTH_END, CSV_DATE_SPECIFIC


class ExportOptionsGroup(QGroupBox):
    """Cuadro «Salidas»: formato, separación y fecha del CSV."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Salidas", parent)
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
            "Genera un único PDF con el mismo nombre que la carpeta de la "
            "corrida, con páginas separadoras de matrícula/mes para los "
            "criterios marcados"
        )
        self.modo_grupo.addButton(self.radio_varios)
        self.modo_grupo.addButton(self.radio_unico)
        # La entrega habitual es un único PDF separado por matrícula: es lo
        # que se marcaba a mano en cada corrida. El mes no entra porque
        # subdivide de más una entrega que ya va por avión.
        self.radio_unico.setChecked(True)
        formato_row.addWidget(self.radio_varios)
        formato_row.addWidget(self.radio_unico)
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
            "Genera errores.pdf con las páginas cuya matrícula, fecha o "
            "número de bitácora quedaron sin resolver, para indexarlas a "
            "mano."
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
