"""Remove catalogue definitions without rewriting historical receipt lines."""


def delete_catalog_item(connection, category, name):
    row = connection.execute(
        "SELECT id FROM universal_items WHERE categoria=%s AND nombre=%s FOR UPDATE",
        (category, name),
    ).fetchone()
    if row is None:
        raise ValueError("El elemento ya no existe. Actualiza el catálogo.")
    identity = row["id"]
    connection.execute(
        "DELETE FROM medication_material_links WHERE medication_id=%s OR material_id=%s",
        (identity, identity),
    )
    connection.execute("DELETE FROM universal_items WHERE id=%s", (identity,))
    connection.execute(
        "UPDATE billing_pricing_settings SET version=version+1,updated_at=NOW() "
        "WHERE setting_key='medication_markup_percent'"
    )
