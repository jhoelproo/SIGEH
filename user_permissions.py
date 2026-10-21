"""Explicit allow/deny exceptions over the existing role defaults."""

import json

SCHEMA_SQL = """ALTER TABLE users ADD COLUMN IF NOT EXISTS
    permission_overrides JSONB NOT NULL DEFAULT '{}'::jsonb"""


def overrides_for(user):
    value = (user or {}).get("permission_overrides") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def effective_permission(user, permission, defaults):
    if not user or not user.get("is_active", True):
        return False
    override = overrides_for(user).get(permission)
    if type(override) is bool:
        return override
    return permission in defaults


def validate_overrides(values, known):
    if not isinstance(values, dict):
        raise ValueError("Los permisos personalizados no son válidos.")
    if any(
        key not in known or type(value) is not bool for key, value in values.items()
    ):
        raise ValueError("Permiso desconocido o excepción no válida.")
    return dict(values)


def save_overrides(connection, username, values, actor, expected, allowed, known):
    values = validate_overrides(values, known)
    identities = sorted(
        {str(username).strip(), str(actor.get("username") or "").strip()}
    )
    rows = connection.execute(
        "SELECT username,role,is_active,permission_overrides FROM users WHERE username=ANY(%s) ORDER BY username FOR UPDATE",
        (identities,),
    ).fetchall()
    users = {row["username"]: dict(row) for row in rows}
    current_actor = users.get(actor.get("username"))
    if not current_actor or not allowed(current_actor):
        raise PermissionError("No tienes permiso para administrar permisos.")
    target = users.get(username)
    if target is None:
        raise ValueError("El usuario ya no existe.")
    previous = overrides_for(target)
    if expected is not None and expected != previous:
        raise ValueError("Los permisos cambiaron en otra estación. Recarga el usuario.")
    if previous == values:
        return values
    connection.execute(
        "UPDATE users SET permission_overrides=%s::jsonb WHERE username=%s",
        (json.dumps(values), username),
    )
    details = json.dumps(
        {"target": username, "previous": previous, "current": values},
        ensure_ascii=False,
    )
    connection.execute(
        "INSERT INTO action_history(username,action,details,created_at) VALUES(%s,'Cambiar permisos individuales',%s,TO_CHAR(NOW(),'YYYY-MM-DD HH24:MI:SS'))",
        (current_actor["username"], details),
    )
    return values
