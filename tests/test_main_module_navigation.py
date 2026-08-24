import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import CALCULOS_QT as app


class MainModuleNavigationTests(unittest.TestCase):
    def test_auxiliary_starts_with_emergencies_then_billing(self):
        user = {"username": "aux", "role": app.ROLE_AUX}
        self.assertEqual(
            app.main_module_labels_for_user(user),
            ("Emergencias", "Facturación"),
        )

    def test_only_admin_sees_and_manages_ars_lists(self):
        admin = {"username": "admin", "role": app.ROLE_ADMIN}
        medical_audit = {
            "username": "auditoria",
            "role": app.ROLE_MEDICAL_AUDIT,
        }
        self.assertEqual(
            app.main_module_labels_for_user(admin),
            ("Emergencias", "Facturación", "Listados de ARS"),
        )
        self.assertNotIn(
            "Listados de ARS",
            app.main_module_labels_for_user(medical_audit),
        )
        self.assertFalse(
            app.user_has_permission(
                medical_audit,
                app.PERMISSION_MANAGE_BILLING_LISTS,
            )
        )

    def test_audit_biller_has_no_emergency_module(self):
        labels = app.main_module_labels_for_user(
            {"username": "audit", "role": app.ROLE_AUDIT}
        )
        self.assertEqual(labels, ("Facturación", "Listados de ARS"))

    def test_enter_catalog_action_is_ignored_outside_billing(self):
        tabs = Mock()
        tabs.currentIndex.return_value = 2
        owner = SimpleNamespace(
            billing_module_index=1,
            module_tabs=tabs,
            get_current_category=Mock(),
        )

        app.MainWindow.add_first_search_result(owner)

        owner.get_current_category.assert_not_called()

    def test_direct_add_is_ignored_outside_billing(self):
        tabs = Mock()
        tabs.currentIndex.return_value = 2
        owner = SimpleNamespace(
            billing_module_index=1,
            module_tabs=tabs,
            mark_activity=Mock(),
        )

        app.MainWindow.add_selected_item(owner)

        owner.mark_activity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
