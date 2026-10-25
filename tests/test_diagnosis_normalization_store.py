"""Canonical learning replaces historical spelling variants without editing receipts."""

from copy import deepcopy
import json

import pytest

import CALCULOS_QT as app
from diagnosis_recommendation_schema import ensure_schema, schema_ready
from diagnosis_recommendation_store import (
    _diagnosis_labels,
    apply_changes,
    load_config,
    restore_model,
    save_config,
    synchronize,
)
from diagnosis_recommendations import DiagnosisCatalog
from tests.test_billing_consistency_postgres import server as server
from tests.test_diagnosis_recommendation_postgres import drain, learning as learning
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


def test_localized_spelling_variants_share_one_learning_diagnosis():
    catalog = DiagnosisCatalog({})
    labels = _diagnosis_labels(
        catalog, "ALTRAGIA DE BRAZO IZQUIERDO.; ARTRALGIA DE RODILLA DERECHA"
    )
    assert labels == {"artralgia": "ARTRALGIA"}


def test_punctuation_and_origin_are_canonical_learning_labels():
    labels = _diagnosis_labels(
        DiagnosisCatalog({}), "CEFALEA DE OI.; ABORTO IMCOMPLETO."
    )
    assert labels == {
        "cefalea de origen a investigar": "CEFALEA DE ORIGEN A INVESTIGAR",
        "aborto incompleto": "ABORTO INCOMPLETO",
    }


def test_learning_labels_use_administrator_vocabulary_for_known_spelling_edits():
    catalog = DiagnosisCatalog({"diagnoses": [{"name": "GASTROENTERITIS"}]})
    assert _diagnosis_labels(catalog, "GASTROENTERITTIS") == {
        "gastroenteritis": "GASTROENTERITIS"
    }


def test_rebuild_merges_old_groups_and_preserves_receipt_history(learning):
    diagnoses = [
        "ALTRAGIA DE BRAZO IZQUIERDO.",
        "ARTRALGIA DE RODILLA DERECHA",
        "ABORTO IMCOMPLETO",
        "ABORTO INCOMPLETO.",
    ]
    for number, diagnosis in enumerate(diagnoses, 1):
        save(
            numero=number,
            nombre=f"QA NORMALIZATION {number}",
            dx=diagnosis,
            grouped=[("Medicamentos", [("MED A", 10, 1, 10, "Medicamentos")])],
        )
    with app.db_connect() as con:
        original = [
            dict(row)
            for row in con.execute("SELECT * FROM recibos ORDER BY id").fetchall()
        ]
        items = [
            dict(row)
            for row in con.execute("SELECT * FROM recibo_items ORDER BY id").fetchall()
        ]
        con.execute("DELETE FROM sigeh_recommendations.jobs")
        con.execute(
            "INSERT INTO sigeh_recommendations.changes(change_key,payload) VALUES(%s,%s::jsonb)",
            (
                "group:altragia de brazo izquierdo::EMERGENCIOLOGÍA",
                '{"diagnosis":"ALTRAGIA DE BRAZO IZQUIERDO.","specialty":"EMERGENCIOLOGÍA","receipts":1,"items":[]}',
            ),
        )
        con.execute(
            "INSERT INTO sigeh_recommendations.observations(receipt_id,diagnoses,specialty,status) "
            "SELECT id,'{\"altragia de brazo izquierdo\":\"ALTRAGIA DE BRAZO IZQUIERDO.\"}'::jsonb,'EMERGENCIOLOGÍA','PENDIENTE' "
            "FROM recibos WHERE numero=1"
        )
    old = apply_changes({}, [])
    model = drain(old)
    assert set(model["groups"]) == {
        "artralgia::EMERGENCIOLOGÍA",
        "aborto incompleto::EMERGENCIOLOGÍA",
    }
    assert all(row["receipts"] == 2 for row in model["groups"].values())
    assert drain(deepcopy(model)) == model
    with app.db_connect() as con:
        assert [
            dict(row)
            for row in con.execute("SELECT * FROM recibos ORDER BY id").fetchall()
        ] == original
        assert [
            dict(row)
            for row in con.execute("SELECT * FROM recibo_items ORDER BY id").fetchall()
        ] == items
        assert (
            con.execute(
                "SELECT count(*) FROM sigeh_recommendations.observations"
            ).fetchone()[0]
            == 4
        )
        assert (
            con.execute("SELECT count(*) FROM sigeh_recommendations.jobs").fetchone()[0]
            == 0
        )
    with app.db_connect() as con:
        assert synchronize(con, model["cursor"])["changes"] == []


