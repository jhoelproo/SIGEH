"""Maintenance operations on disposable PostgreSQL only."""

import pytest
import CALCULOS_QT as app
from ars_management import current_insurer_counts, replace_current_insurer
from admission_visit_dates import history_date_range, history_date_sql
from tests.test_ars_material_rules_postgres import catalog as catalog, ADMIN, set_links
from tests.test_receipt_optional_uuid_postgres import receipts as receipts
from tests.test_billing_consistency_postgres import server as server


def insurer_pair():
    with app.db_connect() as con:
        for name, room in (("MAPFRE", 500), ("MAPFRE/PALIC", 700)):
            con.execute(
                "INSERT INTO ars(nombre,sala_emergencia) VALUES(%s,%s)", (name, room)
            )
        con.execute(
            "INSERT INTO ars_items(ars_id,categoria,nombre,precio) SELECT id,'Laboratorios','COMUN',100 FROM ars WHERE nombre='MAPFRE'"
        )
        con.execute(
            "INSERT INTO ars_items(ars_id,categoria,nombre,precio) SELECT id,'Laboratorios','NUEVO',50 FROM ars WHERE nombre='MAPFRE'"
        )
        con.execute(
            "INSERT INTO ars_items(ars_id,categoria,nombre,precio) SELECT id,'Laboratorios','COMUN',200 FROM ars WHERE nombre='MAPFRE/PALIC'"
        )
        receipt = con.execute(
            "INSERT INTO recibos(numero,nombre,fecha,dx,ars,sala,total) VALUES(700,'QA','2026-11-04','QA','MAPFRE',500,588) RETURNING id"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO recibo_items(recibo_id,categoria,nombre,precio_unit,cantidad,total,ars) VALUES(%s,'Laboratorios','COMUN',88,1,88,'MAPFRE')",
            (receipt,),
        )
    return receipt


def test_merge_preserves_tariffs_historical_amounts_and_identifiers(catalog):
    identity = insurer_pair()
    preview = app.preview_ars_merge("MAPFRE", "MAPFRE/PALIC", ADMIN)
    app.merge_billing_insurers("MAPFRE", "MAPFRE/PALIC", ADMIN, preview["token"])
    with app.db_connect() as con:
        receipt = con.execute(
            "SELECT * FROM recibos WHERE id=%s", (identity,)
        ).fetchone()
        assert receipt["ars"] == "MAPFRE/PALIC" and receipt["total"] == 588
        assert receipt["revision_version"] == 1
        assert con.execute("SELECT count(*) FROM recibos").fetchone()[0] == 1
        item = con.execute(
            "SELECT * FROM recibo_items WHERE recibo_id=%s", (identity,)
        ).fetchone()
        assert item["precio_unit"] == item["total"] == 88 and item["cantidad"] == 1
        assert (
            con.execute("SELECT is_active FROM ars WHERE nombre='MAPFRE'").fetchone()[0]
            == 0
        )
        assert app.get_emergency_price("MAPFRE/PALIC") == 700
        assert app.get_ars_items("Laboratorios", "MAPFRE/PALIC") == {
            "COMUN": 200,
            "NUEVO": 50,
        }


@pytest.mark.parametrize("reason", ["stale", "denied", "different_family"])
def test_merge_validation_keeps_records_intact(catalog, reason):
    insurer_pair()
    preview = app.preview_ars_merge("MAPFRE", "MAPFRE/PALIC", ADMIN)
    if reason == "stale":
        with app.db_connect() as con:
            con.execute(
                "UPDATE ars SET sala_emergencia=701 WHERE nombre='MAPFRE/PALIC'"
            )
    source, destination = (
        ("HUMANO", "PRIMERA")
        if reason == "different_family"
        else ("MAPFRE", "MAPFRE/PALIC")
    )
    actor = dict(ADMIN, role=app.ROLE_AUX) if reason == "denied" else ADMIN
    if reason == "denied":
        actor["username"] = "rules-aux"
    with pytest.raises((ValueError, PermissionError)):
        app.merge_billing_insurers(source, destination, actor, preview["token"])
    with app.db_connect() as con:
        assert (
            con.execute("SELECT is_active FROM ars WHERE nombre='MAPFRE'").fetchone()[0]
            == 1
        )
        assert con.execute("SELECT ars FROM recibos").fetchone()[0] == "MAPFRE"


