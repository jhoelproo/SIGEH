"""Service boundaries, stale views and server-confirmed publication."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import ars_management as insurers
import diagnosis_recommendation_store as recommendations
from admission_v15_adapter import _HybridDatabaseProxy


@pytest.mark.parametrize(
    "source,target",
    [
        ("", "PRIMERA"),
        ("PRIMERA", "PRIMERA"),
        ("MAPFRE", "Mapfre"),
        ("HUMANO", "PRIMERA"),
    ],
)
def test_invalid_merge_does_not_touch_the_database(source, target):
    connection = Mock()
    with pytest.raises(ValueError):
        insurers.merge_preview(connection, source, target)
    connection.execute.assert_not_called()


def test_missing_source_is_not_silently_created():
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = []
    with pytest.raises(ValueError, match="ya no existe"):
        insurers.merge_preview(connection, "MAPFRE", "MAPFRE/PALIC")


@pytest.mark.parametrize("available,rows", [(False, []), (True, [])])
def test_merge_without_recommendation_data_does_not_publish(
    monkeypatch, available, rows
):
    monkeypatch.setattr(recommendations, "available", lambda _con: available)
    monkeypatch.setattr(
        recommendations, "load_config", lambda _con: {"version": 7, "ars_rules": rows}
    )
    save = Mock()
    monkeypatch.setattr(recommendations, "save_config", save)
    insurers._merge_scoped_recommendations(object(), 1, 2, {}, Mock())
    save.assert_not_called()


def test_scoped_recommendations_keep_destination_decisions_and_move_remaining_rules(
    monkeypatch,
):
    rule = dict(
        ars_id=1,
        diagnosis="CEFALEA",
        specialty="EMERGENCIOLOGÍA",
        category="Laboratorios",
        item="HEMOGRAMA",
        mode="pin",
    )
    existing = dict(rule, ars_id=2, mode="block")
    other = dict(rule, diagnosis="FIEBRE")
    current = {"version": 7, "ars_rules": [rule, existing, other]}
    save = Mock()
    monkeypatch.setattr(recommendations, "available", lambda _con: True)
    monkeypatch.setattr(recommendations, "load_config", lambda _con: current)
    monkeypatch.setattr(recommendations, "save_config", save)
    connection, actor, audit = object(), {"username": "QA"}, Mock()
    insurers._merge_scoped_recommendations(connection, 1, 2, actor, audit)
    saved = save.call_args.args[1]
    assert saved["ars_rules"] == [existing, dict(other, ars_id=2)]
    assert save.call_args.args[3] == 7
    assert save.call_args.kwargs["audit"] is audit
    assert current["ars_rules"][0]["ars_id"] == 1


def proxy(offline=False):
    instance = object.__new__(_HybridDatabaseProxy)
    database = SimpleNamespace(listar_ars_conteo=Mock(return_value=[("LOCAL", 1)]))
    runtime = SimpleNamespace(
        offline=offline,
        current_user={"username": "QA"},
        host=SimpleNamespace(connection_factory=lambda: nullcontext("connection")),
    )
    object.__setattr__(instance, "_database", database)
    object.__setattr__(instance, "_runtime", runtime)
    return instance


def test_insurer_counts_use_central_records_online_and_private_cache_offline(
    monkeypatch,
):
    counts = Mock(return_value=[("CENTRAL", 3)])
    monkeypatch.setattr(insurers, "current_insurer_counts", counts)
    assert proxy().listar_ars_conteo() == [("CENTRAL", 3)]
    counts.assert_called_once_with("connection")
    assert proxy(offline=True).listar_ars_conteo() == [("LOCAL", 1)]


def test_shared_insurer_edits_require_connection_and_keep_actor(monkeypatch):
    replace = Mock(return_value=3)
    monkeypatch.setattr(insurers, "replace_current_insurer", replace)
    assert proxy().eliminar_ars_global("MAPFRE/PALIC") == 3
    assert replace.call_args.args == (
        "connection",
        "MAPFRE/PALIC",
        "SIN SEGURO",
        {"username": "QA"},
    )
    with pytest.raises(RuntimeError, match="Conecta"):
        proxy(offline=True).eliminar_ars_global("MAPFRE/PALIC")
    assert replace.call_count == 1
