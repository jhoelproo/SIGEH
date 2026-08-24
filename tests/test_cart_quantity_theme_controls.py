import os
import unittest
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

import CALCULOS_QT as app


class CartQuantityAndThemeControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def _quantity_owner(self, profile):
        owner = SimpleNamespace(
            _responsive_mode=profile,
            is_dark_mode=False,
            cart_table=app.ReceiptItemsTable(1, 6),
        )
        owner._cart_quantity_metrics = MethodType(
            app.MainWindow._cart_quantity_metrics, owner
        )
        owner._cart_quantity_arrow_icon = MethodType(
            app.MainWindow._cart_quantity_arrow_icon, owner
        )
        owner._cart_quantity_changed = lambda *_args, **_kwargs: None
        return owner

    def test_quantity_arrows_have_identical_geometry_in_every_profile(self):
        for profile in (
            app.PROFILE_VERY_COMPACT,
            app.PROFILE_COMPACT,
            app.PROFILE_STANDARD,
            app.PROFILE_WIDE,
        ):
            owner = self._quantity_owner(profile)
            app.MainWindow._install_cart_quantity_editor(owner, 0, 1)
            holder = owner.cart_table.cellWidget(0, 2)
            up = holder.findChild(app.QToolButton, "CartQuantityUp")
            down = holder.findChild(app.QToolButton, "CartQuantityDown")
            self.assertIsNotNone(up, profile)
            self.assertIsNotNone(down, profile)
            self.assertEqual(up.size(), down.size(), profile)
            self.assertEqual(up.iconSize(), down.iconSize(), profile)
            self.assertGreater(up.width(), 0, profile)
            self.assertGreater(up.height(), 0, profile)

    def test_theme_and_settings_restore_the_approved_emoji_controls(self):
        owner = SimpleNamespace(
            is_dark_mode=False,
            btn_theme_toggle=QPushButton(),
            btn_preferences=QPushButton(),
            btn_advanced=None,
        )
        app.MainWindow._restore_billing_original_icons(owner)
        self.assertEqual(owner.btn_theme_toggle.text(), "🌙")
        self.assertEqual(owner.btn_preferences.text(), "⚙️")
        self.assertTrue(owner.btn_theme_toggle.icon().isNull())
        self.assertTrue(owner.btn_preferences.icon().isNull())

        owner.is_dark_mode = True
        app.MainWindow._restore_billing_original_icons(owner)
        self.assertEqual(owner.btn_theme_toggle.text(), "☀️")
        self.assertEqual(owner.btn_preferences.text(), "⚙️")


if __name__ == "__main__":
    unittest.main()
