"""Billing policies independent of the database and receipt widgets."""

from dataclasses import dataclass, field

from billing_specialties import SPECIALTIES
from monthly_candidate_search import normalized_name

MAX_MEDICATION_QUANTITY = 300
MAX_MATERIAL_UNITS = 1000
MAX_MATERIAL_QUANTITY = 1_000_000
_SPECIALTY_KEYS = {normalized_name(value): value for value in SPECIALTIES}
_SPECIALTY_KEYS["general"] = SPECIALTIES[0]


def canonical_specialty(value):
    key = normalized_name(str(value or "")) or "general"
    try:
        return _SPECIALTY_KEYS[key]
    except KeyError as error:
        raise ValueError(
            "Selecciona Emergenciología, Ginecología o Pediatría."
        ) from error


def normalize_honorarium_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("Configuración de honorarios inválida.")
    result = {}
    for key, value in policy.items():
        specialty = canonical_specialty(key)
        if not isinstance(value, bool):
            raise ValueError(
                "El cobro de honorarios debe estar activado o desactivado."
            )
        if specialty in result and result[specialty] != value:
            raise ValueError("La especialidad tiene configuraciones contradictorias.")
        result[specialty] = value
    return result


def honorarium_allowed(policy, specialty):
    if not policy:
        return True
    return normalize_honorarium_policy(policy).get(canonical_specialty(specialty), True)


def honorarium_policy_snapshot(row):
    return dict(row.get("honorarium_specialties") or {})


def normalize_ars_policies(changes):
    result = {}
    for key, policy in changes.items():
        identity = integer_quantity(key, maximum=2_147_483_647)
        if identity in result:
            raise ValueError("La ARS está duplicada.")
        result[identity] = normalize_honorarium_policy(policy)
    return result


def integer_quantity(value, *, minimum=1, maximum=MAX_MATERIAL_UNITS):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("La cantidad debe ser un número entero.")
    try:
        quantity = int(value)
    except ValueError as error:
        raise ValueError("La cantidad debe ser un número entero.") from error
    if not minimum <= quantity <= maximum:
        raise ValueError(f"La cantidad debe estar entre {minimum} y {maximum}.")
    return quantity


def normalize_material_quantities(quantities):
    if not isinstance(quantities, dict):
        raise ValueError("La relación de materiales debe ser un objeto.")
    result = {}
    for material, quantity in quantities.items():
        identity = integer_quantity(material, maximum=2_147_483_647)
        if identity in result:
            raise ValueError("El material está duplicado.")
        result[identity] = integer_quantity(quantity)
    return result


@dataclass
class MaterialAllocation:
    materials: dict[str, int]
    baseline: int = 0
    quantity: int = field(init=False)

    def __post_init__(self):
        self.quantity = integer_quantity(
            self.baseline, minimum=0, maximum=MAX_MEDICATION_QUANTITY
        )
        self.materials = {
            name: integer_quantity(units) for name, units in self.materials.items()
        }

    def preview(self, quantity):
        quantity = integer_quantity(
            quantity, minimum=0, maximum=MAX_MEDICATION_QUANTITY
        )
        previous = max(0, self.quantity - self.baseline)
        current = max(0, quantity - self.baseline)
        return {
            name: (current - previous) * units
            for name, units in self.materials.items()
            if current != previous
        }

    def change(self, quantity):
        changes = self.preview(quantity)
        self.quantity = int(quantity)
        return changes
