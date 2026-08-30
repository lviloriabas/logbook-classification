"""Opciones compactas de salida compartidas por las ventanas."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.airvault.config import (
    AIRVAULT_FILENAME,
    AirVaultConfig,
    guardar_paginas_por_batch,
)
from app.gui.widgets import (
    MultiSelectMenu,
    SpinBoxWithButtons,
    configure_combo_box,
    configure_menu_button,
)
from app.reports.csv_reporter import CSV_DATE_MONTH_END, CSV_DATE_SPECIFIC


class ExportOptionsGroup(QGroupBox):
    """Salida, separacion y fecha en dos filas compactas."""

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

        main_row = QHBoxLayout()
        main_row.setSpacing(8)
        pdf_label = QLabel("PDF:")
        main_row.addWidget(pdf_label)
        self.controls_indent = pdf_label.sizeHint().width() + main_row.spacing()
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItem("Un solo PDF", True)
        self.output_mode_combo.addItem("Varios PDF", False)
        self.output_mode_combo.setToolTip(
            "El PDF unico conserva las secciones en una entrega; varios PDF "
            "crea un archivo por cada separacion marcada."
        )
        configure_combo_box(self.output_mode_combo, 12)
        main_row.addWidget(self.output_mode_combo)

        main_row.addSpacing(8)
        main_row.addWidget(QLabel("Fecha del CSV:"))
        self.csv_date_mode_combo = QComboBox()
        self.csv_date_mode_combo.addItem("Día específico", CSV_DATE_SPECIFIC)
        self.csv_date_mode_combo.addItem("Fin de mes", CSV_DATE_MONTH_END)
        self.csv_date_mode_combo.setItemData(
            0,
            "Usa el día reconocido; si falta, usa el último día del mes.",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.csv_date_mode_combo.setItemData(
            1,
            "Usa siempre el último día del mes reconocido.",
            Qt.ItemDataRole.ToolTipRole,
        )
        self.csv_date_mode_combo.setToolTip(
            "Cambia la fecha representada en el CSV sin volver a ejecutar OCR. "
            "El resultado OCR original se conserva."
        )
        configure_combo_box(self.csv_date_mode_combo, 14)
        main_row.addWidget(self.csv_date_mode_combo)
        main_row.addStretch()
        layout.addLayout(main_row)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(8)
        detail_row.addSpacing(self.controls_indent)
        self.separation_menu = MultiSelectMenu(self)
        self.matricula_check = self._checkable_action(
            "Matrícula",
            "Separa la entrega por matrícula.",
            checked=True,
        )
        self.mes_check = self._checkable_action(
            "Mes", "Separa la entrega por mes."
        )
        self.discrepancias_check = self._checkable_action(
            "Posibles discrepancias",
            "Agrega una sección con posibles discrepancias de firma.",
            checked=True,
        )
        self.errores_check = self._checkable_action(
            "Errores",
            "Genera errores.pdf con las páginas que requieren revisión manual.",
        )
        self.separation_button = QToolButton()
        self.separation_button.setText("Separación")
        self.separation_button.setToolTip(
            "Elegir cómo se separan los PDF y qué apartados adicionales salen."
        )
        configure_menu_button(self.separation_button, self.separation_menu)
        detail_row.addWidget(self.separation_button)

        self.partes_check = QCheckBox("Repartir en")
        self.partes_check.setToolTip(
            "Reparte el PDF único en varias partes sin cortar secciones."
        )
        detail_row.addWidget(self.partes_check)
        self.partes_spin = QSpinBox()
        self.partes_spin.setRange(10, 5000)
        self.partes_spin.setSingleStep(50)
        guardadas = AirVaultConfig.load(
            self._ruta_preferencias
        ).paginas_por_batch
        if guardadas is not None:
            self.partes_spin.setValue(guardadas)
        self.partes_spin.setSuffix(" pág.")
        self.partes_spin.setToolTip(
            "Páginas como máximo en cada parte, contando las separadoras"
        )
        self.partes_spin.valueChanged.connect(
            lambda cantidad: guardar_paginas_por_batch(
                self._ruta_preferencias, cantidad
            )
        )
        self.partes_control = SpinBoxWithButtons(self.partes_spin)
        detail_row.addWidget(self.partes_control)
        detail_row.addStretch()
        layout.addLayout(detail_row)

        self.output_mode_combo.currentIndexChanged.connect(self._sync_parts)
        self.partes_check.toggled.connect(self._sync_parts)
        self._sync_parts()

    def _checkable_action(
        self, text: str, tooltip: str, checked: bool = False
    ):
        action = self.separation_menu.addAction(text)
        action.setCheckable(True)
        action.setChecked(checked)
        action.setToolTip(tooltip)
        return action

    def _sync_parts(self, *_args) -> None:
        single = self.un_solo_pdf()
        self.partes_check.setEnabled(single)
        self.partes_spin.setEnabled(single and self.partes_check.isChecked())

    def un_solo_pdf(self) -> bool:
        return bool(self.output_mode_combo.currentData())

    def set_un_solo_pdf(self, single: bool) -> None:
        index = self.output_mode_combo.findData(bool(single))
        if index >= 0:
            self.output_mode_combo.setCurrentIndex(index)

    def separar_por(self) -> list[str] | None:
        separator = []
        if self.matricula_check.isChecked():
            separator.append("avion")
        if self.mes_check.isChecked():
            separator.append("mes")
        return separator or None

    def csv_date_mode(self) -> str:
        return self.csv_date_mode_combo.currentData() or CSV_DATE_SPECIFIC
