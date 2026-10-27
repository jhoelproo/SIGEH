"""Learning uses actual saved receipts in a disposable PostgreSQL instance."""

from concurrent.futures import ThreadPoolExecutor

import pytest
import CALCULOS_QT as app
from diagnosis_recommendations import group_key, recommendations
from diagnosis_recommendation_store import (
    available,
    apply_response,
    apply_changes,
    load_config,
    save_config,
    synchronize,
)
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save

ADMIN = {"username": "admin", "role": app.ROLE_ADMIN}


@pytest.fixture
def learning(receipts):
    with app.db_connect() as con:
        con.execute(
            "TRUNCATE sigeh_recommendations.observations,sigeh_recommendations.jobs,sigeh_recommendations.changes CASCADE"
        )
        con.execute(
            "UPDATE sigeh_recommendations.settings SET config='{}'::jsonb,version=0"
        )
        con.execute("UPDATE sigeh_recommendations.config_extensions SET config='{}'::jsonb")
    return ADMIN


def medication_receipt(number, name="MED A", **changes):
    return save(
        numero=number,
        nombre=f"QA DIAG {number}",
        dx="DIAGNÓSTICO ALFA",
        grouped=[("Medicamentos", [(name, 10, 1, 10, "Medicamentos")])],
        **changes,
    )


def drain(model=None):
    model = model or apply_changes({}, [])
    for _ in range(50):
        with app.db_connect() as con:
            data = synchronize(con, model["cursor"], generation=model.get("generation"))
        model = apply_response(model, data)
        if not data["more"]:
            return model
    raise AssertionError("Learning queue did not drain")


def test_ten_receipts_generate_one_principal_and_reprint_does_not_double_count(
    learning,
):
    for number in range(1, 11):
        medication_receipt(number)
    model = drain()
    group = model["groups"][group_key("diagnostico alfa", "GENERAL")]
    assert group["receipts"] == 10 and group["items"][0]["uses"] == 10
    assert (
        recommendations(
            model, "diagnostico alfa", "GENERAL", {"Medicamentos": {"MED A": 99}}
        )["Medicamentos"][0]["item"]
        == "MED A"
    )
    with app.db_connect() as con:
        con.execute("UPDATE recibos SET pdf_filename='reprint.pdf' WHERE numero=1")
    assert drain(model) == model
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT count(*) FROM sigeh_recommendations.observations"
            ).fetchone()[0]
            == 10
        )


def test_edit_status_trash_restore_and_purge_replace_the_original_observation(learning):
    identity = medication_receipt(1)
    model = drain()
    key = group_key("diagnostico alfa", "GENERAL")
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibo_items SET nombre='MED B',cantidad=12,total=120 WHERE recibo_id=%s",
            (identity,),
        )
        con.execute(
            "UPDATE recibos SET estado_facturacion='FACTURADO' WHERE id=%s", (identity,)
        )
    model = drain(model)
    row = model["groups"][key]["items"][0]
    assert row["item"] == "MED B" and row["uses"] == 1 and row["invoiced"] == 1
    with app.db_connect() as con:
        con.execute("UPDATE recibos SET is_deleted=1 WHERE id=%s", (identity,))
    model = drain(model)
    assert key not in model["groups"]
    with app.db_connect() as con:
        con.execute("UPDATE recibos SET is_deleted=0 WHERE id=%s", (identity,))
    model = drain(model)
    assert model["groups"][key]["receipts"] == 1


def test_two_clients_receive_deltas_and_second_poll_is_empty(learning):
    medication_receipt(1)
    one = drain()
    two = drain()
    assert one == two
    with app.db_connect() as con:
        assert synchronize(con, two["cursor"])["changes"] == []
        con.execute("UPDATE recibos SET specialty_snapshot='PEDIATRÍA' WHERE numero=1")
    one = drain(one)
    two = drain(two)
    assert one == two
    assert group_key("diagnostico alfa", "GENERAL") not in one["groups"]
    assert group_key("diagnostico alfa", "PEDIATRÍA") in one["groups"]


