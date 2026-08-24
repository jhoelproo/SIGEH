import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


class CatalogFavoritesResponsiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_favorites_are_rendered_first_with_active_star(self):
        widget = app.CatalogListWidget()
        owner = SimpleNamespace(
            source_lists={"Medicamentos": widget},
            catalog_favorites={("Medicamentos", "Frecuente")},
            catalog_font_size=11,
        )
        with patch.object(app, "get_effective_price", side_effect=lambda _cat, price: price):
            app.MainWindow._fill_list(
                owner,
                widget,
                {"Otro": 50.0, "Frecuente": 75.0},
                "",
            )
        self.assertEqual(widget.item(0).data(Qt.UserRole)[0], "Frecuente")
        self.assertTrue(widget.item(0).data(app.CATALOG_FAVORITE_ROLE))

    def test_star_area_emits_a_favorite_toggle(self):
        widget = app.CatalogListWidget()
        widget.resize(500, 160)
        item = app.QListWidgetItem("Elemento")
        item.setData(Qt.UserRole, ("Elemento", 100.0))
        item.setData(app.CATALOG_FAVORITE_ROLE, False)
        widget.addItem(item)
        widget.show()
        self.qt_app.processEvents()

        emitted = []
        widget.favoriteToggled.connect(
            lambda name, price, enabled: emitted.append((name, price, enabled))
        )
        rect = widget.visualItemRect(item)
        QTest.mouseClick(
            widget.viewport(),
            Qt.LeftButton,
            pos=QPoint(widget.viewport().width() - 10, rect.center().y()),
        )
        self.assertEqual(emitted, [("Elemento", 100.0, True)])
        self.assertEqual(widget.selectedItems(), [])

    def test_1366_by_768_uses_low_height_profile(self):
        self.assertEqual(
            app.main_window_layout_profile(1366, 728, 728),
            ("medium", True),
        )
        self.assertEqual(
            app.main_window_layout_profile(1920, 1040, 1040),
            ("wide", False),
        )

    def test_each_catalog_item_keeps_its_category_color_role(self):
        expected = {
            "Medicamentos": "#0277bd",
            "Materiales": "#e65100",
            "Laboratorios": "#c62828",
            "Imágenes": "#6a1b9a",
            "Procedimientos": "#00695c",
            "Honorarios": "#e64a19",
        }
        for category, color in expected.items():
            widget = app.CatalogListWidget()
            owner = SimpleNamespace(
                source_lists={category: widget},
                catalog_favorites=set(),
                catalog_font_size=11,
            )
            with patch.object(app, "get_effective_price", return_value=100.0):
                app.MainWindow._fill_list(owner, widget, {"Elemento": 100.0}, "")
            self.assertEqual(widget.item(0).data(app.CATALOG_CATEGORY_ROLE), category)
            self.assertEqual(app.CAT_COLORS[category], color)


if __name__ == "__main__":
    unittest.main()
