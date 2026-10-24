"""Transactional configuration of specialty fees and medication consumables."""

import json

from billing_item_rules import (
    honorarium_allowed,
    integer_quantity,
    normalize_ars_policies,
    normalize_material_quantities,
)
from billing_money import money

SCHEMA_SQL = """
ALTER TABLE ars ADD COLUMN IF NOT EXISTS honorarium_specialties
    JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(honorarium_specialties)='object');
ALTER TABLE universal_items ADD COLUMN IF NOT EXISTS materials_configured
    BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS medication_material_links(
    medication_id INTEGER NOT NULL REFERENCES universal_items(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL REFERENCES universal_items(id) ON DELETE RESTRICT,
    units INTEGER NOT NULL CHECK(units BETWEEN 1 AND 1000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT NOT NULL,
    PRIMARY KEY(medication_id,material_id),
    CHECK(medication_id<>material_id)
);
CREATE INDEX IF NOT EXISTS medication_material_links_material_idx
    ON medication_material_links(material_id);
ALTER TABLE medication_material_links ENABLE ROW LEVEL SECURITY;
"""


def _administrator(con, actor, allowed):
    row = con.execute(
        "SELECT id,username,role,is_active,permission_overrides FROM users WHERE username=%s FOR SHARE",
        (str(actor.get("username") or ""),),
    ).fetchone()
    user = dict(row or {})
    if not user or int(user.get("is_active", 0)) != 1 or not allowed(user):
        raise PermissionError(
            "Solo el administrador puede configurar honorarios y materiales."
        )
    return user


def save_honorarium_policies(con, changes, actor, *, allowed, audit):
    user = _administrator(con, actor, allowed)
    normalized = normalize_ars_policies(changes)
    rows = con.execute(
        "SELECT id,honorarium_specialties FROM ars WHERE id=ANY(%s) AND is_active=1 ORDER BY id FOR UPDATE",
        (sorted(normalized),),
    ).fetchall()
    if len(rows) != len(normalized):
        raise ValueError(
            "Una de las ARS ya no está disponible. Recarga la configuración."
        )
    for row in rows:
        policy = normalized[int(row["id"])]
        previous = dict(row["honorarium_specialties"] or {})
        if previous == policy:
            continue
        con.execute(
            "UPDATE ars SET honorarium_specialties=%s::jsonb,honorarium_prompt_updated_at=NOW(),honorarium_prompt_updated_by=%s WHERE id=%s",
            (json.dumps(policy, ensure_ascii=False), user["username"], row["id"]),
        )
        audit(
            con,
            user,
            "ARS_SPECIALTY_HONORARIUM_CHANGED",
            "ars",
            row["id"],
            {"previous": previous, "current": policy},
        )


def load_material_editor(con, medication_id):
    medication = con.execute(
        "SELECT id,nombre,materials_configured FROM universal_items WHERE id=%s AND categoria='Medicamentos'",
        (medication_id,),
    ).fetchone()
    if not medication:
        raise ValueError("El medicamento ya no existe.")
    materials = con.execute(
        """SELECT m.id,m.nombre,m.precio,m.is_active,COALESCE(l.units,0) AS units
           FROM universal_items m LEFT JOIN medication_material_links l
             ON l.material_id=m.id AND l.medication_id=%s
           WHERE m.categoria='Materiales' AND (m.is_active=1 OR l.material_id IS NOT NULL)
           ORDER BY m.nombre""",
        (medication_id,),
    ).fetchall()
    return {
        "medication": dict(medication),
        "materials": [dict(row) for row in materials],
    }


