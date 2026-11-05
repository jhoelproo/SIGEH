"""Merge equivalent billing insurers under Admission's canonical name."""

from contextlib import nullcontext
import hashlib
import json

from admission_contract import canonicalize_ars, normalize_key
from billing_item_rule_store import _administrator


def _insurer_enabled(row):
    return bool(row.get("is_active", 1) and row.get("billing_enabled", True))


def _canonical_names(names):
    return {canonicalize_ars(name) or name for name in names}


def _active_insurer_names(rows, selectable):
    return {
        str(row["nombre"] or "").strip()
        for row in rows
        if _insurer_enabled(row) and selectable(row["nombre"])
    }


def selectable_insurer_names(rows, admission_names, selectable):
    active = _active_insurer_names(rows, selectable)
    disabled = _canonical_names(
        row["nombre"] for row in rows if not _insurer_enabled(row)
    )
    disabled -= _canonical_names(active)
    active.update(
        name for name in admission_names if selectable(name) and name not in disabled
    )
    return sorted(active, key=str.casefold)


def merge_preview(con, source, destination):
    source, destination = str(source).strip(), str(destination).strip()
    if (
        not source
        or source == destination
        or canonicalize_ars(destination) != destination
    ):
        raise ValueError(
            "Selecciona un origen y un nombre principal válido de Admisión."
        )
    family = canonicalize_ars(source)
    if family and family != destination:
        raise ValueError("Las ARS seleccionadas no son equivalentes en Admisión.")
    rows = con.execute(
        "SELECT * FROM ars WHERE nombre IN (%s,%s) ORDER BY id FOR UPDATE",
        (source, destination),
    ).fetchall()
    by_name = {row["nombre"]: dict(row) for row in rows}
    if source not in by_name:
        raise ValueError("La ARS de origen ya no existe.")
    catalogs = [
        dict(row)
        for row in con.execute(
            "SELECT ai.* FROM ars_items ai JOIN ars a ON a.id=ai.ars_id "
            "WHERE a.nombre IN (%s,%s) ORDER BY ai.id FOR UPDATE OF ai",
            (source, destination),
        ).fetchall()
    ]
    token = hashlib.sha256(
        json.dumps([by_name, catalogs], sort_keys=True, default=str).encode()
    ).hexdigest()
    return {
        "source": source,
        "destination": destination,
        "insurers": by_name,
        "catalogs": catalogs,
        "token": token,
    }


def merge_equivalent_ars(
    con, source, destination, actor, *, allowed, audit, expected_token
):
    user = _administrator(con, actor, allowed)
    con.execute("SET LOCAL lock_timeout='5s'")
    preview = merge_preview(con, source, destination)
    if preview["token"] != expected_token:
        raise ValueError("Las tarifas cambiaron. Revisa nuevamente la fusión.")
    original = preview["insurers"][source]
    target = preview["insurers"].get(destination)
    if target is None:
        con.execute(
            "UPDATE ars SET nombre=%s,is_active=1 WHERE id=%s",
            (destination, original["id"]),
        )
    else:
        _combine_catalogs(con, original["id"], target["id"])
        con.execute(
            "UPDATE ars SET is_active=1,billing_enabled=TRUE WHERE id=%s",
            (target["id"],),
        )
        con.execute(
            "UPDATE ars SET is_active=0,billing_enabled=FALSE WHERE id=%s",
            (original["id"],),
        )
        _merge_scoped_recommendations(con, original["id"], target["id"], user, audit)
    _copy_profile(con, source, destination)
    changed = con.execute(
        "UPDATE recibos SET ars=%s,revision_version=revision_version+1 WHERE ars=%s RETURNING id",
        (destination, source),
    ).fetchall()
    con.execute("UPDATE recibo_items SET ars=%s WHERE ars=%s", (destination, source))
    con.execute(
        """INSERT INTO admission_ars_crosswalk(source_value_key,source_value,canonical_ars,is_active,updated_at,updated_by)
           VALUES(%s,%s,%s,1,NOW()::TEXT,%s) ON CONFLICT(source_value_key) DO UPDATE SET
           canonical_ars=EXCLUDED.canonical_ars,is_active=1,updated_at=EXCLUDED.updated_at,updated_by=EXCLUDED.updated_by""",
        (normalize_key(source), source, destination, user["username"]),
    )
    con.execute(
        "UPDATE billing_pricing_settings SET version=version+1,updated_at=NOW() WHERE setting_key='medication_markup_percent'"
    )
    result = {"source": source, "destination": destination, "receipts": len(changed)}
    audit(con, user, "ARS_EQUIVALENTS_MERGED", "ars", original["id"], result)
    return result


