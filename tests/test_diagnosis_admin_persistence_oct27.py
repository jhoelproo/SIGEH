"""Administrative decisions survive history, old clients, rollback and reconnect."""

from copy import deepcopy
from types import SimpleNamespace

import psycopg2
import pytest
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
import diagnosis_recommendation_admin as admin
from diagnosis_recommendation_store import load_config, save_config
from diagnosis_recommendations import (
    DiagnosisCatalog,
    recommendations,
    normalize_config,
)
from recommendation_config_events import listener_parameters, receive_changes
from tests.test_diagnosis_recommendation_postgres import (
    learning as learning,
    receipts as receipts,
    server as server,
    drain,
    ADMIN,
)
from tests.test_receipt_optional_uuid_postgres import save


def test_delete_and_rename_survive_reopen_and_two_client_sync(learning):
    save(dx="DIAGNOSTICO MAL QA")
    first, second = drain(), drain()
    old = deepcopy(first)
    with app.db_connect() as con:
        original = dict(con.execute("SELECT * FROM recibos WHERE numero=1").fetchone())
        save_config(
            con,
            {"excluded_diagnoses": ["DIAGNOSTICO MAL QA"]},
            ADMIN,
            0,
            audit=app._audit_billing_item_rule,
        )
    first, second = drain(first), drain(second)
    assert first == second and old["config"].get("excluded_diagnoses", []) == []
    assert not admin.prepare_admin_data({**first, "items": []})["definitions"]
    with app.db_connect() as con:
        assert (
            dict(con.execute("SELECT * FROM recibos WHERE numero=1").fetchone())
            == original
        )
        # A previous client saves the old configuration shape.
        con.execute("UPDATE sigeh_recommendations.settings SET config='{}',version=2")
        con.execute(
            "UPDATE sigeh_recommendations.changes SET payload='{\"version\":2}',revision=DEFAULT WHERE change_key='config'"
        )
    restored = drain(first)
    assert restored["config"]["version"] == 2
    assert restored["config"]["excluded_diagnoses"] == ["DIAGNOSTICO MAL QA"]


def test_server_notification_occurs_only_after_commit_and_never_on_rollback(
    learning, server
):
    listener = psycopg2.connect(server)
    listener.autocommit = True
    try:
        with listener.cursor() as cursor:
            cursor.execute("LISTEN sigeh_recommendation_config")
        with app.db_connect() as con:
            save_config(
                con,
                {"excluded_diagnoses": ["CEFALEA M"]},
                ADMIN,
                0,
                audit=app._audit_billing_item_rule,
            )
            assert not receive_changes(listener, timeout=0)
        assert receive_changes(listener, timeout=2)
        with pytest.raises(RuntimeError):
            with app.db_connect() as con:
                save_config(
                    con,
                    {"excluded_diagnoses": []},
                    ADMIN,
                    1,
                    audit=app._audit_billing_item_rule,
                )
                raise RuntimeError("Rollback")
        assert not receive_changes(listener, timeout=0)
        with app.db_connect() as con:
            assert load_config(con)["excluded_diagnoses"] == ["CEFALEA M"]
    finally:
        listener.close()


def test_ars_catalog_validation_scope_rename_and_persistence(learning):
    with app.db_connect() as con:
        ids = [
            con.execute(
                "INSERT INTO ars(nombre) VALUES(%s) RETURNING id", (name,)
            ).fetchone()[0]
            for name in ("ARS QA A", "ARS QA B")
        ]
        for identity, item in zip(ids, ["LAB A", "LAB B"]):
            con.execute(
                "INSERT INTO ars_items(ars_id,categoria,nombre,precio) VALUES(%s,'Laboratorios',%s,15)",
                (identity, item),
            )
    rule = {
        "ars_id": ids[0],
        "diagnosis": "CEFALEA",
        "specialty": "GENERAL",
        "category": "Laboratorios",
        "item": "LAB A",
        "mode": "pin",
    }
    with app.db_connect() as con:
        save_config(
            con, {"ars_rules": [rule]}, ADMIN, 0, audit=app._audit_billing_item_rule
        )
    with pytest.raises(ValueError, match="catálogo"):
        with app.db_connect() as con:
            save_config(
                con,
                {"ars_rules": [{**rule, "ars_id": ids[1]}]},
                ADMIN,
                1,
                audit=app._audit_billing_item_rule,
            )
    model = drain()
    catalogs = {"Laboratorios": {"LAB A": 15, "LAB B": 15}}
    assert (
        recommendations(model, "CEFALEA", "GENERAL", catalogs, ars_id=ids[0])[
            "Laboratorios"
        ][0]["item"]
        == "LAB A"
    )
    assert (
        recommendations(model, "CEFALEA", "GENERAL", catalogs, ars_id=ids[1])[
            "Laboratorios"
        ]
        == []
    )
    with app.db_connect() as con:
        con.execute("UPDATE ars SET nombre='ARS QA NUEVA' WHERE id=%s", (ids[0],))
        assert load_config(con)["ars_rules"][0]["ars_id"] == ids[0]


