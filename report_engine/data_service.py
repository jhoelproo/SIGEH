from __future__ import annotations

from datetime import datetime, timedelta

from .query import (
    ANALYSIS_CONFIRMED,
    DATE_BASIS_LABELS,
    medication_ars_sql_exclusion,
    receipt_scope,
)


class PanelDataService:
    """Fuente única de datos para GUI, Excel y PDF del panel estadístico."""

    COVERAGE_EXPR = (
        "COALESCE(NULLIF(r.tipo_cobertura, ''), "
        "CASE WHEN COALESCE(r.ars, '')='' THEN 'NO_ASEGURADO' ELSE 'ASEGURADO' END)"
    )

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _period_before(start_date: str, end_date: str):
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        days = max(1, (end - start).days + 1)
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        return previous_start.strftime("%Y-%m-%d"), previous_end.strftime("%Y-%m-%d")

    @staticmethod
    def _selection_filter(column: str, selection, clauses: list, params: list):
        if isinstance(selection, dict):
            values = [str(value) for value in selection.get("values", []) if str(value).strip()]
            mode = selection.get("mode", "include")
            if values:
                placeholder = "%s"
                if mode in ("exclude", "excluir"):
                    clauses.append(f"NOT (COALESCE({column}, '') = ANY({placeholder}))")
                else:
                    clauses.append(f"COALESCE({column}, '') = ANY({placeholder})")
                params.append(values)
            return
        ignored = {"", "Todas las ARS", "Todos los Usuarios", None}
        if selection not in ignored:
            clauses.append(f"{column}=%s")
            params.append(selection)

    @classmethod
    def _receipt_where(
        cls, start_date, end_date, ars_filter, user_filter, medication, category, coverage,
        analysis_type=ANALYSIS_CONFIRMED,
        service_type="EMERGENCIA",
    ):
        clauses, params, date_expr, definition = receipt_scope(
            "r", start_date, end_date, analysis_type, service_type
        )
        cls._selection_filter("r.ars", ars_filter, clauses, params)
        cls._selection_filter("r.username", user_filter, clauses, params)
        if coverage == "Asegurados":
            clauses.append(f"{cls.COVERAGE_EXPR}='ASEGURADO'")
        elif coverage == "No asegurados":
            clauses.append(f"{cls.COVERAGE_EXPR}='NO_ASEGURADO'")
        if medication and medication != "Todos los medicamentos":
            clauses.append(
                "EXISTS (SELECT 1 FROM recibo_items fx WHERE fx.recibo_id=r.id "
                "AND fx.categoria='Medicamentos' AND fx.nombre=%s)"
            )
            params.append(medication)
        if category and category not in ("Todas las categorías", "Sala de Emergencia"):
            clauses.append(
                "EXISTS (SELECT 1 FROM recibo_items fc WHERE fc.recibo_id=r.id AND fc.categoria=%s)"
            )
            params.append(category)
        return " AND ".join(clauses), params, date_expr, definition

    @classmethod
    def _item_where(
        cls, start_date, end_date, ars_filter, user_filter, medication, category, coverage,
        analysis_type=ANALYSIS_CONFIRMED,
    ):
        where, params, date_expr, definition = cls._receipt_where(
            start_date, end_date, ars_filter, user_filter, "", "", coverage, analysis_type
        )
        clauses = [where]
        if medication and medication != "Todos los medicamentos":
            clauses.extend(["ri.categoria='Medicamentos'", "ri.nombre=%s"])
            params.append(medication)
        if category == "Sala de Emergencia":
            clauses.append("1=0")
        elif category and category != "Todas las categorías":
            clauses.append("ri.categoria=%s")
            params.append(category)
        return " AND ".join(clauses), params, date_expr, definition

    @staticmethod
    def _summary(con, where, params):
        row = con.execute(
            f"""SELECT COUNT(*) AS receipt_count,
                       COALESCE(SUM(r.total), 0) AS total_amount,
                       COALESCE(AVG(r.total), 0) AS average_amount,
                       COALESCE(SUM(r.sala), 0) AS room_total
                FROM recibos r WHERE {where}""",
            tuple(params),
        ).fetchone()
        return {
            "receipts": int(row["receipt_count"] or 0),
            "total": float(row["total_amount"] or 0),
            "average": float(row["average_amount"] or 0),
            "room": float(row["room_total"] or 0),
        }

    @staticmethod
    def _fill_daily_gaps(rows, start_date, end_date):
        indexed = {row["label"]: row for row in rows}
        cursor = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        result = []
        while cursor <= end:
            label = cursor.strftime("%Y-%m-%d")
            result.append(indexed.get(label, {"label": label, "receipts": 0, "total": 0.0, "average": 0.0}))
            cursor += timedelta(days=1)
        return result

    def load(
        self,
        start_date: str,
        end_date: str,
        ars_filter=None,
        user_filter=None,
        medication: str = "Todos los medicamentos",
        category: str = "Todas las categorías",
        trend_granularity: str = "day",
        coverage: str = "Todas",
        compare_previous: bool = False,
        previous_start: str = "",
        previous_end: str = "",
        analysis_type: str = ANALYSIS_CONFIRMED,
    ):
        ars_filter = ars_filter or {"mode": "include", "values": []}
        user_filter = user_filter or {"mode": "include", "values": []}
        receipt_where, receipt_params, receipt_date_expr, definition = self._receipt_where(
            start_date, end_date, ars_filter, user_filter, medication, category, coverage,
            analysis_type,
        )
        item_where, item_params, _item_date_expr, _item_definition = self._item_where(
            start_date, end_date, ars_filter, user_filter, medication, category, coverage,
            analysis_type,
        )
        if not previous_start or not previous_end:
            previous_start, previous_end = self._period_before(start_date, end_date)
        previous_where, previous_params, previous_date_expr, _ = self._receipt_where(
            previous_start, previous_end, ars_filter, user_filter, medication, category, coverage,
            analysis_type,
        )
        previous_item_where, previous_item_params, _, _ = self._item_where(
            previous_start, previous_end, ars_filter, user_filter, medication, category, coverage,
            analysis_type,
        )

        time_expression = {
            "day": f"TO_CHAR({receipt_date_expr}, 'YYYY-MM-DD')",
            "week": f"TO_CHAR(DATE_TRUNC('week', {receipt_date_expr}), 'YYYY-MM-DD')",
            "month": f"TO_CHAR(DATE_TRUNC('month', {receipt_date_expr}), 'YYYY-MM')",
        }.get(trend_granularity, f"TO_CHAR({receipt_date_expr}, 'YYYY-MM-DD')")

        with self.connection_factory() as con:
            summary = self._summary(con, receipt_where, receipt_params)
            previous = self._summary(con, previous_where, previous_params) if compare_previous else {}
            consultation_where, consultation_params, _, _ = self._receipt_where(
                start_date,
                end_date,
                ars_filter,
                user_filter,
                medication,
                category,
                coverage,
                analysis_type,
                "CONSULTA",
            )
            raw_consultation = con.execute(
                f"""SELECT COUNT(*) AS receipt_count,
                           COALESCE(SUM(r.total),0) AS total_amount
                    FROM recibos r WHERE {consultation_where}""",
                tuple(consultation_params),
            ).fetchone()
            consultation_summary = {
                "receipts": int(raw_consultation["receipt_count"] or 0),
                "total": float(raw_consultation["total_amount"] or 0),
            }
            consultation_ars_rows = con.execute(
                f"""SELECT COALESCE(NULLIF(r.ars,''),'Sin ARS') AS label,
                           COUNT(*) AS receipt_count,
                           COALESCE(SUM(r.total),0) AS total_amount
                    FROM recibos r WHERE {consultation_where}
                    GROUP BY 1 ORDER BY SUM(r.total) DESC""",
                tuple(consultation_params),
            ).fetchall()
            consultations = [
                {
                    "label": str(row["label"]),
                    "receipts": int(row["receipt_count"]),
                    "total": float(row["total_amount"]),
                }
                for row in consultation_ars_rows
            ]

            trend_rows = con.execute(
                f"""SELECT {time_expression} AS label,
                           COUNT(*) AS receipt_count,
                           COALESCE(SUM(r.total), 0) AS total_amount
                    FROM recibos r WHERE {receipt_where} GROUP BY 1 ORDER BY 1""",
                tuple(receipt_params),
            ).fetchall()
            trend = [
                {
                    "label": str(row["label"]),
                    "receipts": int(row["receipt_count"]),
                    "total": float(row["total_amount"]),
                }
                for row in trend_rows
            ]
            for row in trend:
                row["average"] = row["total"] / row["receipts"] if row["receipts"] else 0.0
            if trend_granularity == "day":
                trend = self._fill_daily_gaps(trend, start_date, end_date)

            def category_data(where, params, room, receipt_count):
                rows = con.execute(
                    f"""SELECT ri.categoria AS label,
                               COUNT(DISTINCT r.id) AS receipt_count,
                               COALESCE(SUM(ri.cantidad), 0) AS item_quantity,
                               COALESCE(SUM(ri.total), 0) AS total_amount
                        FROM recibo_items ri JOIN recibos r ON r.id=ri.recibo_id
                        WHERE {where} GROUP BY ri.categoria ORDER BY SUM(ri.total) DESC""",
                    tuple(params),
                ).fetchall()
                data = [
                    {
                        "label": str(row["label"]),
                        "receipts": int(row["receipt_count"]),
                        "quantity": int(row["item_quantity"]),
                        "total": float(row["total_amount"]),
                    }
                    for row in rows
                ]
                if room > 0 and category in ("Todas las categorías", "Sala de Emergencia"):
                    data.append({"label": "Sala de Emergencia", "receipts": receipt_count, "quantity": receipt_count, "total": room})
                total = sum(row["total"] for row in data)
                for row in data:
                    row["average"] = row["total"] / row["receipts"] if row["receipts"] else 0.0
                    row["percentage"] = row["total"] / total if total else 0.0
                data.sort(key=lambda item: item["total"], reverse=True)
                return data

            categories = category_data(item_where, item_params, summary["room"], summary["receipts"])
            previous_categories = (
                category_data(
                    previous_item_where, previous_item_params,
                    previous.get("room", 0.0), previous.get("receipts", 0),
                )
                if compare_previous else []
            )

            insured_where = f"{receipt_where} AND {self.COVERAGE_EXPR}='ASEGURADO'"
            raw_ars_rows = [] if coverage == "No asegurados" else con.execute(
                f"""SELECT COALESCE(NULLIF(r.ars, ''), 'Sin ARS') AS label,
                           COUNT(*) AS receipt_count,
                           COALESCE(SUM(r.total), 0) AS total_amount
                    FROM recibos r WHERE {insured_where}
                    GROUP BY COALESCE(NULLIF(r.ars, ''), 'Sin ARS')
                    ORDER BY SUM(r.total) DESC, COUNT(*) DESC""",
                tuple(receipt_params),
            ).fetchall()
            ars_rows = [
                {
                    "label": str(row["label"]),
                    "receipts": int(row["receipt_count"]),
                    "total": float(row["total_amount"]),
                }
                for row in raw_ars_rows
            ]

            coverage_rows = con.execute(
                f"""SELECT {self.COVERAGE_EXPR} AS coverage_type,
                           COUNT(*) AS receipt_count,
                           COALESCE(SUM(r.total), 0) AS total_amount,
                           COALESCE(AVG(r.total), 0) AS average_amount,
                           COALESCE(SUM(r.sala), 0) AS room_total
                    FROM recibos r WHERE {receipt_where}
                    GROUP BY {self.COVERAGE_EXPR} ORDER BY 1""",
                tuple(receipt_params),
            ).fetchall()
            coverage_stats = [
                {
                    "label": (
                        "Asegurados"
                        if row["coverage_type"] == "ASEGURADO"
                        else "No asegurados"
                    ),
                    "receipts": int(row["receipt_count"]),
                    "total": float(row["total_amount"]),
                    "average": float(row["average_amount"]),
                    "room": float(row["room_total"]),
                }
                for row in coverage_rows
            ]

            detailed_rows = con.execute(
                f"""SELECT r.numero AS receipt_number,
                           r.fecha AS service_date,
                           r.created_at AS created_at,
                           r.username AS username,
                           r.ars AS ars,
                           {self.COVERAGE_EXPR} AS coverage_type,
                           ri.categoria AS category,
                           ri.nombre AS item_name,
                           ri.cantidad AS item_quantity,
                           ri.precio_unit AS unit_price,
                           ri.total AS item_total,
                           r.estado_facturacion AS billing_status,
                           r.estado_facturacion_at AS billing_status_at,
                           r.estado_facturacion_por AS validated_by,
                           r.referencia_facturacion AS billing_reference,
                           r.auditoria_asignada_a AS audit_assignee,
                           r.auditoria_riesgo AS audit_risk,
                           r.motivo_no_facturado_codigo AS not_invoiced_reason_code,
                           r.estado_documento AS document_state,
                           r.numero_autorizacion AS authorization_number
                    FROM recibo_items ri JOIN recibos r ON r.id=ri.recibo_id
                    WHERE {item_where}
                    ORDER BY {receipt_date_expr}, r.numero, ri.categoria, ri.nombre""",
                tuple(item_params),
            ).fetchall()
            details = [
                {
                    "receipt": int(row["receipt_number"]),
                    "service_date": str(row["service_date"] or ""),
                    "created_at": str(row["created_at"] or ""),
                    "username": str(row["username"] or ""),
                    "ars": str(row["ars"] or ""),
                    "coverage": str(row["coverage_type"] or ""),
                    "category": str(row["category"]),
                    "item": str(row["item_name"]),
                    "quantity": int(row["item_quantity"]),
                    "unit_price": float(row["unit_price"]),
                    "total": float(row["item_total"]),
                    "billing_status": str(row["billing_status"] or ""),
                    "billing_status_at": str(row["billing_status_at"] or ""),
                    "validated_by": str(row["validated_by"] or ""),
                    "billing_reference": str(row["billing_reference"] or ""),
                    "audit_assignee": str(row["audit_assignee"] or ""),
                    "audit_risk": int(row["audit_risk"] or 0),
                    "not_invoiced_reason_code": str(
                        row["not_invoiced_reason_code"] or ""
                    ),
                    "document_state": str(row["document_state"] or ""),
                    "authorization_number": str(
                        row["authorization_number"] or ""
                    ),
                }
                for row in detailed_rows
            ]

        for row in ars_rows:
            row["average"] = row["total"] / row["receipts"] if row["receipts"] else 0.0
        distribution_total = sum(row["total"] for row in categories)
        distribution = [dict(row) for row in categories[:7]]
        ars_total = sum(row["total"] for row in ars_rows)
        ars_receipts = sum(row["receipts"] for row in ars_rows)
        summary_table = [
            {"type": "ars", "label": row["label"], "receipts": row["receipts"], "total": row["total"],
             "average": row["average"], "money_percentage": row["total"] / ars_total if ars_total else 0.0,
             "receipt_percentage": row["receipts"] / ars_receipts if ars_receipts else 0.0}
            for row in ars_rows
        ]
        current_category_map = {row["label"]: row for row in categories}
        previous_category_map = {row["label"]: row for row in previous_categories}
        category_labels = list(current_category_map)
        category_labels.extend(label for label in previous_category_map if label not in current_category_map)
        category_comparison = [
            {
                "label": label,
                "current": current_category_map.get(label, {}).get("total", 0.0),
                "previous": previous_category_map.get(label, {}).get("total", 0.0),
            }
            for label in category_labels
        ]

        return {
            "start_date": start_date, "end_date": end_date,
            "previous_start": previous_start, "previous_end": previous_end,
            "filters": {"ars": ars_filter, "user": user_filter, "medication": medication,
                        "category": category, "coverage": coverage, "trend_granularity": trend_granularity,
                        "compare_previous": compare_previous, "analysis_type": definition["key"],
                        "analysis_label": definition["label"], "statuses": list(definition["statuses"]),
                        "date_basis": definition["date_basis"],
                        "date_basis_label": DATE_BASIS_LABELS[definition["date_basis"]],
                        "total_label": definition["total_label"],
                        "receipt_label": definition["receipt_label"]},
            "query": {
                "analysis_type": definition["key"], "analysis_label": definition["label"],
                "statuses": list(definition["statuses"]), "date_basis": definition["date_basis"],
                "date_basis_label": DATE_BASIS_LABELS[definition["date_basis"]],
            },
            "summary": summary, "previous": previous, "trend": trend, "monthly": trend,
            "categories": categories, "previous_categories": previous_categories,
            "category_comparison": category_comparison, "category_distribution": distribution,
            "coverage": coverage_stats, "users": [], "ars": ars_rows, "ars_breakdown": ars_rows,
            "show_ars_comparison": coverage != "No asegurados" and bool(ars_rows),
            "bar": ars_rows, "comparison": summary_table, "breakdown": ars_rows, "breakdown_type": "ars",
            "summary_table": summary_table, "details": details,
            "distribution_total": distribution_total,
            "consultations": consultations,
            "consultation_summary": consultation_summary,
        }
