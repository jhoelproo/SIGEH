"""Use the real eligibility query with synthetic data, including cross-station edits."""

import json
from pathlib import Path

import pytest

import CALCULOS_QT as app
from query_snapshot_cache import QuerySnapshotCache
from tests.test_billing_consistency_postgres import (
    SOURCE,
    USER,
    database as database,
    server as server,
)


def service():
    return app.BillingAdmissionQueryService(snapshot_cache=QuerySnapshotCache())


def candidates(reader):
    return reader.get_operational_candidates(current_user=USER, session_id="A")


@pytest.mark.parametrize(
    "change",
    [
        "patient_name='CORRECTED NAME'",
        "nss_snapshot='001234567890123456789'",
        "authorization_snapshot='000123'",
        "specialty='PEDIATRIA'",
    ],
)
def test_other_station_edit_is_visible_without_incrementing_revision(database, change):
    reader = service()
    before = candidates(reader)
    assert len(before) == 1
    assert candidates(reader) == before
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET " + change)
    after = candidates(reader)
    assert after != before
    assert after == candidates(app.BillingAdmissionQueryService())


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE admission_attention_projection SET is_deleted=TRUE",
        "UPDATE admission_attention_projection SET source_status='ANULADA'",
        "UPDATE ars SET billing_enabled=FALSE",
        "INSERT INTO recibos(id,admission_atencion_id,admission_source_instance_id) VALUES(1,329,'ORIGIN')",
        "INSERT INTO admission_billing_claims(source_instance_id,attention_id,claimed_by,session_id,station_id,expires_at) VALUES('ORIGIN',329,'other','B','TEST-B',NOW()+INTERVAL '1 hour')",
    ],
)
def test_external_eligibility_change_never_leaves_an_old_candidate(database, change):
    reader = service()
    assert len(candidates(reader)) == 1
    with database() as con:
        con.execute(change)
    assert candidates(reader) == []
    assert candidates(reader) == []


def test_sort_only_update_changes_the_order_of_cached_results(database):
    with database() as con:
        con.execute(
            "INSERT INTO admission_attention_projection(attention_id,operational_source_id,created_at_effective_utc) VALUES(330,%s,NOW()+INTERVAL '1 hour')",
            (SOURCE,),
        )
    reader = service()
    assert [r.attention_id for r in candidates(reader)] == [330, 329]
    with database() as con:
        con.execute(
            "UPDATE admission_attention_projection SET created_at_effective_utc=NOW()+INTERVAL '2 hours' WHERE attention_id=329"
        )
    assert [r.attention_id for r in candidates(reader)] == [329, 330]


def test_comparative_transfer_of_the_real_queue_preserves_all_rows(database):
    with database() as con:
        con.execute(
            "INSERT INTO admission_attention_projection(attention_id,operational_source_id,patient_name) SELECT n,%s,'SYNTHETIC '||n FROM generate_series(330,428) n",
            (SOURCE,),
        )
    baseline = app.BillingAdmissionQueryService()
    optimized = service()
    byte_counts = {"baseline": 0, "optimized": 0}
    query_counts = {"baseline": 0, "optimized": 0}

    def measure(reader, name):
        original = reader.central_reader.fetch_all

        def fetch(sql, params, **options):
            rows, timings = original(sql, params, **options)
            byte_counts[name] += len(json.dumps(rows, default=str).encode())
            query_counts[name] += 1
            return rows, timings

        reader.central_reader.fetch_all = fetch

    measure(baseline, "baseline")
    measure(optimized, "optimized")
    for _ in range(30):
        expected = candidates(baseline)
        actual = candidates(optimized)
        assert actual == expected
        assert len(actual) == 100
    reduction = 1 - byte_counts["optimized"] / byte_counts["baseline"]
    assert reduction > 0.9
    evidence = {
        "scenario": "real billing query; 100 synthetic candidates; 30 unchanged refreshes",
        "bytes": byte_counts,
        "queries": query_counts,
        "reduction_percent": reduction * 100,
        "wire_bytes": False,
        "monthly_consumption_projection": False,
    }
    target = Path("output/supabase-free-billing-comparison.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
