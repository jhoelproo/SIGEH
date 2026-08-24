import os
import inspect
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGroupBox, QHBoxLayout, QLabel, QSizePolicy,
    QVBoxLayout,
)

import CALCULOS_QT as app


class ReportsEvolutionLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_all_result_tabs_remain_and_evolution_expands(self):
        source = inspect.getsource(app.ReportsDialog._build_dashboard_tab)
        for label in ("Resumen", "Distribuciones", "Evolución", "Detalle"):
            self.assertIn(label, source)
        self.assertIn("line_layout.addWidget(self.line_chart, 1)", source)
        self.assertIn("dashboard_evolution_layout.addWidget(line_box, 1)", source)

        box = QGroupBox("Evolución diaria")
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(box)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Evolución de:"))
        controls.addWidget(QComboBox(), 1)
        layout.addLayout(controls)
        chart = app.ModernLineChart()
        layout.addWidget(chart, 1)
        try:
            self.assertEqual(
                box.sizePolicy().verticalPolicy(),
                QSizePolicy.Expanding,
            )
            chart_index = layout.indexOf(chart)
            self.assertGreaterEqual(chart_index, 0)
            self.assertEqual(layout.stretch(chart_index), 1)

            for width, height in ((900, 700), (1400, 900)):
                box.resize(width, height)
                box.show()
                self.qt_app.processEvents()
                self.assertGreaterEqual(chart.height(), 240)
        finally:
            box.close()

    def test_billing_date_is_one_integrated_control(self):
        widget = app.BillingDateEdit()
        try:
            widget.resize(334, 40)
            widget.setDisplayFormat("dd-MM-yyyy")
            widget.setDate(QDate(2026, 7, 28))
            widget.show()
            self.qt_app.processEvents()

            self.assertEqual(widget.height(), 40)
            self.assertEqual(widget.editor.height(), 38)
            self.assertEqual(widget.calendar_button.height(), 38)
            self.assertEqual(
                widget.editor.geometry().right() + 1,
                widget.calendar_button.geometry().left(),
            )
            self.assertIn("QWidget#BillingServiceDate", widget.styleSheet())

            widget._select_date(QDate(2026, 8, 5))
            self.assertEqual(widget.date().toString("dd-MM-yyyy"), "05-08-2026")
            for dark in (False, True):
                widget.apply_theme(dark)
                self.assertFalse(widget.calendar_button.icon().isNull())
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
