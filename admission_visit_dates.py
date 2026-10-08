"""Clinical dates and duplicate intervals, independent of station turn identity."""

from datetime import date, datetime, time, timedelta


def service_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    raise ValueError("Seleccione una fecha válida (día, mes y año).")


def date_variants(value):
    selected = service_date(value)
    return tuple(
        selected.strftime(pattern) for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
    )


def visit_moment(data):
    selected = service_date(
        data.get("Fecha") or data.get("fecha") or data.get("service_date")
    )
    raw = (
        str(data.get("Hora") or data.get("hora") or data.get("service_time") or "")
        .strip()
        .upper()
    )
    for pattern in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.combine(selected, datetime.strptime(raw, pattern).time())
        except ValueError:
            continue
    raise ValueError("La atención requiere una hora válida para comprobar su turno.")


def operational_interval(moment):
    base = moment.date() - timedelta(days=moment.time() < time(8))
    start = datetime.combine(base, time(8))
    return start, start + timedelta(days=1)


def duplicate_interval(start, end, moment):
    if start <= moment < end:
        return start, end
    return operational_interval(moment)


def attention_in_interval(attention, start, end):
    try:
        moment = visit_moment(attention)
    except ValueError:
        return False
    return start <= moment < end


def duplicate_date_values(start, end):
    days = (end.date() - start.date()).days + 1
    return tuple(
        value
        for offset in range(days)
        for value in date_variants(start.date() + timedelta(days=offset))
    )


def clinical_day_id(connection, configured_day_id, start, end, moment):
    if start <= moment < end:
        return configured_day_id
    return ensure_clinical_day_id(connection, moment)


def remote_clinical_day_id(connection, configured_day_id, payload):
    try:
        moment = visit_moment(payload)
    except ValueError:
        return configured_day_id
    return ensure_clinical_day_id(connection, moment)


def ensure_clinical_day_id(connection, moment):
    clinical_start, clinical_end = operational_interval(moment)
    connection.execute(
        """INSERT OR IGNORE INTO dias_operativos(fecha_base,fecha_inicio,fecha_fin)
           VALUES(?,?,?)""",
        (
            clinical_start.date().isoformat(),
            clinical_start.isoformat(" "),
            clinical_end.isoformat(" "),
        ),
    )
    return int(
        connection.execute(
            "SELECT id FROM dias_operativos WHERE fecha_base=?",
            (clinical_start.date().isoformat(),),
        ).fetchone()[0]
    )


def current_turn_mode(mode):
    return mode in {"Este turno", "Turno actual"}