def save_material_links(con, medication_id, quantities, actor, *, allowed, audit):
    user = _administrator(con, actor, allowed)
    medication_id = integer_quantity(medication_id, maximum=2_147_483_647)
    quantities = normalize_material_quantities(quantities)
    medication = con.execute(
        "SELECT id FROM universal_items WHERE id=%s AND categoria='Medicamentos' FOR UPDATE",
        (medication_id,),
    ).fetchone()
    if not medication:
        raise ValueError("El medicamento ya no existe.")
    materials = con.execute(
        "SELECT id FROM universal_items WHERE id=ANY(%s) AND categoria='Materiales' AND is_active=1 FOR SHARE",
        (sorted(quantities),),
    ).fetchall()
    if len(materials) != len(quantities):
        raise ValueError(
            "Hay materiales inexistentes o inactivos. Actualiza la selección."
        )
    previous = con.execute(
        "SELECT material_id,units FROM medication_material_links WHERE medication_id=%s ORDER BY material_id",
        (medication_id,),
    ).fetchall()
    con.execute(
        "DELETE FROM medication_material_links WHERE medication_id=%s", (medication_id,)
    )
    if quantities:
        con.execute(
            """INSERT INTO medication_material_links(medication_id,material_id,units,updated_by)
               SELECT %s,material_id,units,%s FROM unnest(%s::int[],%s::int[]) AS t(material_id,units)""",
            (
                medication_id,
                user["username"],
                list(quantities),
                list(quantities.values()),
            ),
        )
    con.execute(
        "UPDATE universal_items SET materials_configured=TRUE WHERE id=%s",
        (medication_id,),
    )
    con.execute(
        "UPDATE billing_pricing_settings SET version=version+1,updated_at=NOW(),updated_by=%s WHERE setting_key='medication_markup_percent'",
        (user["username"],),
    )
    audit(
        con,
        user,
        "MEDICATION_MATERIAL_LINKS_CHANGED",
        "universal_items",
        medication_id,
        {"previous": [dict(row) for row in previous], "current": quantities},
    )


def load_material_rules(con):
    rows = con.execute(
        """SELECT d.nombre AS medication,m.id AS material_id,m.nombre AS material,
                  m.is_active,l.units
           FROM universal_items d LEFT JOIN medication_material_links l ON l.medication_id=d.id
           LEFT JOIN universal_items m ON m.id=l.material_id
           WHERE d.categoria='Medicamentos' AND d.is_active=1 AND d.materials_configured
           ORDER BY d.id,m.id"""
    ).fetchall()
    links = {}
    for row in rows:
        materials = links.setdefault(row["medication"], [])
        if row["material_id"] is not None:
            materials.append(
                {
                    "id": int(row["material_id"]),
                    "name": row["material"],
                    "units": int(row["units"]),
                    "active": bool(row["is_active"]),
                }
            )
    version = con.execute(
        "SELECT version FROM billing_pricing_settings WHERE setting_key='medication_markup_percent'"
    ).fetchone()
    return {"links": links, "version": int(version["version"] if version else 0)}


def validate_honorarium_charge(con, ars, specialty, items, receipt_id=None):
    fees = [row for row in items if row[0] == "Honorarios"]
    if not ars or not fees:
        return
    specialty = _receipt_specialty(con, receipt_id, specialty)
    row = con.execute(
        "SELECT COALESCE(to_jsonb(a)->'honorarium_specialties','{}'::jsonb) AS policy FROM ars a WHERE nombre=%s FOR SHARE",
        (ars,),
    ).fetchone()
    if not row or honorarium_allowed(row["policy"], specialty):
        return
    if receipt_id and _unchanged_historical_fees(con, receipt_id, ars, specialty, fees):
        return
    raise ValueError(
        "Esta ARS no cobra honorarios para la especialidad seleccionada. Retira los honorarios del recibo."
    )


def _receipt_specialty(con, receipt_id, specialty):
    if receipt_id and not specialty:
        current = con.execute(
            "SELECT specialty_snapshot FROM recibos WHERE id=%s FOR UPDATE",
            (receipt_id,),
        ).fetchone()
        specialty = current["specialty_snapshot"] if current else ""
    return specialty


def _unchanged_historical_fees(con, receipt_id, ars, specialty, fees):
    from billing_item_rules import canonical_specialty

    receipt = con.execute(
        "SELECT ars,specialty_snapshot FROM recibos WHERE id=%s FOR UPDATE",
        (receipt_id,),
    ).fetchone()
    if (
        not receipt
        or receipt["ars"] != ars
        or canonical_specialty(receipt["specialty_snapshot"])
        != canonical_specialty(specialty)
    ):
        return False
    previous = con.execute(
        "SELECT nombre,precio_unit,cantidad,total FROM recibo_items WHERE recibo_id=%s AND categoria='Honorarios'",
        (receipt_id,),
    ).fetchall()
    old = _fee_limits(
        (row["nombre"], row["precio_unit"], row["cantidad"], row["total"])
        for row in previous
    )
    new = _fee_limits((row[1], row[2], row[3], row[4]) for row in fees)
    return all(
        name in old and all(value <= limit for value, limit in zip(values, old[name]))
        for name, values in new.items()
    )


def _fee_limits(items):
    limits = {}
    for name, price, quantity, total in items:
        old = limits.get(name, (money(0), 0, money(0)))
        limits[name] = (
            max(old[0], money(price)),
            old[1] + int(quantity),
            old[2] + money(total),
        )
    return limits
