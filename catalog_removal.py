"""Remove catalogue definitions without rewriting historical receipt lines."""

from billing_item_rules import integer_quantity


def _catalog_ids(values):
    return sorted({integer_quantity(value, maximum=2_147_483_647) for value in values})


def delete_catalog_item(connection, category, name):
    row = connection.execute(
        "SELECT id FROM universal_items WHERE categoria=%s AND nombre=%s FOR UPDATE",
        (category, name),
    ).fetchone()
    if row is None:
        raise ValueError("El elemento ya no existe. Actualiza el catálogo.")
    identity = row["id"]
    delete_catalog_items(
        connection, category, [identity], confirmed_active_ids=[identity]
    )


def delete_catalog_items(connection, category, item_ids, *, confirmed_active_ids=()):
    ids = _catalog_ids(item_ids)
    if not ids:
        return []
    confirmed = set(_catalog_ids(confirmed_active_ids))
    rows = connection.execute(
        """SELECT id,nombre,is_active FROM universal_items
           WHERE categoria=%s AND id=ANY(%s) ORDER BY id FOR UPDATE""",
        (category, ids),
    ).fetchall()
    if len(rows) != len(ids):
        raise ValueError(
            "La selección cambió en el catálogo central. Actualízalo y vuelve a seleccionar."
        )
    if any(row["is_active"] and row["id"] not in confirmed for row in rows):
        raise ValueError(
            "La selección incluye un ítem activo sin confirmar. Actualiza el catálogo y revísala."
        )

    connection.execute(
        "DELETE FROM medication_material_links WHERE medication_id=ANY(%s) OR material_id=ANY(%s)",
        (ids, ids),
    )
    deleted = connection.execute(
        "DELETE FROM universal_items WHERE categoria=%s AND id=ANY(%s) RETURNING id",
        (category, ids),
    ).fetchall()
    if len(deleted) != len(rows):
        raise RuntimeError(
            "La base de datos no completó el borrado. La selección se conservó."
        )
    connection.execute(
        "UPDATE billing_pricing_settings SET version=version+1,updated_at=NOW() "
        "WHERE setting_key='medication_markup_percent'"
    )
    return rows
