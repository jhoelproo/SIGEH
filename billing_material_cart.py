"""Track only consumables added by this draft, preserving manual quantities."""

from PySide6.QtCore import QSignalBlocker

from billing_item_rules import (
    MAX_MATERIAL_QUANTITY,
    MAX_MEDICATION_QUANTITY,
    MaterialAllocation,
)


class MedicationMaterialCart:
    def __init__(self, window, report_error):
        self.window = window
        self.report_error = report_error
        self.rules = {}
        self.allocations = {}
        self.manual_quantities = {}
        self._updating = False

    def reset(self):
        self.allocations.clear()
        self.manual_quantities.clear()

    def row(self, category, name):
        table = self.window.cart_table
        return next(
            (
                index
                for index in range(table.rowCount())
                if table.item(index, 1).text() == name
                and table.item(index, 0).text().endswith(category)
            ),
            -1,
        )

    def _quantity(self, category, name):
        row = self.row(category, name)
        return self.window._cart_quantity_value(row) if row >= 0 else 0

    def prepare_add(self, name, quantity):
        if name not in self.rules:
            return False
        selected = self._available_materials(name)
        previous = self._quantity("Medicamentos", name)
        allocation = self.allocations.get(name) or MaterialAllocation(
            selected, baseline=previous
        )
        target = min(MAX_MEDICATION_QUANTITY, previous + quantity)
        self._validate(allocation.preview(target))
        self.allocations[name] = allocation
        for material in allocation.materials:
            self.manual_quantities.setdefault(
                material, self._quantity("Materiales", material)
            )
        return True

    def _available_materials(self, medication):
        prices = self.window.universal.get("Materiales", {})
        available = {}
        for material in self.rules[medication]:
            name = material["name"]
            if material["active"] and name in prices:
                available[name] = material["units"]
            else:
                raise ValueError(
                    f"Material vinculado no disponible: {name}. Revisa el catálogo."
                )
        return available

    def finish_add(self, name):
        row = self.row("Medicamentos", name)
        if row >= 0:
            self.quantity_changed(row, self.window._cart_quantity_value(row))

    def quantity_changed(self, row, quantity):
        if self._updating:
            return quantity
        table = self.window.cart_table
        name = table.item(row, 1).text()
        if table.item(row, 0).text().endswith("Materiales"):
            self._manual_quantity_changed(row, name, quantity)
            return quantity
        if not table.item(row, 0).text().endswith("Medicamentos"):
            return quantity
        allocation = self.allocations.get(name)
        if allocation is None:
            return quantity
        try:
            self._validate(allocation.preview(quantity))
        except ValueError as error:
            self.report_error(str(error))
            self._set_quantity(row, allocation.quantity)
            return allocation.quantity
        self._apply(allocation.change(quantity))
        return quantity

    def _manual_quantity_changed(self, row, name, quantity):
        if name in self.manual_quantities:
            previous = int(self.window.cart_table.item(row, 2).text())
            self.manual_quantities[name] = max(
                0, self.manual_quantities[name] + quantity - previous
            )

    def removing(self, row):
        table = self.window.cart_table
        name = table.item(row, 1).text()
        if table.item(row, 0).text().endswith("Materiales"):
            self.manual_quantities.pop(name, None)
            return
        if not table.item(row, 0).text().endswith("Medicamentos"):
            return
        allocation = self.allocations.pop(name, None)
        if allocation is not None:
            self._apply(allocation.change(0))

    def _validate(self, deltas):
        for material, quantity in deltas.items():
            if (
                self._quantity("Materiales", material) + quantity
                > MAX_MATERIAL_QUANTITY
            ):
                raise ValueError(
                    "La cantidad de materiales supera el máximo del recibo."
                )

    def _set_quantity(self, row, quantity):
        editor = self.window._cart_quantity_editor(row)
        blocker = QSignalBlocker(editor)
        editor.setValue(quantity)
        del blocker

    def _apply(self, deltas):
        self._updating = True
        try:
            for name, delta in deltas.items():
                self._apply_material(name, delta)
        finally:
            self._updating = False

    def _apply_material(self, name, delta):
        row = self.row("Materiales", name)
        if delta > 0:
            self.manual_quantities.setdefault(name, self._quantity("Materiales", name))
            price = self.window.universal["Materiales"][name]
            self.window.insert_or_increment_cart_item("Materiales", name, price, delta)
        elif row >= 0:
            quantity = max(
                self.manual_quantities.get(name, 0),
                self._quantity("Materiales", name) + delta,
            )
            if quantity <= 0:
                self.window.cart_table.removeRow(row)
            else:
                self._set_quantity(row, quantity)
                self.window._cart_quantity_changed(
                    self.window._cart_quantity_editor(row), quantity, recalculate=False
                )