def test_older_client_evidence_is_reprocessed_after_initial_upgrade(learning):
    identity = save(dx="ARTRALGIA DE BRAZO IZQUIERDO")
    model = drain()
    with app.db_connect() as con:
        con.execute(
            "UPDATE sigeh_recommendations.observations SET normalization_version=0,"
            'diagnoses=\'{"altragia de brazo izquierdo":"ALTRAGIA DE BRAZO IZQUIERDO"}\'::jsonb '
            "WHERE receipt_id=%s",
            (identity,),
        )
        con.execute("DELETE FROM sigeh_recommendations.jobs")
    updated = drain(model)
    assert set(updated["groups"]) == {"artralgia::EMERGENCIOLOGÍA"}
    assert updated["groups"]["artralgia::EMERGENCIOLOGÍA"]["receipts"] == 1
    with app.db_connect() as con:
        assert con.execute(
            "SELECT normalization_version,diagnoses FROM sigeh_recommendations.observations WHERE receipt_id=%s",
            (identity,),
        ).fetchone()["diagnoses"] == {"artralgia": "ARTRALGIA"}
        assert (
            con.execute(
                "SELECT normalization_version FROM sigeh_recommendations.observations WHERE receipt_id=%s",
                (identity,),
            ).fetchone()[0]
            == 1
        )
    assert drain(updated) == updated


def test_additive_schema_upgrade_keeps_old_rows_and_is_idempotent(learning):
    identity = save(dx="CEFALEA DE OI.")
    drain()
    with app.db_connect() as con:
        generation = con.execute(
            "SELECT generation FROM sigeh_recommendations.settings"
        ).fetchone()[0]
        con.execute(
            "ALTER TABLE sigeh_recommendations.observations DROP COLUMN normalization_version"
        )
        assert not schema_ready(con)
        assert ensure_schema(con)
        assert schema_ready(con)
        assert not ensure_schema(con)
        assert (
            con.execute(
                "SELECT normalization_version FROM sigeh_recommendations.observations WHERE receipt_id=%s",
                (identity,),
            ).fetchone()[0]
            == 0
        )
        assert (
            con.execute(
                "SELECT generation FROM sigeh_recommendations.settings"
            ).fetchone()[0]
            == generation
        )
        assert (
            con.execute("SELECT dx FROM recibos WHERE id=%s", (identity,)).fetchone()[0]
            == "CEFALEA DE OI."
        )
        assert con.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid='sigeh_recommendations.observations'::regclass"
        ).fetchone()[0]
    assert (
        drain()["groups"]["cefalea de origen a investigar::EMERGENCIOLOGÍA"]["receipts"]
        == 1
    )


def test_normalization_upgrade_rolls_back_with_failed_transaction(learning):
    save(dx="ABORTO IMCOMPLETO")
    with app.db_connect() as con:
        con.execute("DELETE FROM sigeh_recommendations.jobs")
    with pytest.raises(RuntimeError, match="Synthetic rollback"):
        with app.db_connect() as con:
            synchronize(con)
            raise RuntimeError("Synthetic rollback")
    with app.db_connect() as con:
        assert (
            con.execute("SELECT config FROM sigeh_recommendations.settings").fetchone()[
                0
            ]
            == {}
        )
        assert (
            con.execute("SELECT count(*) FROM sigeh_recommendations.jobs").fetchone()[0]
            == 0
        )
        assert (
            con.execute(
                "SELECT count(*) FROM sigeh_recommendations.observations"
            ).fetchone()[0]
            == 0
        )
    assert drain()["groups"]["aborto incompleto::EMERGENCIOLOGÍA"]["receipts"] == 1


def test_legacy_conflicting_config_remains_readable_and_requires_explicit_resolution(
    learning,
):
    rule = {
        "diagnosis": "ARTRALGIA",
        "specialty": "GENERAL",
        "category": "Medicamentos",
        "item": "MED A",
        "mode": "pin",
        "priority": 1,
    }
    raw = {
        "diagnoses": [{"name": "ARTRALGIA"}, {"name": "ALTRALGIA"}],
        "rules": [rule, {**rule, "diagnosis": "ALTRALGIA", "mode": "block"}],
    }
    with app.db_connect() as con:
        con.execute(
            "UPDATE sigeh_recommendations.settings SET config=%s::jsonb",
            (json.dumps(raw),),
        )
        loaded = load_config(con)
        assert len(loaded["diagnoses"]) == 1 and len(loaded["rules"]) == 2
    model = drain()
    assert len(restore_model(model)["config"]["rules"]) == 2
    with pytest.raises(ValueError, match="contradictorias"):
        with app.db_connect() as con:
            save_config(
                con,
                loaded,
                learning,
                loaded["version"],
                audit=app._audit_billing_item_rule,
            )
