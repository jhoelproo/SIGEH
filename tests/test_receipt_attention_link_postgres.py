"""Linking transactions against disposable PostgreSQL only."""

import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import CALCULOS_QT as app
from receipt_attention_link import link_receipt_to_inherited_attention
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_inherited_receipt_save import inherited as inherited


def link(identity, attention):
    return link_receipt_to_inherited_attention(
        identity, attention, "uuid_test", backend=app, session_id="A"
    )


def read_receipt(identity):
    with app.db_connect() as con:
        return dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (identity,)).fetchone()
        )


def test_link_preserves_original_receipt_and_resolves_pending(inherited):
    identity = save(authorization_number="")
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibos SET username='AUTOR_ORIGINAL' WHERE id=%s", (identity,)
        )
    before = read_receipt(identity)
    assert link(identity, inherited) == identity
    after = read_receipt(identity)
    for field in (
        "numero",
        "nombre",
        "fecha",
        "created_at",
        "username",
        "total",
        "sala",
        "ars",
        "tipo_cobertura",
        "pdf_filename",
        "estado_documento",
        "numero_autorizacion",
    ):
        assert after[field] == before[field], field
    assert after["admission_atencion_id"] == 372
    assert after["revision_version"] == before["revision_version"] + 1
    with app.db_connect() as con:
        assert (
            con.execute("SELECT estado FROM admission_shift_inheritances").fetchone()[0]
            == "COMPLETADA"
        )
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1
    with pytest.raises(ValueError):
        link(identity, inherited)


def test_audit_failure_rolls_back_entire_link(inherited, monkeypatch):
    identity = save()
    before = read_receipt(identity)

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(app, "_insert_action_history", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        link(identity, inherited)
    assert read_receipt(identity) == before
    with app.db_connect() as con:
        assert (
            con.execute("SELECT estado FROM admission_shift_inheritances").fetchone()[0]
            == "PENDIENTE"
        )


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE admission_attention_projection SET source_status='ANULADA'",
        "UPDATE admission_attention_projection SET is_deleted=TRUE",
        "UPDATE admission_attention_projection SET patient_name='OTRO PACIENTE'",
        "UPDATE admission_attention_projection SET canonical_ars='OTRA ARS'",
        "UPDATE admission_billing_claims SET session_id='OTHER',station_id='OTHER',claimed_by='OTHER'",
    ],
)
def test_live_changes_reject_stale_selection(inherited, change):
    identity = save()
    with app.db_connect() as con:
        con.execute(change)
    with pytest.raises(ValueError):
        link(identity, inherited)
    assert read_receipt(identity)["admission_atencion_id"] is None


@pytest.mark.parametrize("same_receipt", [False, True])
def test_concurrent_links_only_one_commits(inherited, same_receipt):
    first = save()
    second = first if same_receipt else save(numero=2, fecha="2026-09-06")
    barrier = Barrier(2)

    def attempt(identity):
        barrier.wait(timeout=10)
        try:
            link(identity, inherited)
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, [first, second])) == ["rejected", "saved"]
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM recibos WHERE admission_atencion_id=372"
            ).fetchone()[0]
            == 1
        )
        assert (
            con.execute(
                "SELECT COUNT(*) FROM action_history WHERE action='ADMISSION_ATTENTION_LINKED_LATER'"
            ).fetchone()[0]
            == 1
        )


def test_link_without_claim_and_missing_records(inherited):
    identity = save()
    with app.db_connect() as con:
        con.execute("DELETE FROM admission_billing_claims")
    with pytest.raises(ValueError, match="recibo ya no existe"):
        link(-1, inherited)
    selection = inherited.snapshot()
    selection["attention_id"] = -1
    with pytest.raises(ValueError, match="atención ya no existe"):
        link(identity, selection)
    assert link(identity, inherited) == identity


def test_uninsured_receipt_with_empty_ars_can_link(inherited):
    identity = save(authorization_number="")
    with app.db_connect() as con:
        con.execute("UPDATE recibos SET ars='',tipo_cobertura='NO_ASEGURADO' WHERE id=%s", (identity,))
        con.execute(
            "UPDATE admission_attention_projection SET canonical_ars='SIN SEGURO',coverage_status='SIN_SEGURO_DECLARADO'"
        )
    assert link(identity, inherited) == identity
    assert read_receipt(identity)["tipo_cobertura"] == "NO_ASEGURADO"


@pytest.mark.parametrize("turn", [3950, 123])
def test_admin_can_link_unbilled_current_or_historical_attention(inherited, turn):
    identity = save()
    with app.db_connect() as con:
        con.execute("DELETE FROM admission_shift_inheritances")
        con.execute("UPDATE admission_attention_projection SET turn_id=%s", (turn,))
    assert link(identity, inherited) == identity
    after = read_receipt(identity)
    assert after["admission_atencion_id"] == inherited.attention_id
    assert after["turno_origen_id"] == turn
    assert after["herencia_estado"] != "HEREDADA_PROCESADA"
    with app.db_connect() as con:
        assert (
            con.execute("SELECT COUNT(*) FROM admission_shift_inheritances").fetchone()[
                0
            ]
            == 0
        )


def test_historical_attention_with_another_billed_receipt_cannot_link(inherited):
    first = save()
    with app.db_connect() as con:
        con.execute("DELETE FROM admission_shift_inheritances")
        con.execute("UPDATE admission_attention_projection SET turn_id=123")
        con.execute(
            "UPDATE recibos SET admission_atencion_id=372,admission_source_instance_id='ORIGIN',estado_facturacion=%s WHERE id=%s",
            (app.BILLING_INVOICED, first),
        )
    second = save(numero=2, fecha="2026-09-06")
    with pytest.raises(ValueError):
        link(second, inherited)
    assert read_receipt(second)["admission_atencion_id"] is None