def test_merge_renames_when_admission_name_has_no_billing_row(catalog):
    with app.db_connect() as con:
        identity = con.execute(
            "INSERT INTO ars(nombre,sala_emergencia) VALUES('HUMANO/PRIMERA',450) RETURNING id"
        ).fetchone()[0]
    plan = app.preview_ars_merge("HUMANO/PRIMERA", "PRIMERA", ADMIN)
    app.merge_billing_insurers("HUMANO/PRIMERA", "PRIMERA", ADMIN, plan["token"])
    with app.db_connect() as con:
        row = con.execute(
            "SELECT id,sala_emergencia FROM ars WHERE nombre='PRIMERA'"
        ).fetchone()
        assert row[0] == identity and row[1] == 450


def test_merge_rolls_back_when_audit_fails(catalog, monkeypatch):
    insurer_pair()
    plan = app.preview_ars_merge("MAPFRE", "MAPFRE/PALIC", ADMIN)

    def fail(*_args):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(app, "_audit_billing_item_rule", fail)
    with pytest.raises(RuntimeError):
        app.merge_billing_insurers("MAPFRE", "MAPFRE/PALIC", ADMIN, plan["token"])
    with app.db_connect() as con:
        assert con.execute("SELECT ars FROM recibos").fetchone()[0] == "MAPFRE"


def test_catalog_delete_is_real_and_unlinks_material_without_removing_receipt(catalog):
    _, ids = catalog
    set_links(ids, {ids["MAT QA"]: 1})
    receipt = insurer_pair()
    app.mutate_universal_catalog("DELETE", "Materiales", "MAT QA", current_user=ADMIN)
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT id FROM universal_items WHERE id=%s", (ids["MAT QA"],)
            ).fetchone()
            is None
        )
        assert (
            con.execute("SELECT count(*) FROM medication_material_links").fetchone()[0]
            == 0
        )
        assert (
            con.execute("SELECT total FROM recibos WHERE id=%s", (receipt,)).fetchone()[
                0
            ]
            == 588
        )


def test_directory_insurer_removal_is_visible_on_a_new_connection(catalog):
    from patient_directory import CentralPatientDirectoryRepository

    repository = CentralPatientDirectoryRepository(app.db_connect)
    repository.ensure_schema()
    repository.upsert_patient(
        {"nombre": "PACIENTE QA", "ars": "MAPFRE/PALIC", "nss": "12345678"},
        source_instance_id="QA",
        legacy_patient_id=1,
    )
    identity = repository.find_by_nss("12345678")["global_patient_id"]
    with app.db_connect() as con:
        assert current_insurer_counts(con) == [("MAPFRE/PALIC", 1)]
        assert (
            replace_current_insurer(
                con, "MAPFRE/PALIC", "SIN SEGURO", ADMIN, allowed=app.user_is_admin
            )
            == 1
        )
    with app.db_connect() as another_computer:
        row = another_computer.execute(
            "SELECT canonical_ars,server_revision FROM admission_patient_directory WHERE global_patient_id=%s",
            (identity,),
        ).fetchone()
        assert row[0] == "SIN SEGURO" and row[1] == 2
        assert current_insurer_counts(another_computer) == [("SIN SEGURO", 1)]


def test_merge_revives_destination_and_preserves_its_profile_and_decisions(catalog):
    from diagnosis_recommendation_store import load_config, save_config

    insurer_pair()
    with app.db_connect() as con:
        source = con.execute("SELECT id FROM ars WHERE nombre='MAPFRE'").fetchone()[0]
        target = con.execute(
            "SELECT id FROM ars WHERE nombre='MAPFRE/PALIC'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO billing_ars_profiles(ars,display_name,ars_rnc,updated_at,updated_by) "
            "VALUES('MAPFRE','ORIGEN','111',NOW()::TEXT,'rules-admin'),"
            "('MAPFRE/PALIC','DESTINO','222',NOW()::TEXT,'rules-admin')"
        )
        config = load_config(con)
        config["diagnoses"] = [
            {"name": "CEFALEA", "aliases": []},
            {"name": "FIEBRE", "aliases": []},
        ]
        rule = dict(
            ars_id=source,
            diagnosis="CEFALEA",
            specialty="GENERAL",
            category="Laboratorios",
            item="COMUN",
            mode="pin",
        )
        config["ars_rules"] = [
            rule,
            dict(rule, ars_id=target, mode="block"),
            dict(rule, diagnosis="FIEBRE", item="NUEVO"),
        ]
        before = save_config(
            con, config, ADMIN, config["version"], audit=app._audit_billing_item_rule
        )
        con.execute(
            "UPDATE ars SET is_active=0,billing_enabled=FALSE WHERE id=%s", (target,)
        )
    plan = app.preview_ars_merge("MAPFRE", "MAPFRE/PALIC", ADMIN)
    app.merge_billing_insurers("MAPFRE", "MAPFRE/PALIC", ADMIN, plan["token"])
    with app.db_connect() as another_computer:
        row = another_computer.execute(
            "SELECT is_active,billing_enabled FROM ars WHERE id=%s", (target,)
        ).fetchone()
        assert row[0] == 1 and row[1]
        config = load_config(another_computer)
        assert config["version"] == before + 1
        assert {rule["ars_id"] for rule in config["ars_rules"]} == {target}
        assert {rule["mode"] for rule in config["ars_rules"]} == {"pin", "block"}
        assert (
            another_computer.execute(
                "SELECT ars_rnc FROM billing_ars_profiles WHERE ars='MAPFRE/PALIC'"
            ).fetchone()[0]
            == "222"
        )


