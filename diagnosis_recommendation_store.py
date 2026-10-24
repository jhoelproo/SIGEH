"""Idempotent receipt evidence and compact, versioned group synchronization."""

import json

from diagnosis_recommendations import (
    CATEGORIES,
    SPECIALTIES,
    MAX_DIAGNOSES,
    build_evidence,
    group_key,
    model_catalog,
    normalize_config,
    specialty_key,
    text_key,
)

LOCK_KEY = "sigeh-diagnosis-recommendations-v1"
BATCH_SIZE = 20
DELTA_LIMIT = 100
VALID_STATUSES = {"PENDIENTE", "FACTURADO"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def available(con):
    return bool(
        con.execute("SELECT to_regclass('sigeh_recommendations.settings')").fetchone()[
            0
        ]
    )


def load_config(con):
    row = con.execute(
        "SELECT config,version FROM sigeh_recommendations.settings WHERE singleton"
    ).fetchone()
    return {**normalize_config(dict(row["config"])), "version": int(row["version"])}


def _publish(con, rows):
    if not rows:
        return
    con.execute(
        """INSERT INTO sigeh_recommendations.changes(change_key,payload)
           SELECT change_key,payload FROM jsonb_to_recordset(%s::jsonb) AS r(change_key TEXT,payload JSONB)
           ON CONFLICT(change_key) DO UPDATE SET payload=EXCLUDED.payload,revision=EXCLUDED.revision
           WHERE sigeh_recommendations.changes.payload IS DISTINCT FROM EXCLUDED.payload""",
        (_json(rows),),
    )


def _administrator(con, actor):
    row = con.execute(
        "SELECT username,role,is_active FROM users WHERE username=%s FOR SHARE",
        (str(actor.get("username") or ""),),
    ).fetchone()
    if not row or row["role"] != "administrador" or row["is_active"] != 1:
        raise PermissionError("Solo el administrador puede configurar recomendaciones.")
    return row


def _validate_items(con, rules):
    requested = {(row["category"], text_key(row["item"])) for row in rules}
    if not requested:
        return
    rows = con.execute(
        """SELECT categoria,nombre FROM universal_items WHERE categoria='Medicamentos' AND is_active=1
           UNION SELECT categoria,nombre FROM ars_items WHERE categoria IN ('Laboratorios','Imágenes') AND is_active=1"""
    ).fetchall()
    known = {(row["categoria"], text_key(row["nombre"])) for row in rows}
    if not requested <= known:
        raise ValueError("Hay ítems inexistentes o inactivos; actualiza el catálogo.")


def save_config(con, value, actor, expected_version, *, audit):
    user = _administrator(con, actor)
    value = normalize_config(value)
    _validate_items(con, value["rules"])
    con.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (LOCK_KEY,))
    current = load_config(con)
    if current["version"] != expected_version:
        raise ValueError(
            "Otro administrador cambió la configuración. Actualiza antes de guardar."
        )
    version = expected_version + 1
    con.execute(
        "UPDATE sigeh_recommendations.settings SET config=%s::jsonb,version=%s WHERE singleton",
        (_json(value), version),
    )
    _publish(con, [{"change_key": "config", "payload": {**value, "version": version}}])
    con.execute(
        "INSERT INTO sigeh_recommendations.jobs(receipt_id) SELECT id FROM recibos ON CONFLICT(receipt_id) DO UPDATE SET touched_at=NOW()"
    )
    audit(
        con,
        user,
        "RECOMMENDATION_CONFIG_CHANGED",
        "recomendaciones",
        version,
        {"version": version},
    )
    return version


def admin_catalog(con, actor):
    _administrator(con, actor)
    rows = con.execute(
        """SELECT categoria,nombre FROM universal_items WHERE categoria='Medicamentos' AND is_active=1
           UNION SELECT categoria,nombre FROM ars_items WHERE categoria IN ('Laboratorios','Imágenes') AND is_active=1
           ORDER BY categoria,nombre"""
    ).fetchall()
    return [dict(row) for row in rows]


def _receipt_rows(con, identities):
    return con.execute(
        """SELECT r.id,r.dx,r.specialty_snapshot,r.estado_facturacion,r.is_deleted,
                  COALESCE((SELECT jsonb_agg(jsonb_build_object('category',i.categoria,'item',i.nombre))
                    FROM recibo_items i WHERE i.recibo_id=r.id AND i.categoria IN ('Medicamentos','Laboratorios','Imágenes')),'[]'::jsonb) AS items
           FROM recibos r WHERE r.id=ANY(%s)""",
        (identities,),
    ).fetchall()


def _observation(row, catalog):
    if row["is_deleted"] or row["estado_facturacion"] not in VALID_STATUSES:
        return None
    specialty = specialty_key(row["specialty_snapshot"])
    try:
        normalized = catalog.normalize(row["dx"], allow_fuzzy=False)
    except ValueError:
        return None
    diagnoses = _diagnosis_labels(catalog, normalized)
    if not specialty or not diagnoses:
        return None
    items = _receipt_items(row["items"])
    value = {
        "receipt_id": int(row["id"]),
        "diagnoses": diagnoses,
        "specialty": specialty,
        "status": row["estado_facturacion"],
        "items": items,
    }
    return value


