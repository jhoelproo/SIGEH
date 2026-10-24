"""Transactional rules against disposable PostgreSQL; no remote data."""

import pytest

import CALCULOS_QT as app
import billing_item_rule_store as rules
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_billing_consistency_postgres import server as server

ADMIN = {"username": "rules-admin", "role": app.ROLE_ADMIN}


@pytest.fixture
def catalog(receipts):
    with app.db_connect() as con:
        for username, role in (
            ("rules-admin", app.ROLE_ADMIN),
            ("rules-aux", app.ROLE_AUX),
        ):
            con.execute(
                """INSERT INTO users(full_name,username,role,password_hash,password_salt,
                   security_question,security_answer_hash,security_answer_salt,is_active,created_at)
                   VALUES(%s,%s,%s,'synthetic','synthetic','QA','synthetic','synthetic',1,'2026-10-23')""",
                (username, username, role),
            )
        con.execute("INSERT INTO ars(nombre,is_active) VALUES('ARS REGLAS',1)")
        ars = con.execute("SELECT id FROM ars WHERE nombre='ARS REGLAS'").fetchone()[0]
        identities = {}
        for category, name, price in (
            ("Medicamentos", "MED QA", 10),
            ("Materiales", "MAT QA", 2),
            ("Materiales", "MAT DOS", 3),
        ):
            row = con.execute(
                "INSERT INTO universal_items(categoria,nombre,precio,is_active) VALUES(%s,%s,%s,1) RETURNING id",
                (category, name, price),
            ).fetchone()
            identities[name] = row[0]
    return ars, identities


def set_links(identities, quantities, actor=ADMIN):
    return app.save_medication_material_links(identities["MED QA"], quantities, actor)


def set_policy(ars, policy):
    return app.save_ars_honorarium_prompt_settings(
        {}, ADMIN, specialty_changes={ars: policy}
    )


