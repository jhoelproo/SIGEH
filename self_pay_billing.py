"""Coverage and shared tariffs for receipts outside insurance billing."""

from billing_money import money

CONTRIBUTIVE_TARIFF = "SENASA CONTRIBUTIVO"
INSURED = "ASEGURADO"
UNINSURED = "NO_ASEGURADO"
FOREIGN = "EXTRANJERO"
COVERAGE_LABELS = {
    INSURED: "Asegurado",
    UNINSURED: "No asegurado",
    FOREIGN: "Extranjero",
}
SELF_PAY_COVERAGES = (FOREIGN, UNINSURED)
PAID = "PAGADO"
EXEMPT = "EXONERADO"
PENDING = "PENDIENTE_PAGO"
PAYMENT_LABELS = {PAID: "Pagado", EXEMPT: "Exonerado", PENDING: "Pendiente de pago"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_self_pay(
 receipt_id BIGINT PRIMARY KEY REFERENCES recibos(id),
 coverage TEXT NOT NULL CHECK(coverage IN ('EXTRANJERO','NO_ASEGURADO')),
 payment_status TEXT NOT NULL CHECK(payment_status IN ('PAGADO','EXONERADO','PENDIENTE_PAGO')),
 exemption_reason TEXT NOT NULL DEFAULT '',
 tariff_ars TEXT NOT NULL DEFAULT 'SENASA CONTRIBUTIVO',
 paid_at TIMESTAMPTZ,
 recorded_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CHECK(coverage='NO_ASEGURADO' OR payment_status IN ('PAGADO','PENDIENTE_PAGO')),
 CHECK(payment_status<>'EXONERADO' OR LENGTH(BTRIM(exemption_reason))>=8)
);
ALTER TABLE receipt_self_pay ENABLE ROW LEVEL SECURITY;
DO $self_pay_upgrade$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM information_schema.columns
   WHERE table_schema='public' AND table_name='receipt_self_pay' AND column_name='paid_at') THEN
   ALTER TABLE receipt_self_pay ADD COLUMN paid_at TIMESTAMPTZ;
   UPDATE receipt_self_pay p SET paid_at=COALESCE(NULLIF(r.created_at,''),NULLIF(r.fecha,''))::TIMESTAMPTZ
     FROM recibos r WHERE r.id=p.receipt_id AND p.payment_status='PAGADO';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='receipt_self_pay'::REGCLASS
   AND conname='receipt_self_pay_payment_status_check'
   AND STRPOS(pg_get_constraintdef(oid),'PENDIENTE_PAGO')=0) THEN
   ALTER TABLE receipt_self_pay DROP CONSTRAINT receipt_self_pay_payment_status_check;
   ALTER TABLE receipt_self_pay DROP CONSTRAINT receipt_self_pay_check;
   ALTER TABLE receipt_self_pay ADD CONSTRAINT receipt_self_pay_payment_status_check
     CHECK(payment_status IN ('PAGADO','EXONERADO','PENDIENTE_PAGO'));
   ALTER TABLE receipt_self_pay ADD CONSTRAINT receipt_self_pay_check
     CHECK(coverage='NO_ASEGURADO' OR payment_status IN ('PAGADO','PENDIENTE_PAGO'));
 END IF;