@pytest.mark.parametrize(
    "category,name", [("Medicamentos", "MED QA"), ("Materiales", "MAT QA")]
)
def test_delete_inactive_catalog_items_and_reject_stale_selection(
    catalog, category, name
):
    _, ids = catalog
    set_links(ids, {ids["MAT QA"]: 1})
    with app.db_connect() as con:
        con.execute("UPDATE universal_items SET is_active=0 WHERE nombre=%s", (name,))
    app.mutate_universal_catalog("DELETE", category, name, current_user=ADMIN)
    with pytest.raises(ValueError, match="no existe"):
        app.mutate_universal_catalog("DELETE", category, name, current_user=ADMIN)
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT id FROM universal_items WHERE nombre=%s", (name,)
            ).fetchone()
            is None
        )


def test_live_authority_protects_deletion_and_merge_preview(catalog):
    actor = {"username": "rules-aux", "role": app.ROLE_ADMIN}
    with pytest.raises(PermissionError):
        app.mutate_universal_catalog(
            "DELETE", "Medicamentos", "MED QA", current_user=actor
        )
    with pytest.raises(PermissionError):
        app.delete_ars("ARS REGLAS", actor)
    with pytest.raises(PermissionError):
        app.preview_ars_merge("MAPFRE", "MAPFRE/PALIC", actor)
    with pytest.raises(PermissionError):
        app.preview_ars_merge("MAPFRE", "MAPFRE/PALIC", dict(actor, role=app.ROLE_AUX))
    with app.db_connect() as con:
        assert con.execute(
            "SELECT id FROM universal_items WHERE nombre='MED QA'"
        ).fetchone()


def test_disabled_insurer_does_not_reappear_from_admission(catalog, monkeypatch):
    monkeypatch.setattr(
        app.AdmissionReadOnlyRepository,
        "list_canonical_ars",
        lambda _self: ["ARS REGLAS", "MAPFRE/PALIC"],
    )
    assert "ARS REGLAS" in app.ars_list()
    app.delete_ars("ARS REGLAS", ADMIN)
    assert "ARS REGLAS" not in app.ars_list()
    assert "MAPFRE/PALIC" in app.ars_list()


def test_postgres_range_uses_inclusive_calendar_dates(catalog):
    with app.db_connect() as con:
        con.execute("CREATE TEMP TABLE range_qa(service_date TEXT)")
        dates = ["02/11/2026", "2026-11-03", "04/11/2026", "04-11-2026", "2026-11-05"]
        for day in dates:
            con.execute("INSERT INTO range_qa VALUES(%s)", (day,))
        sql = (
            "SELECT service_date FROM range_qa p WHERE "
            + history_date_sql("p.service_date")
            + " BETWEEN %s AND %s"
        )
        assert [
            row[0]
            for row in con.execute(
                sql, history_date_range(("03/11/2026", "04/11/2026"))
            ).fetchall()
        ] == dates[1:4]


def test_missing_current_patient_records_are_safe_and_empty_target_rejected(catalog):
    with app.db_connect() as con:
        assert (
            replace_current_insurer(
                con, "NO EXISTE", "SIN SEGURO", ADMIN, allowed=app.user_is_admin
            )
            == 0
        )
        with pytest.raises(ValueError, match="nueva ARS"):
            replace_current_insurer(
                con, "NO EXISTE", "", ADMIN, allowed=app.user_is_admin
            )