def test_materials_persist_across_connections_rename_and_empty_configuration(catalog):
    _, ids = catalog
    editor = set_links(ids, {ids["MAT QA"]: 2, ids["MAT DOS"]: 1000})
    assert editor["medication"]["materials_configured"]
    assert {row["units"] for row in editor["materials"]} == {2, 1000}
    with app.db_connect() as con:
        before = con.execute(
            "SELECT numeric_value,version FROM billing_pricing_settings"
        ).fetchone()
        con.execute(
            "UPDATE universal_items SET nombre='MAT RENOMBRADO' WHERE id=%s",
            (ids["MAT QA"],),
        )
    loaded = app.load_billing_material_rules()
    runtime = app.refresh_billing_material_catalog()
    assert runtime["links"] == loaded["links"]
    assert runtime["universal"]["Medicamentos"]["MED QA"] == 10
    assert {item["name"] for item in loaded["links"]["MED QA"]} == {
        "MAT RENOMBRADO",
        "MAT DOS",
    }
    set_links(ids, {})
    assert app.load_billing_material_rules()["links"]["MED QA"] == []
    with app.db_connect() as con:
        after = con.execute(
            "SELECT numeric_value,version FROM billing_pricing_settings"
        ).fetchone()
        assert after["numeric_value"] == before["numeric_value"]
        assert after["version"] == before["version"] + 1
        assert (
            con.execute(
                "SELECT COUNT(*) FROM action_history WHERE action='MEDICATION_MATERIAL_LINKS_CHANGED'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    "mutation", ["missing", "inactive", "medication", "zero", "too_many", "duplicate"]
)
def test_invalid_material_change_rolls_back(catalog, mutation):
    _, ids = catalog
    set_links(ids, {ids["MAT QA"]: 2})
    changes = {
        "missing": {2147483647: 1},
        "inactive": {ids["MAT DOS"]: 1},
        "medication": {ids["MED QA"]: 1},
        "zero": {ids["MAT QA"]: 0},
        "too_many": {ids["MAT QA"]: 1001},
        "duplicate": {ids["MAT QA"]: 1, str(ids["MAT QA"]): 2},
    }
    with app.db_connect() as con:
        con.execute(
            "UPDATE universal_items SET is_active=0 WHERE id=%s", (ids["MAT DOS"],)
        )
    before = app.load_billing_material_rules()
    with pytest.raises(ValueError):
        set_links(ids, changes[mutation])
    assert app.load_billing_material_rules() == before


@pytest.mark.parametrize(
    "actor,disable",
    [
        ({"username": "rules-aux", "role": app.ROLE_ADMIN}, False),
        (ADMIN, True),
        ({"username": "missing", "role": app.ROLE_ADMIN}, False),
    ],
)
def test_database_authority_overrides_forged_or_stale_actor(catalog, actor, disable):
    ars, ids = catalog
    if disable:
        with app.db_connect() as con:
            con.execute("UPDATE users SET is_active=0 WHERE username='rules-admin'")
    with pytest.raises(PermissionError):
        set_links(ids, {ids["MAT QA"]: 1}, actor)
    with pytest.raises(PermissionError):
        app.save_ars_honorarium_prompt_settings(
            {}, actor, specialty_changes={ars: {"GENERAL": False}}
        )
    assert "MED QA" not in app.load_billing_material_rules()["links"]


def test_audit_failure_rolls_back_links_and_version(catalog, monkeypatch):
    _, ids = catalog
    before = app.load_billing_material_rules()

    def fail(*_args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(app, "_audit_billing_item_rule", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        set_links(ids, {ids["MAT QA"]: 1})
    assert app.load_billing_material_rules() == before


def test_policy_three_specialties_and_no_duplicate_audit(catalog):
    ars, _ = catalog
    policy = {"GENERAL": False, "PEDIATRIA": True, "GINECOLOGIA": False}
    set_policy(ars, policy)
    set_policy(ars, policy)
    with app.db_connect() as con:
        persisted = con.execute(
            "SELECT honorarium_specialties FROM ars WHERE id=%s", (ars,)
        ).fetchone()[0]
        assert persisted == {
            "EMERGENCIOLOGÍA": False,
            "PEDIATRÍA": True,
            "GINECOLOGÍA": False,
        }
        assert (
            con.execute(
                "SELECT COUNT(*) FROM action_history WHERE action='ARS_SPECIALTY_HONORARIUM_CHANGED'"
            ).fetchone()[0]
            == 1
        )
        for specialty in ("GENERAL", "GINECOLOGIA"):
            with pytest.raises(ValueError, match="no cobra honorarios"):
                rules.validate_honorarium_charge(
                    con,
                    "ARS REGLAS",
                    specialty,
                    [("Honorarios", "DIA", 100, 1, 100, "ARS REGLAS")],
                )
        rules.validate_honorarium_charge(
            con,
            "ARS REGLAS",
            "PEDIATRIA",
            [("Honorarios", "DIA", 100, 1, 100, "ARS REGLAS")],
        )


def test_persistence_rejects_forbidden_fee_without_partial_receipt(catalog):
    ars, _ = catalog
    set_policy(ars, {"GENERAL": False})
    with pytest.raises(ValueError, match="no cobra honorarios"):
        save(
            ars="ARS REGLAS",
            grouped=[("Honorarios", [("DIA", 100, 1, 100, "ARS REGLAS")])],
        )
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 0
    identity = save(ars="ARS REGLAS")
    assert identity > 0


def test_historical_fees_preserved_but_new_or_increased_fees_blocked(catalog):
    ars, _ = catalog
    grouped = [("Honorarios", [("DIA", 100, 2, 200, "ARS REGLAS")])]
    identity = save(ars="ARS REGLAS", total=200, grouped=grouped)
    set_policy(ars, {"GENERAL": False})
    assert (
        save(recibo_id=identity, ars="ARS REGLAS", total=200, grouped=grouped)
        == identity
    )
    for fee in [
        ("DIA", 100, 3, 300, "ARS REGLAS"),
        ("NOCHE", 100, 1, 100, "ARS REGLAS"),
        ("DIA", 101, 1, 101, "ARS REGLAS"),
    ]:
        with pytest.raises(ValueError, match="no cobra honorarios"):
            save(recibo_id=identity, ars="ARS REGLAS", grouped=[("Honorarios", [fee])])
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert (
            con.execute(
                "SELECT cantidad,total FROM recibo_items WHERE recibo_id=%s",
                (identity,),
            ).fetchone()["cantidad"]
            == 2
        )


def test_schema_idempotent_and_links_private(catalog):
    _, ids = catalog
    set_links(ids, {ids["MAT QA"]: 1})
    with app.db_connect() as con:
        con.executescript(rules.SCHEMA_SQL)
        con.executescript(rules.SCHEMA_SQL)
        assert (
            con.execute("SELECT COUNT(*) FROM medication_material_links").fetchone()[0]
            == 1
        )
        assert con.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid='medication_material_links'::regclass"
        ).fetchone()[0]
        assert (
            con.execute(
                "SELECT COUNT(*) FROM pg_policies WHERE tablename='medication_material_links'"
            ).fetchone()[0]
            == 0
        )
        assert (
            con.execute(
                "SELECT COUNT(*) FROM information_schema.role_table_grants WHERE table_name='medication_material_links' AND grantee='PUBLIC'"
            ).fetchone()[0]
            == 0
        )


def test_missing_medication_and_ars_do_not_change_configuration(catalog):
    ars, ids = catalog
    with pytest.raises(ValueError, match="medicamento"):
        app.save_medication_material_links(ids["MAT QA"], {}, ADMIN)
    with pytest.raises(ValueError, match="medicamento"):
        app.load_medication_material_editor(2147483647)
    with pytest.raises(ValueError, match="ARS"):
        app.save_ars_honorarium_prompt_settings(
            {}, ADMIN, specialty_changes={ars: {"GENERAL": False}, 2147483647: {}}
        )
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT honorarium_specialties FROM ars WHERE id=%s", (ars,)
            ).fetchone()[0]
            == {}
        )


def test_inactive_link_visible_to_editor_and_runtime(catalog):
    _, ids = catalog
    set_links(ids, {ids["MAT QA"]: 1})
    with app.db_connect() as con:
        con.execute(
            "UPDATE universal_items SET is_active=0 WHERE id=%s", (ids["MAT QA"],)
        )
    assert not app.load_billing_material_rules()["links"]["MED QA"][0]["active"]
    editor = app.load_medication_material_editor(ids["MED QA"])
    assert any(
        row["id"] == ids["MAT QA"] and row["units"] == 1 for row in editor["materials"]
    )