def test_preliminary_permission_never_makes_an_unauthorized_receipt_complete(learning):
    receipt_id = save(authorization_number="")
    with app.db_connect() as con:
        row = con.execute(
            "SELECT estado_documento,numero_autorizacion FROM recibos WHERE id=%s",
            (receipt_id,),
        ).fetchone()
    assert row["estado_documento"] == app.DOCUMENT_PRELIMINARY
    assert not row["numero_autorizacion"]


def test_new_shared_studies_require_an_ars_and_inactive_catalog_is_rejected(learning):
    with app.db_connect() as con:
        identity = con.execute(
            "INSERT INTO ars(nombre) VALUES('ARS QA') RETURNING id"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO ars_items(ars_id,categoria,nombre,precio) VALUES(%s,'Imágenes','RX QA',10)",
            (identity,),
        )
    study = {
        "diagnosis": "CEFALEA",
        "specialty": "GENERAL",
        "category": "Imágenes",
        "item": "RX QA",
        "mode": "pin",
    }
    with pytest.raises(ValueError, match="ARS"):
        with app.db_connect() as con:
            save_config(
                con, {"rules": [study]}, ADMIN, 0, audit=app._audit_billing_item_rule
            )
    with app.db_connect() as con:
        con.execute("UPDATE ars_items SET is_active=0 WHERE nombre='RX QA'")
    with pytest.raises(ValueError, match="catálogo"):
        with app.db_connect() as con:
            save_config(
                con,
                {"ars_rules": [{**study, "ars_id": identity}]},
                ADMIN,
                0,
                audit=app._audit_billing_item_rule,
            )


@pytest.mark.parametrize(
    "port,host,expected",
    [
        ("6543", "x.pooler.supabase.com", "5432"),
        ("5432", "x.pooler.supabase.com", "5432"),
        ("6543", "localhost", "6543"),
    ],
)
def test_listener_uses_session_pooler_only_for_supabase(port, host, expected):
    parameters = listener_parameters(f"host={host} port={port} user=synthetic")
    assert parameters["port"] == expected
    assert parameters["connect_timeout"] == 5


def test_admin_rename_retains_alias_and_delete_excludes_history(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(admin.RecommendationAdminDialog, "reload", lambda self: None)
    dialog = admin.RecommendationAdminDialog(
        SimpleNamespace(user_is_admin=lambda _: True), ADMIN, {}
    )
    data = {
        "config": {**normalize_config({}), "version": 0},
        "items": [],
        "groups": {
            "a": {"diagnosis": "DIAGNOSTICO MAL QA", "items": []},
            "b": {"diagnosis": "CEFALEA M", "items": []},
        },
    }
    dialog._loaded(data)
    while dialog._populating:
        qt.processEvents()
    row = next(
        row
        for row in range(dialog.diagnoses.rowCount())
        if dialog.diagnoses.item(row, 0).text() == "DIAGNOSTICO MAL QA"
    )
    dialog.diagnoses.item(row, 0).setText("DIAGNOSTICO CORRECTO QA")
    dialog.diagnoses.selectRow(1 - row)
    dialog.remove_rows(dialog.diagnoses)
    config = dialog.configuration()
    assert config["diagnoses"][0]["aliases"] == ["DIAGNOSTICO MAL QA"]
    catalog = DiagnosisCatalog(config)
    assert catalog.normalize("DIAGNOSTICO MAL QA") == "DIAGNOSTICO CORRECTO QA"
    reopened = admin.prepare_admin_data({**data, "config": {**config, "version": 1}})
    assert [row["name"] for row in reopened["definitions"]] == [
        "DIAGNOSTICO CORRECTO QA"
    ]
    dialog.close()