END;
$self_pay_upgrade$;
"""


def coverage_code(label: str) -> str:
    for code, text in COVERAGE_LABELS.items():
        if label == text:
            return code
    raise ValueError("Seleccione una cobertura válida.")


def coverage_label(code: str) -> str:
    try:
        return COVERAGE_LABELS[code]
    except KeyError as exc:
        raise ValueError("La cobertura no es válida.") from exc


def is_self_pay(code: str) -> bool:
    return code in SELF_PAY_COVERAGES


def tariff_ars(code: str, selected_ars: str) -> str:
    coverage_label(code)
    return CONTRIBUTIVE_TARIFF if is_self_pay(code) else selected_ars


def payment_states(code: str) -> tuple[str, ...]:
    if code == FOREIGN:
        return (PAID, PENDING)
    if code == UNINSURED:
        return (PAID, EXEMPT, PENDING)
    return ()


def validate_payment(code: str, status: str, reason: str = "") -> str:
    if status not in payment_states(code):
        raise ValueError("El estado de cobro no corresponde a esta cobertura.")
    cleaned = str(reason or "").strip()
    if status == EXEMPT and len(cleaned) < 8:
        raise ValueError("Indique el motivo de la exoneración (mínimo 8 caracteres).")
    return cleaned if status == EXEMPT else ""


def save_payment(connection, receipt_id, code, status, reason, actor):
    reason = validate_payment(code, status, reason)
    connection.execute(
        """INSERT INTO receipt_self_pay
           (receipt_id,coverage,payment_status,exemption_reason,recorded_by,paid_at)
           VALUES(%s,%s,%s,%s,%s,CASE WHEN %s='PAGADO' THEN NOW() END)
           ON CONFLICT(receipt_id) DO UPDATE SET payment_status=EXCLUDED.payment_status,
             exemption_reason=EXCLUDED.exemption_reason,recorded_by=EXCLUDED.recorded_by,
             paid_at=CASE WHEN EXCLUDED.payment_status<>'PAGADO' THEN NULL
               WHEN receipt_self_pay.payment_status='PAGADO' THEN receipt_self_pay.paid_at
               ELSE EXCLUDED.paid_at END,updated_at=NOW()""",
        (int(receipt_id), code, status, reason, str(actor), status),
    )


def install_schema(connection):
    connection.executescript(SCHEMA)


def form_coverage(form) -> str:
    widget = getattr(form, "coverage_combo", None)
    if widget is None or not hasattr(widget, "currentText"):
        return INSURED
    text = widget.currentText()
    return coverage_code(text) if text in COVERAGE_LABELS.values() else INSURED


def validate_tariffs(connection, *, room, items, service_type, receipt_id=None):
    row = connection.execute(
        "SELECT sala_emergencia,consulta_price FROM ars WHERE nombre=%s AND is_active=1 FOR SHARE",
        (CONTRIBUTIVE_TARIFF,),
    ).fetchone()
    if row is None:
        raise ValueError("No está disponible la tarifa de SENASA Contributivo.")
    if money(room) != money(
        row["consulta_price" if service_type == "CONSULTA" else "sala_emergencia"] or 0
    ) and not _retains_room_price(connection, receipt_id, room):
        raise ValueError("La sala debe usar la tarifa de SENASA Contributivo.")
    prices = _tariff_prices(connection, receipt_id)
    for category, name, price, _quantity, _total, item_ars in items:
        if item_ars and prices.get((category, name)) != money(price):
            raise ValueError("El ítem debe usar la tarifa de SENASA Contributivo.")


def _retains_room_price(connection, receipt_id, room):
    if receipt_id is None:
        return False
    previous = connection.execute(
        "SELECT sala FROM recibos WHERE id=%s AND receipt_origin='SELF_PAY' FOR UPDATE",
        (int(receipt_id),),
    ).fetchone()
    return previous is not None and money(previous["sala"]) == money(room)


def _tariff_prices(connection, receipt_id):
    catalog = connection.execute(
        """SELECT ai.categoria,ai.nombre,ai.precio FROM ars_items ai
           JOIN ars a ON a.id=ai.ars_id WHERE a.nombre=%s AND ai.is_active=1""",
        (CONTRIBUTIVE_TARIFF,),
    ).fetchall()
    prices = {
        (row["categoria"], row["nombre"]): money(row["precio"]) for row in catalog
    }
    if receipt_id is not None:
        previous = connection.execute(
            "SELECT categoria,nombre,precio_unit FROM recibo_items WHERE recibo_id=%s",
            (int(receipt_id),),
        ).fetchall()
        prices.update(
            {
                (row["categoria"], row["nombre"]): money(row["precio_unit"])
                for row in previous
            }
        )
    return prices


def validate_amounts(room, items, total):
    expected = money(room)
    for _category, _name, price, quantity, subtotal, _ars in items:
        if int(quantity) <= 0 or money(price) < 0:
            raise ValueError("La cantidad y el precio del ítem no son válidos.")
        if money(price) * int(quantity) != money(subtotal):
            raise ValueError("El subtotal no coincide con el precio y la cantidad.")
        expected += money(subtotal)
    if expected <= 0 or expected != money(total):
        raise ValueError("El total de cobro no coincide con la sala y los ítems.")


def _count(rows):
    return sum(int(row.get("count", 1)) for row in rows)


def _amount(rows):
    return str(sum((money(row.get("total") or 0) for row in rows), money(0)))


def _coverage_summary(code, rows):
    paid = [row for row in rows if row.get("payment_status") == PAID]
    exempt = [row for row in rows if row.get("payment_status") == EXEMPT]
    pending = [row for row in rows if row.get("payment_status") == PENDING]
    return {
        "coverage": code,
        "label": coverage_label(code),
        "count": _count(rows),
        "tariff_total": _amount(rows),
        "paid_count": _count(paid),
        "exempt_count": _count(exempt),
        "collected": _amount(paid),
        "exempt_amount": _amount(exempt),
        "pending_count": _count(pending),
        "pending_amount": _amount(pending),
    }


def payment_summary(receipts):
    return [
        _coverage_summary(
            code, [row for row in receipts if row.get("coverage") == code]
        )
        for code in SELF_PAY_COVERAGES
    ]


def receipt_filters(*, start, end, code="", status="", search=""):
    clauses = [
        "r.is_deleted=0",
        "NULLIF(r.created_at,'')::DATE BETWEEN %s::DATE AND %s::DATE",
    ]
    params = [start, end]
    for column, value, allowed in (
        ("p.coverage", code, SELF_PAY_COVERAGES),
        ("p.payment_status", status, (PAID, EXEMPT, PENDING)),
    ):
        if value:
            if value not in allowed:
                raise ValueError("Filtro de cobro inválido.")
            clauses.append(f"{column}=%s")
            params.append(value)
    if search:
        clauses.append("(r.nombre ILIKE %s OR r.numero::TEXT ILIKE %s)")
        params.extend([f"%{search}%"] * 2)
    return " AND ".join(clauses), params


def list_receipts(connection, *, offset=0, limit=100, **filters):
    sql, params = receipt_filters(**filters)
    rows = connection.execute(
        f"""SELECT r.id,r.numero,r.nombre,r.fecha,r.created_at,r.total,r.username,
             p.coverage,p.payment_status,p.exemption_reason,
             COUNT(*) OVER() AS matched_count
             FROM receipt_self_pay p JOIN recibos r ON r.id=p.receipt_id
             WHERE {sql} ORDER BY r.created_at DESC,r.id DESC LIMIT %s OFFSET %s""",
        tuple(params + [max(1, min(int(limit), 200)), max(0, int(offset))]),
    ).fetchall()
    return [dict(row) for row in rows]


def load_summary(connection, **filters):
    sql, params = receipt_filters(**filters)
    rows = connection.execute(
        f"""SELECT p.coverage,p.payment_status,COUNT(*) AS count,SUM(r.total) AS total
           FROM receipt_self_pay p JOIN recibos r ON r.id=p.receipt_id
           WHERE {sql} GROUP BY p.coverage,p.payment_status""",
        tuple(params),
    ).fetchall()
    return payment_summary(rows)
