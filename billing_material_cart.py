"""Add configured consumables once, retaining materials chosen by the operator."""

from billing_item_rules import MAX_MEDICATION_QUANTITY
from monthly_candidate_search import normalized_name


def mask_size(name):
    label = normalized_name(name)
    if "mascarilla" not in label:
        return ""
    if any(token in label for token in ("pediatr", "infantil", "nino")):
        return "pediatric"
    return "adult" if "adult" in label else ""


def material_matches_specialty(name, specialty):
    size = mask_size(name)
    expected = "pediatric" if "pediatr" in normalized_name(specialty) else "adult"
    return not size or size == expected


def specialty_materials(materials, specialty):
    matching = [
        item
        for item in materials
        if material_matches_specialty(item["name"], specialty)
    ]
    masks = [item for item in matching if "mascarilla" in normalized_name(item["name"])]
    selected = preferred_mask(masks)
    return [item for item in matching if item not in masks or item is selected]


def preferred_mask(masks):
    return next((item for item in masks if mask_size(item["name"])), None) or next(
        iter(masks), None
    )


def consumable_family(name):
    label = normalized_name(name)
    return next((family for family in ("mascarilla", "guante") if family in label), "")


class MedicationMaterialCart:
    def __init__(self, window, report_error):
        self.window = window
        self.report_error = report_error
        self.rules = {}
        self.allocations = {}
        self.manual_quantities = {}
        self._automatic = set()
        self._updating = False

    def reset(self):
        self.allocations.clear()
        self.manual_quantities.clear()
        self._automatic.clear()

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

    def _has_material(self, name):
        if self._quantity("Materiales", name) > 0:
            return True
        family = consumable_family(name)
        table = self.window.cart_table
        return bool(family) and any(
            table.item(row, 0).text().endswith("Materiales")
            and consumable_family(table.item(row, 1).text()) == family
            and self.window._cart_quantity_value(row) > 0
            for row in range(table.rowCount())
        )

    def prepare_add(self, name, quantity):
        if name not in self.rules:
            return False
        selected = self._available_materials(name)
        previous = self._quantity("Medicamentos", name)
        self.allocations.setdefault(
            name, {"baseline": previous, "quantity": previous, "materials": selected}
        )
        return True

    def _available_materials(self, medication):
        prices = self.window.universal.get("Materiales", {})
        selected = []
        for material in specialty_materials(self.rules[medication], self._specialty()):
            name = material["name"]
            if not material["active"] or name not in prices:
                raise ValueError(
                    f"Material vinculado no disponible: {name}. Revisa el catálogo."
                )
            selected.append(name)
        return selected

    def _specialty(self):
        attention = getattr(self.window, "current_admission_attention", None) or {}
        return (
            attention.get("specialty")
            or attention.get("specialty_snapshot")
            or attention.get("especialidad")
            or attention.get("hoja")
            or getattr(self.window, "_editing_specialty_snapshot", "")
        )

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
            self.manual_quantities[name] = quantity
            self._automatic.discard(name)
        elif (
            table.item(row, 0).text().endswith("Medicamentos")
            and name in self.allocations
        ):
            self.allocations[name]["quantity"] = min(MAX_MEDICATION_QUANTITY, quantity)
            self._reconcile()
        return quantity

    def removing(self, row):
        table = self.window.cart_table
        name = table.item(row, 1).text()
        if table.item(row, 0).text().endswith("Materiales"):
            self.manual_quantities.pop(name, None)
            self._automatic.discard(name)
        elif table.item(row, 0).text().endswith("Medicamentos"):
            self.allocations.pop(name, None)
            self._reconcile()

    def _reconcile(self):
        required = {
            name
            for allocation in self.allocations.values()
            if allocation["quantity"] > allocation["baseline"]
            for name in allocation["materials"]
        }
        self._updating = True
        try:
            for name in self._automatic - required:
                row = self.row("Materiales", name)
                if row >= 0:
                    self.window.cart_table.removeRow(row)
            self._automatic.intersection_update(required)
            for name in sorted(required):
                if not self._has_material(name):
                    price = self.window.universal["Materiales"][name]
                    self.window.insert_or_increment_cart_item(
                        "Materiales", name, price, 1
                    )
                    self._automatic.add(name)
        finally:
            self._updating = False