def test_admin_configuration_is_authoritative_and_audited(learning):
    with app.db_connect() as con:
        con.execute(
            "INSERT INTO universal_items(categoria,nombre,precio,is_active) VALUES('Medicamentos','MED A',10,1) ON CONFLICT DO NOTHING"
        )
        con.execute("UPDATE users SET role='auxiliar' WHERE username='admin'")
    config = {
        "diagnoses": [{"name": "DIAGNÓSTICO ALFA", "aliases": ["DA"]}],
        "rules": [],
    }
    with pytest.raises(PermissionError):
        with app.db_connect() as con:
            save_config(con, config, ADMIN, 0, audit=app._audit_billing_item_rule)
    with app.db_connect() as con:
        con.execute("UPDATE users SET role='administrador' WHERE username='admin'")
        assert (
            save_config(con, config, ADMIN, 0, audit=app._audit_billing_item_rule) == 1
        )
    with app.db_connect() as con:
        assert load_config(con)["diagnoses"][0]["aliases"] == ["DA"]
        assert (
            con.execute(
                "SELECT count(*) FROM action_history WHERE action='RECOMMENDATION_CONFIG_CHANGED'"
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(ValueError, match="Otro administrador"):
        with app.db_connect() as con:
            save_config(con, config, ADMIN, 0, audit=app._audit_billing_item_rule)


def test_unknown_multiple_medication_pairs_remain_pending_for_admin(learning):
    save(
        numero=1,
        dx="ALFA; BETA",
        grouped=[
            (
                "Medicamentos",
                [
                    ("MED A", 10, 1, 10, "Medicamentos"),
                    ("MED B", 10, 1, 10, "Medicamentos"),
                ],
            )
        ],
    )
    model = drain()
    for group in model["groups"].values():
        assert all(
            row["uses"] == 0 and row["candidates"] == 1 for row in group["items"]
        )
    assert not recommendations(
        model, "ALFA; BETA", "GENERAL", {"Medicamentos": {"MED A": 1, "MED B": 2}}
    )["Medicamentos"]


def test_saving_rollback_does_not_leave_learning_jobs(learning):
    with pytest.raises(RuntimeError):
        with app.db_connect() as con:
            con.execute(
                "INSERT INTO recibos(numero,nombre,fecha,dx,ars,sala,total,username,created_at) VALUES(1,'QA','2026-10-24','ALFA','FUTURO',0,0,'qa','2026-10-24')"
            )
            raise RuntimeError("Interrupted receipt transaction")
    with app.db_connect() as con:
        assert (
            con.execute("SELECT count(*) FROM sigeh_recommendations.jobs").fetchone()[0]
            == 0
        )


def test_concurrent_processors_do_not_duplicate_evidence(learning):
    for number in range(1, 11):
        medication_receipt(number)

    def process():
        with app.db_connect() as con:
            return synchronize(con)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: process(), range(2)))
    assert all(row["installed"] for row in results)
    model = drain()
    assert model["groups"][group_key("diagnostico alfa", "GENERAL")]["receipts"] == 10
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT count(*) FROM sigeh_recommendations.evidence WHERE confirmed"
            ).fetchone()[0]
            == 10
        )


def test_eligible_legacy_purge_removes_its_observation(learning):
    with app.db_connect() as con:
        identity = con.execute(
            "INSERT INTO recibos(numero,nombre,fecha,dx,ars,sala,total,username,created_at,estado_facturacion) VALUES(1,'QA LEGACY','2026-10-24','ALFA','FUTURO',0,10,'qa','2026-10-24','PENDIENTE') RETURNING id"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO recibo_items(recibo_id,categoria,nombre,precio_unit,cantidad,total) VALUES(%s,'Medicamentos','MED A',10,1,10)",
            (identity,),
        )
    model = drain()
    assert group_key("ALFA", "GENERAL") in model["groups"]
    with app.db_connect() as con:
        con.execute("DELETE FROM recibos WHERE id=%s", (identity,))
    assert group_key("ALFA", "GENERAL") not in drain(model)["groups"]


def test_admin_rule_change_reprocesses_history_and_block_wins_immediately(learning):
    medication_receipt(1)
    model = drain()
    with app.db_connect() as con:
        con.execute(
            "INSERT INTO universal_items(categoria,nombre,precio,is_active) VALUES('Medicamentos','MED A',10,1) ON CONFLICT DO NOTHING"
        )
        value = {
            "diagnoses": [{"name": "DIAGNÓSTICO ALFA", "aliases": ["DA"]}],
            "rules": [
                {
                    "diagnosis": "DA",
                    "specialty": "GENERAL",
                    "category": "Medicamentos",
                    "item": "MED A",
                    "mode": "block",
                    "priority": 1,
                }
            ],
        }
        save_config(con, value, ADMIN, 0, audit=app._audit_billing_item_rule)
    model = drain(model)
    assert (
        model["groups"][group_key("diagnostico alfa", "GENERAL")]["items"][0]["uses"]
        == 0
    )
    assert not recommendations(model, "DA", "GENERAL", {"Medicamentos": {"MED A": 10}})[
        "Medicamentos"
    ]


def test_invalid_catalog_rule_and_audit_failure_roll_back_configuration(learning):
    value = {
        "diagnoses": [{"name": "ALFA", "aliases": []}],
        "rules": [
            {
                "diagnosis": "ALFA",
                "specialty": "GENERAL",
                "category": "Medicamentos",
                "item": "NO EXISTE",
                "mode": "pin",
                "priority": 1,
            }
        ],
    }
    with pytest.raises(ValueError, match="inexistentes"):
        with app.db_connect() as con:
            save_config(con, value, ADMIN, 0, audit=app._audit_billing_item_rule)
    value["rules"] = []

    def broken_audit(*_args):
        raise RuntimeError("Synthetic audit failure")

    with pytest.raises(RuntimeError, match="audit failure"):
        with app.db_connect() as con:
            save_config(con, value, ADMIN, 0, audit=broken_audit)
    with app.db_connect() as con:
        assert load_config(con)["version"] == 0