def _receipt_items(items):
    return {
        category: sorted(
            {item["item"] for item in items if item["category"] == category}
        )
        for category in CATEGORIES
    }


def _diagnosis_labels(catalog, normalized):
    unique = {
        text_key(part): part for part in catalog.split(normalized) if text_key(part)
    }
    return dict(list(unique.items())[:MAX_DIAGNOSES])


def _groups(observations):
    return {
        (diagnosis, row["specialty"])
        for row in observations
        for diagnosis in row["diagnoses"]
    }


def _known_model(con, config, groups):
    keys = [
        "group:" + group_key(diagnosis, specialty) for diagnosis, specialty in groups
    ]
    rows = con.execute(
        "SELECT change_key,payload FROM sigeh_recommendations.changes WHERE change_key=ANY(%s)",
        (keys,),
    ).fetchall()
    return {
        "config": config,
        "groups": {
            row["change_key"][6:]: dict(row["payload"])
            for row in rows
            if row["payload"] is not None
        },
    }


def _replace_observations(con, identities, observations, evidence):
    con.execute(
        "DELETE FROM sigeh_recommendations.observations WHERE receipt_id=ANY(%s)",
        (identities,),
    )
    if not observations:
        return
    con.execute(
        """INSERT INTO sigeh_recommendations.observations(receipt_id,diagnoses,specialty,status)
           SELECT receipt_id,diagnoses,specialty,status FROM jsonb_to_recordset(%s::jsonb)
           AS r(receipt_id BIGINT,diagnoses JSONB,specialty TEXT,status TEXT)""",
        (_json(observations),),
    )
    con.execute(
        """INSERT INTO sigeh_recommendations.evidence(receipt_id,diagnosis,specialty,category,item,confirmed)
           SELECT receipt_id,diagnosis,specialty,category,item,confirmed FROM jsonb_to_recordset(%s::jsonb)
           AS r(receipt_id BIGINT,diagnosis TEXT,specialty TEXT,category TEXT,item TEXT,confirmed BOOLEAN)""",
        (_json(evidence),),
    )


def _group_totals(con, requested):
    return con.execute(
        """WITH requested AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) AS r(diagnosis TEXT,specialty TEXT))
           SELECT q.diagnosis,q.specialty,count(o.receipt_id) AS receipts,
                  max(o.diagnoses->>q.diagnosis) AS label
           FROM requested q LEFT JOIN sigeh_recommendations.observations o
             ON o.specialty=q.specialty AND o.diagnoses ? q.diagnosis
           GROUP BY q.diagnosis,q.specialty""",
        (_json(requested),),
    ).fetchall()


def _group_items(con, requested):
    return con.execute(
        """WITH requested AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) AS r(diagnosis TEXT,specialty TEXT))
           SELECT e.diagnosis,e.specialty,e.category,e.item,
                  count(*) FILTER(WHERE e.confirmed) AS uses,
                  count(*) FILTER(WHERE e.confirmed AND o.status='FACTURADO') AS invoiced,
                  count(*) FILTER(WHERE e.confirmed AND o.status='PENDIENTE') AS pending,
                  count(*) FILTER(WHERE NOT e.confirmed) AS candidates
           FROM requested q JOIN sigeh_recommendations.evidence e USING(diagnosis,specialty)
           JOIN sigeh_recommendations.observations o USING(receipt_id)
           GROUP BY e.diagnosis,e.specialty,e.category,e.item
           ORDER BY e.diagnosis,e.specialty,e.category,e.item""",
        (_json(requested),),
    ).fetchall()


def _publish_groups(con, groups):
    if not groups:
        return
    requested = [
        {"diagnosis": diagnosis, "specialty": specialty}
        for diagnosis, specialty in sorted(groups)
    ]
    totals = _group_totals(con, requested)
    items = {}
    for row in _group_items(con, requested):
        items.setdefault((row["diagnosis"], row["specialty"]), []).append(
            {
                key: row[key]
                for key in (
                    "category",
                    "item",
                    "uses",
                    "invoiced",
                    "pending",
                    "candidates",
                )
            }
        )
    values = []
    for row in totals:
        payload = {
            "diagnosis": row["label"],
            "specialty": row["specialty"],
            "receipts": row["receipts"],
            "items": items.get((row["diagnosis"], row["specialty"]), []),
        }
        values.append(
            {
                "change_key": "group:" + group_key(row["diagnosis"], row["specialty"]),
                "payload": payload if row["receipts"] else None,
            }
        )
    _publish(con, values)


def _check_interrupted(interrupted):
    if interrupted():
        raise InterruptedError("Procesamiento de recomendaciones cancelado.")


