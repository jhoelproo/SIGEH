import inspect
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton

import CALCULOS_QT as app
from app_icons import AppIcons
from admission_v15_adapter import DEFAULT_V15_ROOT

V15_PARENT = str(DEFAULT_V15_ROOT.parent)
if V15_PARENT not in sys.path:
    sys.path.insert(0, V15_PARENT)
from ADMISION_PYSIDE6_V15 import qt_compat as v15_qt


def _qt_app():
    return QApplication.instance() or QApplication([])


class _Result:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _SummaryConnection:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "SELECT p.* FROM admission_attention_projection" in sql:
            return _Result(rows=[
                {
                    "source_instance_id": "PC1-LOCAL",
                    "operational_source_id": "OPERATIONAL-SOURCE",
                    "attention_id": 11,
                    "patient_id": 1011,
                    "turn_id": 33,
                    "patient_name": "PACIENTE 11",
                    "service_date": "2026-08-08",
                    "service_time": "08:00:00",
                    "canonical_ars": "APS",
                    "coverage_status": "ASEGURADO_VALIDADO",
                    "service_type": "EMERGENCIA",
                    "readiness": app.READINESS_READY,
                    "readiness_reasons": "[]",
                },
                {
                    "source_instance_id": "PC2-LOCAL",
                    "operational_source_id": "OPERATIONAL-SOURCE",
                    "attention_id": 12,
                    "patient_id": 1012,
                    "turn_id": 33,
                    "patient_name": "PACIENTE 12",
                    "service_date": "2026-08-08",
                    "service_time": "08:01:00",
                    "canonical_ars": "FUTURO",
                    "coverage_status": "ASEGURADO_VALIDADO",
                    "service_type": "EMERGENCIA",
                    "readiness": "PENDIENTE_CORRECCION",
                    "readiness_reasons": "[]",
                },
            ])
        if "DISTINCT ON (admission_atencion_id)" in sql:
            return _Result(rows=[{
                "source_instance_id": "PC1-LOCAL",
                "admission_atencion_id": 11,
                "estado_facturacion": "FACTURADO",
            }])
        if "COUNT(*) AS patients" in sql:
            return _Result(row={"patients": 2, "total": 1250.0})
        return _Result(row={"inherited_pending": 1, "inherited_processed": 0})


class _CurrentShiftRepository:
    def get_current_shift_context(self):
        return {
            "source_instance_id": "V15-REAL",
            "turn_id": 33,
            "started_at": "2026-08-08 08:00:00",
        }

    def list_current_turn_attentions(self, *, limit):
        assert limit == 50000
        return [
            SimpleNamespace(
                attention_id=11,
                canonical_ars="APS",
                ars="APS",
                uninsured=False,
                billing_readiness=app.READINESS_READY,
            ),
            SimpleNamespace(
                attention_id=12,
                canonical_ars="FUTURO",
                ars="FUTURO",
                uninsured=False,
                billing_readiness="PENDIENTE_CORRECCION",
            ),
        ]


def test_shift_summary_uses_central_operational_turn_not_local_v15_turn():
    connection = _SummaryConnection()
    with (
        patch.object(app, "db_connect", return_value=connection),
        patch.object(
            app,
            "get_central_operational_context",
            return_value={
                "operational_source_id": "OPERATIONAL-SOURCE",
                "source_instance_id": "OPERATIONAL-SOURCE",
                "turn_id": 33,
                "updated_at": "2026-08-08 08:00:00",
            },
        ),
    ):
        summary = app.load_current_shift_billing_summary(_CurrentShiftRepository())

    assert summary["source_instance_id"] == "OPERATIONAL-SOURCE"
    assert summary["turn_id"] == 33
    assert summary["admitted"] == 2
    assert summary["invoiced"] == 1
    assert summary["pending"] == 1
    assert summary["correction"] == 1
    assert [row["ars"] for row in summary["by_ars"]] == ["APS", "FUTURO"]
    assert all("MAX(p2.turn_id)" not in sql for sql, _params in connection.calls)


class _SelectionConnection:
    def __init__(self):
        self.params = None
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        return _Result(row=None)


def test_privileged_roles_cannot_select_uninherited_historical_attention():
    connection = _SelectionConnection()
    with (
        patch.object(
            app.BillingAdmissionQueryService,
            "current_shift",
            return_value={"source_instance_id": "V15-REAL", "turn_id": 33},
        ),
        patch.object(app, "db_connect", return_value=connection),
    ):
        result = app.get_projected_billable_attention(
            99,
            "V15-REAL",
            current_user={"role": app.ROLE_AUDIT},
        )

    assert result is None
    assert "ELSE 'HISTÓRICO' END AS turn_scope" in connection.sql
    assert "AND (p.turn_id=cs.turn_id" in connection.sql
    assert "(%s OR p.turn_id=cs.turn_id" not in connection.sql


def test_regular_roles_keep_current_or_inherited_turn_restriction():
    connection = _SelectionConnection()
    with (
        patch.object(
            app.BillingAdmissionQueryService,
            "current_shift",
            return_value={"source_instance_id": "V15-REAL", "turn_id": 33},
        ),
        patch.object(app, "db_connect", return_value=connection),
    ):
        app.get_projected_billable_attention(
            99,
            "V15-REAL",
            current_user={"role": app.ROLE_AUX},
        )
    assert "AND (p.turn_id=cs.turn_id" in connection.sql
    assert "(%s OR p.turn_id=cs.turn_id" not in connection.sql


def test_yellow_theme_icons_remain_visible_for_moon_and_sun():
    _qt_app()
    button = QPushButton()
    for key in ("billing_theme_to_dark", "billing_theme_to_light"):
        pixmap = AppIcons.icon(key, button, 20, color="#FFD45A").pixmap(20, 20)
        image = pixmap.toImage()
        assert any(
            QColor(image.pixel(x, y)).red() > 220
            and QColor(image.pixel(x, y)).green() > 150
            and QColor(image.pixel(x, y)).blue() < 150
            for x in range(image.width())
            for y in range(image.height())
        )


def test_change_user_notice_keeps_text_width_separate_from_warning_icon():
    source = inspect.getsource(v15_qt._MessageBoxNS.showwarning)
    assert "QLabel#qt_msgbox_label" in source
    assert "QLabel#qt_msgboxex_icon_label" in source
    assert '"QMessageBox QLabel { color:' not in source