def test_cancelled_processing_rolls_back_without_losing_pending_jobs(learning):
    medication_receipt(1)
    with pytest.raises(InterruptedError):
        with app.db_connect() as con:
            synchronize(con, interrupted=lambda: True)
    with app.db_connect() as con:
        assert (
            con.execute("SELECT count(*) FROM sigeh_recommendations.jobs").fetchone()[0]
            == 1
        )
    assert drain()["groups"]


def test_cache_cursor_resets_after_restore_or_central_generation_change(learning):
    medication_receipt(1)
    model = drain()
    with app.db_connect() as con:
        data = synchronize(con, model["cursor"] + 1000, generation=model["generation"])
    assert data["reset"] and apply_response(model, data)["groups"] == model["groups"]
    with app.db_connect() as con:
        con.execute(
            "UPDATE sigeh_recommendations.settings SET generation=gen_random_uuid()"
        )
    with app.db_connect() as con:
        data = synchronize(con, model["cursor"], generation=model["generation"])
    assert data["reset"] and data["generation"] != model["generation"]


def test_private_schema_is_not_available_to_an_unprivileged_role(learning):
    import psycopg2

    with app.db_connect() as con:
        con.execute("CREATE ROLE recommendation_reader NOLOGIN")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with app.db_connect() as con:
            con.execute("SET LOCAL ROLE recommendation_reader")
            con.execute("SELECT * FROM sigeh_recommendations.changes")


def test_administrator_gui_loads_and_saves_real_shared_configuration(learning):
    from diagnosis_recommendation_admin import RecommendationAdminDialog
    from tests.test_diagnosis_recommendation_runtime import wait_task

    with app.db_connect() as con:
        con.execute(
            "INSERT INTO universal_items(categoria,nombre,precio,is_active) VALUES('Medicamentos','MED GUI',10,1) ON CONFLICT DO NOTHING"
        )
    dialog = RecommendationAdminDialog(app, ADMIN, {})
    wait_task(dialog.worker)
    dialog.add_diagnosis("ALFA", ["A"])
    dialog.add_rule(
        {
            "diagnosis": "ALFA",
            "specialty": "EMERGENCIOLOGÍA",
            "category": "Medicamentos",
            "item": "MED GUI",
            "mode": "pin",
            "priority": 1,
        }
    )
    dialog.save()
    wait_task(dialog.worker)
    from PySide6.QtWidgets import QDialog

    assert dialog.result() == QDialog.Accepted
    with app.db_connect() as con:
        value = load_config(con)
    assert value["version"] == 1 and value["rules"][0]["item"] == "MED GUI"
    dialog.deleteLater()


def test_unrecognized_specialty_and_ambiguous_diagnosis_do_not_train(learning):
    identity = medication_receipt(1)
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibos SET specialty_snapshot='DESCONOCIDA' WHERE id=%s",
            (identity,),
        )
    assert not drain()["groups"]
    value = {
        "diagnoses": [
            {"name": "ALFA", "aliases": ["X"]},
            {"name": "BETA", "aliases": ["X"]},
        ],
        "rules": [],
    }
    with app.db_connect() as con:
        save_config(con, value, ADMIN, 0, audit=app._audit_billing_item_rule)
        con.execute(
            "UPDATE recibos SET specialty_snapshot='GENERAL',dx='X' WHERE id=%s",
            (identity,),
        )
    assert not drain()["groups"]


def test_missing_optional_schema_disables_learning_without_affecting_receipts(learning):
    medication_receipt(1)
    with app.db_connect() as con:
        con.execute("DROP SCHEMA sigeh_recommendations CASCADE")
    with app.db_connect() as con:
        assert synchronize(con) == {"changes": [], "more": False, "installed": False}
        assert con.execute("SELECT count(*) FROM recibos").fetchone()[0] == 1


def test_compatible_existing_installation_activates_learning_without_legacy_migration(
    learning, monkeypatch
):
    medication_receipt(1)
    with app.db_connect() as con:
        assert app.inspect_database_schema_compatibility(con)[0]
        con.execute("DROP SCHEMA sigeh_recommendations CASCADE")
        con.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),app_version TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO schema_migrations(migration_id,app_version) VALUES(%s,'qa') ON CONFLICT DO NOTHING",
            (app.BOOTSTRAP_SCHEMA_MIGRATION_ID,),
        )

    def forbidden_legacy_migration():
        raise AssertionError("Existing compatible installations must avoid legacy DDL")

    monkeypatch.setattr(app, "db_init", forbidden_legacy_migration)
    assert app.prepare_database_schema() == "ALREADY_COMPATIBLE"
    with app.db_connect() as con:
        assert available(con)
    assert drain()["groups"][group_key("diagnostico alfa", "GENERAL")]["receipts"] == 1
