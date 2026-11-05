"""Inclusive calendar intervals without network, locale or a real clock."""

from types import SimpleNamespace
import sqlite3

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QDialog

from admission_contract import canonicalize_ars, equivalent_ars_values
from admission_history_layout import fit_history_dialog, bind_dialog_close
from admission_visit_dates import (
    history_date_range,
    history_date_sql,
    history_date_predicate,
)


@pytest.mark.parametrize(
    "value",
    [
        ("03/11/2026", "04-11-2026"),
        ["2026-11-03", "2026-11-04"],
    ],
)
def test_range_normalizes_both_endpoints(value):
    assert history_date_range(value) == ("2026-11-03", "2026-11-04")


def test_single_day_range_is_valid():
    assert history_date_range("04/11/2026") == ("2026-11-04", "2026-11-04")


@pytest.mark.parametrize(
    "value",
    [
        [],
        ["2026-11-03"],
        ["2026-11-03"] * 3,
        ("2026-11-04", "2026-11-03"),
        ("invalid", "2026-11-04"),
    ],
)
def test_invalid_ranges_fail_explicitly(value):
    with pytest.raises(ValueError):
        history_date_range(value)


def test_sqlite_range_includes_endpoints_in_all_supported_formats():
    with sqlite3.connect(":memory:") as con:
        con.execute("CREATE TABLE atenciones(fecha TEXT)")
        dates = ["02/11/2026", "2026-11-03", "04/11/2026", "04-11-2026", "2026-11-05"]
        con.executemany("INSERT INTO atenciones VALUES(?)", [(day,) for day in dates])
        sql = (
            "SELECT fecha FROM atenciones WHERE "
            + history_date_sql("fecha")
            + " BETWEEN ? AND ?"
        )
        assert [
            row[0]
            for row in con.execute(
                sql, history_date_range(("03/11/2026", "04/11/2026"))
            )
        ] == dates[1:4]


def test_date_column_is_not_a_sql_injection_surface():
    with pytest.raises(ValueError):
        history_date_sql("fecha);DROP TABLE atenciones;--")
    with pytest.raises(ValueError):
        history_date_predicate("Entre fechas", (), "fecha", ";DROP")
    bind_dialog_close(SimpleNamespace(), lambda: None)


@pytest.mark.parametrize("width,height", [(1024, 728), (1280, 680), (1920, 1040)])
def test_history_dialog_is_bounded_by_available_desktop(monkeypatch, width, height):
    dialog = QDialog()
    available = QRect(120, 20, width, height)
    monkeypatch.setattr(
        dialog, "screen", lambda: SimpleNamespace(availableGeometry=lambda: available)
    )
    fit_history_dialog(dialog)
    assert available.contains(dialog.geometry())
    assert dialog.height() <= height - 44
    dialog.close()


def test_no_monitor_leaves_dialog_size_unchanged(monkeypatch):
    dialog = QDialog()
    before = dialog.size()
    monkeypatch.setattr(dialog, "screen", lambda: None)
    fit_history_dialog(dialog)
    assert dialog.size() == before


@pytest.mark.parametrize(
    "value,canonical",
    [
        ("HUMANO/PRIMERA", "PRIMERA"),
        ("PRIMERA", "PRIMERA"),
        ("MAPFRE", "MAPFRE/PALIC"),
        ("MAPFRE/PALIC", "MAPFRE/PALIC"),
        ("HUMANO", "HUMANO"),
        ("S.CONTRIBUTIVO", "SENASA CONTRIBUTIVO"),
    ],
)
def test_admission_name_is_canonical_without_merging_independent_humano(
    value, canonical
):
    assert canonicalize_ars(value) == canonical
    assert value.casefold() in equivalent_ars_values(canonical)