def _process_jobs(con, interrupted):
    rows = con.execute(
        "SELECT receipt_id FROM sigeh_recommendations.jobs ORDER BY touched_at DESC,receipt_id DESC LIMIT %s FOR UPDATE SKIP LOCKED",
        (BATCH_SIZE,),
    ).fetchall()
    identities = [int(row["receipt_id"]) for row in rows]
    if not identities:
        return False
    config = load_config(con)
    previous = con.execute(
        "SELECT receipt_id,diagnoses,specialty FROM sigeh_recommendations.observations WHERE receipt_id=ANY(%s)",
        (identities,),
    ).fetchall()
    catalog = model_catalog({"config": config})
    _check_interrupted(interrupted)
    current = [
        value
        for row in _receipt_rows(con, identities)
        if (value := _observation(row, catalog)) is not None
    ]
    groups = _groups(previous) | _groups(current)
    known = _known_model(con, config, groups)
    evidence = [
        {"receipt_id": row["receipt_id"], **item}
        for row in current
        for item in build_evidence(
            known, list(row["diagnoses"]), row["specialty"], row["items"]
        )
    ]
    _replace_observations(con, identities, current, evidence)
    _check_interrupted(interrupted)
    _publish_groups(con, groups)
    con.execute(
        "DELETE FROM sigeh_recommendations.jobs WHERE receipt_id=ANY(%s)", (identities,)
    )
    return True


def synchronize(con, cursor=0, *, generation=None, interrupted=lambda: False):
    con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    con.execute("SET LOCAL statement_timeout='5s'")
    if not available(con):
        return {"changes": [], "more": False, "installed": False}
    metadata = con.execute(
        "SELECT generation,(SELECT COALESCE(MAX(revision),0) FROM sigeh_recommendations.changes) AS revision FROM sigeh_recommendations.settings WHERE singleton"
    ).fetchone()
    current_generation = str(metadata["generation"])
    reset = (
        int(cursor) > int(metadata["revision"])
        or generation is not None
        and generation != current_generation
    )
    cursor = 0 if reset else max(0, int(cursor))
    acquired = con.execute(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (LOCK_KEY,)
    ).fetchone()[0]
    _check_interrupted(interrupted)
    processed = _process_jobs(con, interrupted) if acquired else False
    _check_interrupted(interrupted)
    rows = con.execute(
        "SELECT change_key,revision,payload FROM sigeh_recommendations.changes WHERE revision>%s ORDER BY revision LIMIT %s",
        (cursor, DELTA_LIMIT),
    ).fetchall()
    return {
        "changes": [dict(row) for row in rows],
        "more": processed or len(rows) == DELTA_LIMIT,
        "installed": True,
        "generation": current_generation,
        "reset": reset,
    }


def apply_changes(model, changes):
    result = {
        "cursor": int(model.get("cursor", 0)),
        "config": dict(model.get("config", normalize_config({}))),
        "groups": dict(model.get("groups", {})),
    }
    for row in changes:
        if int(row["revision"]) <= result["cursor"]:
            continue
        if row["change_key"] == "config":
            result["config"] = dict(row["payload"])
        else:
            key = row["change_key"][6:]
            if row["payload"] is None:
                result["groups"].pop(key, None)
            else:
                result["groups"][key] = dict(row["payload"])
        result["cursor"] = int(row["revision"])
    return result


def apply_response(model, data):
    if not data.get("installed", True):
        return model
    result = apply_changes({} if data.get("reset") else model, data["changes"])
    result["generation"] = data.get("generation", model.get("generation", ""))
    return result


def restore_model(value):
    if not isinstance(value, dict):
        raise ValueError("La copia local de recomendaciones no es válida.")
    config = value.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("La configuración local de recomendaciones no es válida.")
    try:
        normalized = {
            **normalize_config(config),
            "version": _nonnegative(config.get("version", 0)),
        }
    except (AttributeError, TypeError, KeyError) as exc:
        raise ValueError(
            "La configuración local de recomendaciones está incompleta."
        ) from exc
    groups = value.get("groups", {})
    if not isinstance(groups, dict):
        raise ValueError("Los grupos de recomendaciones no son válidos.")
    for row in groups.values():
        _validate_group(row)
    return {
        "config": normalized,
        "groups": dict(groups),
        "cursor": _nonnegative(value.get("cursor", 0)),
        "generation": str(value.get("generation") or ""),
    }


def _nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("La versión o frecuencia de recomendaciones no es válida.")
    return value


def _validate_group(row):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("diagnosis"), str)
        or not row.get("diagnosis")
        or row.get("specialty") not in SPECIALTIES
    ):
        raise ValueError("Hay un grupo de recomendaciones incompleto.")
    _nonnegative(row.get("receipts"))
    if not isinstance(row.get("items"), list):
        raise ValueError("Los ítems de recomendaciones no son válidos.")
    for item in row["items"]:
        _validate_statistic(item)


def _validate_statistic(item):
    if (
        not isinstance(item, dict)
        or item.get("category") not in CATEGORIES
        or not item.get("item")
    ):
        raise ValueError("Hay un ítem de recomendaciones incompleto.")
    for field in ("uses", "invoiced", "pending", "candidates"):
        _nonnegative(item.get(field))
    if item["invoiced"] + item["pending"] != item["uses"]:
        raise ValueError("Las frecuencias de recomendaciones no coinciden.")