def _combine_catalogs(con, source_id, destination_id):
    con.execute(
        """INSERT INTO ars_items(ars_id,categoria,nombre,precio,is_active)
           SELECT %s,categoria,nombre,precio,is_active FROM ars_items WHERE ars_id=%s
           ON CONFLICT(ars_id,categoria,nombre) DO NOTHING""",
        (destination_id, source_id),
    )


def _merge_scoped_recommendations(con, source_id, destination_id, actor, audit):
    import diagnosis_recommendation_store as recommendations

    if not recommendations.available(con):
        return
    current = recommendations.load_config(con)
    rows = current.get("ars_rules", [])
    moved = [
        dict(row, ars_id=destination_id) for row in rows if row["ars_id"] == source_id
    ]
    if not moved:
        return
    kept = [row for row in rows if row["ars_id"] != source_id]

    def identity(row):
        return tuple(
            row[key] for key in ("ars_id", "diagnosis", "specialty", "category", "item")
        )

    known = {identity(row) for row in kept}
    combined = kept + [row for row in moved if identity(row) not in known]
    recommendations.save_config(
        con, dict(current, ars_rules=combined), actor, current["version"], audit=audit
    )


def _copy_profile(con, source, destination):
    con.execute(
        """INSERT INTO billing_ars_profiles(ars,display_name,ars_rnc,ars_address,ars_phone,ars_email,
           administrative_notes,provider_code,provider_name,provider_rnc,director_name,director_title,
           service_description,specialty_default,updated_at,updated_by)
           SELECT %s,%s,ars_rnc,ars_address,ars_phone,ars_email,administrative_notes,
           provider_code,provider_name,provider_rnc,director_name,director_title,
           service_description,specialty_default,updated_at,updated_by FROM billing_ars_profiles WHERE ars=%s
           ON CONFLICT(ars) DO NOTHING""",
        (destination, destination, source),
    )


def current_insurer_counts(con):
    rows = con.execute(
        "SELECT canonical_ars,COUNT(*) AS cantidad FROM admission_patient_directory "
        "WHERE NOT is_deleted AND COALESCE(canonical_ars,'')<>'' GROUP BY canonical_ars "
        "ORDER BY cantidad DESC,canonical_ars"
    ).fetchall()
    return [(row["canonical_ars"], int(row["cantidad"])) for row in rows]


def replace_current_insurer(con, source, destination, actor, *, allowed):
    from patient_directory import CentralPatientDirectoryRepository

    user = _administrator(con, actor, allowed)
    destination = canonicalize_ars(destination) or str(destination).strip().upper()
    if not destination:
        raise ValueError("Selecciona la nueva ARS.")
    rows = con.execute(
        "SELECT global_patient_id,server_revision FROM admission_patient_directory "
        "WHERE NOT is_deleted AND canonical_ars=%s ORDER BY global_patient_id FOR UPDATE",
        (source,),
    ).fetchall()
    repository = CentralPatientDirectoryRepository(lambda: nullcontext(con))
    for row in rows:
        repository.update_patient(
            str(row["global_patient_id"]),
            {"canonical_ars": destination},
            expected_revision=row["server_revision"],
            actor_user=user["username"],
            actor_role=user["role"],
            propagate_recent=False,
        )
    return len(rows)
