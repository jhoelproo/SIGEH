import CALCULOS_QT as app


class _Result:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _Connection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))
        return self.results.pop(0)


def test_operational_ars_clause_is_safe_in_parameterized_queries():
    clause = app.medication_ars_sql_exclusion("r.ars")
    assert "%" not in clause
    assert "SENASASUB" in clause


def test_history_count_and_filter_options_use_named_columns():
    count_connection = _Connection([
        _Result(one={"total_count": 37}),
    ])
    assert app.count_receipts_history(_connection=count_connection) == 37

    filters_connection = _Connection([
        _Result(one={
            "active_users": ["ana", "luis"],
            "active_ars": ["ARS A", "ARS B"],
        }),
    ])
    assert app.get_receipt_history_filter_options(
        _connection=filters_connection
    ) == {
        "users": ["ana", "luis"],
        "ars": ["ARS A", "ARS B"],
    }


def test_history_metrics_map_aliases_without_tuple_positions():
    connection = _Connection([
        _Result(many=[{
            "status": app.BILLING_PENDING,
            "status_receipts": 4,
            "status_total": 1250.5,
            "pending_receipts": 4,
            "pending_total": 1250.5,
            "pending_overdue": 1,
            "pending_unassigned": 2,
            "pending_preliminary": 3,
            "pending_ready": 1,
        }]),
    ])
    metrics = app.get_receipt_history_metrics(_connection=connection)
    assert metrics["summary"][app.BILLING_PENDING] == {
        "receipts": 4,
        "total": 1250.5,
    }
    assert metrics["queue"] == {
        "receipts": 4,
        "total": 1250.5,
        "overdue": 1,
        "unassigned": 2,
        "preliminary": 3,
        "ready": 1,
    }


def test_history_period_starts_neutral():
    assert app.ReceiptHistoryPeriodFilter.OPTIONS[0] == "Todos"
