"""Transactional insurer corrections and complete tariff plans."""

from decimal import Decimal

from billing_money import line_total, sum_money


def reprice_items(items, catalogs, effective_price):
    """Resolve every item before returning a plan; never partially reprice."""
    prices = {
        category: {name.strip().casefold(): price for name, price in values.items()}
        for category, values in catalogs.items()
    }
    result = []
    for category, name, _price, quantity, _subtotal, _ars in items:
        price = prices.get(category, {}).get(name.strip().casefold())
        if price is None:
            raise ValueError(
                f"El ítem «{name}» ({category}) no existe en la nueva tarifa."
            )
        if int(quantity) < 1:
            raise ValueError("La cantidad debe ser mayor que cero.")
        unit = Decimal(str(effective_price(category, price)))
        if not unit.is_finite() or unit < 0:
            raise ValueError("El catálogo contiene un precio inválido.")
        unit = unit.quantize(Decimal("0.01"))
        result.append(
            (
                category,
                name,
                float(unit),
                int(quantity),
                float(line_total(unit, quantity)),
            )
        )
    return result


def require_current_tariff(
    connection, ars, room, items, total, service_type, effective_price
):
    tariff = connection.execute(
        "SELECT sala_emergencia,consulta_price FROM ars WHERE nombre=%s AND is_active=1 FOR SHARE",
        (ars,),
    ).fetchone()
    if tariff is None:
        raise ValueError("La nueva ARS no tiene una tarifa activa.")
    catalogs: dict[str, dict[str, float]] = {}
    rows = connection.execute(
        """SELECT ai.categoria,ai.nombre,ai.precio FROM ars_items ai
           JOIN ars a ON a.id=ai.ars_id WHERE a.nombre=%s AND a.is_active=1 AND ai.is_active=1
           UNION ALL SELECT categoria,nombre,precio FROM universal_items WHERE is_active=1""",
        (ars,),
    ).fetchall()
    for row in rows:
        catalogs.setdefault(row["categoria"], {})[row["nombre"]] = row["precio"]
    plan = reprice_items(items, catalogs, effective_price)
    expected_room = (
        tariff["consulta_price" if service_type == "CONSULTA" else "sala_emergencia"]
        or 0
    )
    expected_total = sum_money([expected_room, *(item[4] for item in plan)])
    supplied = [
        (item[0], item[1], float(item[2]), int(item[3]), float(item[4]))
        for item in items
    ]
    if (
        supplied != plan
        or Decimal(str(room)) != Decimal(str(expected_room))
        or Decimal(str(total)) != expected_total
    ):
        raise ValueError(
            "La tarifa cambió o los importes no corresponden. Vuelva a seleccionar la ARS para recalcular."
        )


def correct_linked_insurer(connection, previous, attention, ars, actor):
    """Update only this attention and its patient, using the receipt transaction."""
    from admission_demographics import _correct_snapshot

    row = _locked_linked_attention(connection, previous, attention)
    from admission_contract import canonicalize_ars

    if canonicalize_ars(ars) != ars:
        raise ValueError("La nueva ARS necesita una equivalencia aprobada en Admisión.")
    patient = _correct_patient(connection, row, ars, actor)
    patch = {
        "global_patient_id": patient["global_patient_id"],
        "server_revision": patient["server_revision"],
        "ars": ars,
        "nombre": row["patient_name"],
        "nss": row["nss_snapshot"],
        "cedula": row["cedula_snapshot"],
    }
    _correct_snapshot(connection, dict(row), patch, patient["updated_at"])
    return connection.execute(
        "SELECT canonical_ars,snapshot_hash,source_updated_at,coverage_status,readiness FROM admission_attention_projection WHERE global_attention_id=%s::UUID",
        (str(row["global_attention_id"]),),
    ).fetchone()


def _locked_linked_attention(connection, previous, attention):
    _require_matching_identity(previous, attention)
    global_id = str(previous.get("admission_global_attention_id") or "")
    row = connection.execute(
        """SELECT * FROM admission_attention_projection
           WHERE ((%s<>'' AND global_attention_id::TEXT=%s)
             OR (%s='' AND attention_id=%s AND source_instance_id=%s)) FOR UPDATE""",
        (
            global_id,
            global_id,
            global_id,
            previous["admission_atencion_id"],
            previous["admission_source_instance_id"] or "LEGACY",
        ),
    ).fetchone()
    if not row or not row["global_patient_id"] or not row["global_attention_id"]:
        raise ValueError(
            "El vínculo carece de identidad central. Corrija primero la identidad en Admisión."
        )
    return row


def _require_matching_identity(previous, attention):
    global_id = str(previous.get("admission_global_attention_id") or "")
    if not attention:
        raise ValueError("Debe cargar la atención vinculada antes de corregir su ARS.")
    expected: tuple[object, ...]
    supplied: tuple[object, ...]
    if global_id:
        expected = (global_id,)
        supplied = (str(attention.get("global_attention_id") or ""),)
    else:
        expected = (
            previous["admission_atencion_id"],
            previous.get("admission_source_instance_id") or "LEGACY",
        )
        supplied = (
            attention.get("attention_id"),
            attention.get("source_instance_id") or "LEGACY",
        )
    if expected != supplied:
        raise ValueError(
            "La atención y su estación de origen no corresponden al recibo."
        )


def _correct_patient(connection, attention, ars, actor):
    from patient_directory import CentralPatientDirectoryRepository

    patient_id = str(attention["global_patient_id"])
    current = connection.execute(
        "SELECT global_patient_id,canonical_ars,is_deleted FROM admission_patient_directory WHERE global_patient_id=%s::UUID FOR UPDATE",
        (patient_id,),
    ).fetchone()
    if not current or current["is_deleted"]:
        raise ValueError("El paciente ya no está disponible en Admisión.")
    if str(current["canonical_ars"] or "").casefold() not in (
        str(attention["canonical_ars"] or "").casefold(),
        ars.casefold(),
    ):
        raise ValueError(
            "La ARS del paciente cambió en otra estación. Recargue Admisión antes de corregir."
        )
    updated = connection.execute(
        """UPDATE admission_patient_directory SET canonical_ars=%s,
           server_revision=server_revision+1,updated_at=NOW()
           WHERE global_patient_id=%s::UUID RETURNING *""",
        (ars, patient_id),
    ).fetchone()
    CentralPatientDirectoryRepository._insert_event(
        connection,
        dict(updated),
        "PATIENT_UPDATED",
        audit={
            "operation": "RECEIPT_ARS_CORRECTION",
            "actor_user": actor["username"],
            "actor_role": actor["role"],
            "fields_changed": ["canonical_ars"],
        },
    )
    return {
        "global_patient_id": patient_id,
        "server_revision": updated["server_revision"],
        "updated_at": updated["updated_at"],
    }


def reprice_cart(form, runtime, effective_price):
    from PySide6.QtCore import Qt

    items = []
    for index in range(form.cart_table.rowCount()):
        category = form.cart_table.item(index, 0).text().split(" ")[-1]
        name = form.cart_table.item(index, 1).text()
        items.append((category, name, 0, form._cart_quantity_value(index), 0, ""))
    catalogs = {**form.universal, **runtime["catalogs"]}
    plan = reprice_items(items, catalogs, effective_price)
    for index, item in enumerate(plan):
        for column, value in ((3, item[2]), (4, item[4])):
            cell = form.cart_table.item(index, column)
            cell.setText(f"${value:,.2f}")
            cell.setData(Qt.ItemDataRole.UserRole, value)
    form.locked_ars = form.current_ars if form.cart_has_ars_items() else None
