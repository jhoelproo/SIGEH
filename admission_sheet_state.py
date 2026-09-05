"""Turn validation for sheet generation, independent of the local JSON mirror."""

from datetime import datetime


class SheetOperationalError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ConfirmedTurnConfig(dict):
    """In-process configuration derived from an authorized operational snapshot."""


def validate_sheet_snapshot_identity(config, state):
    """Reject a form prepared before a handoff/permission revision."""
    fields = ("operational_source_id", "turn_id", "generation", "operational_revision")
    if any(config.get(field) != state.get(field) for field in fields):
        raise SheetOperationalError(
            "STALE_OPERATIONAL_SNAPSHOT",
            "El estado operacional cambió durante la validación. Vuelve a generar la hoja.",
        )


def _positive_turn_id(value):
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def generation_state_error(state):
    if not state:
        return (
            "CENTRAL_UNAVAILABLE",
            "No se pudo verificar el estado operacional central.",
        )
    if state.get("offline") and not state.get("writable"):
        return "CENTRAL_UNAVAILABLE", "Sin conexión central ni permiso offline vigente."
    if not str(state.get("operational_source_id") or "").strip() or not state.get(
        "turn_id"
    ):
        return "NO_TURN_CONFIGURED", "No existe un turno central activo configurado."
    if not _positive_turn_id(state["turn_id"]):
        return (
            "LOCAL_STATE_STALE",
            "La identidad del turno central no pudo verificarse.",
        )
    if state.get("status") != "ACTIVE":
        return "NO_TURN_CONFIGURED", "El turno central no está activo."
    if not state.get("writable", state.get("write_allowed", False)):
        return (
            "SESSION_INVALID",
            "El turno existe, pero esta sesión no tiene permiso para registrar.",
        )
    return None


def confirmed_turn_config(state):
    error = generation_state_error(state)
    if error:
        raise SheetOperationalError(*error)
    try:
        started = state.get("turn_started_at")
        if not isinstance(started, datetime):
            started = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        if started.tzinfo:
            started = started.astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        raise SheetOperationalError(
            "LOCAL_STATE_STALE",
            "El turno existe, pero su rango aún no pudo verificarse.",
        ) from None
    representative = state.get("active_user_display_name") or state.get(
        "active_username"
    )
    if not representative or not state.get("turn_code"):
        raise SheetOperationalError(
            "LOCAL_STATE_STALE",
            "El turno existe, pero falta verificar su configuración central.",
        )
    return ConfirmedTurnConfig(
        representante=representative,
        turno_codigo=state["turn_code"],
        fecha_base=started.date(),
        inicio_real_dt=started,
        inicio_real=started.isoformat(),
        operational_source_id=state["operational_source_id"],
        turn_id=state["turn_id"],
        generation=state.get("generation"),
        operational_revision=state.get("operational_revision"),
    )
