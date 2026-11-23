"""History handoff through the same PostgreSQL claim and receipt lifecycle."""

import CALCULOS_QT as app
import pytest
from billing_history_handoff import billing_destination
from tests.test_inherited_receipt_save import GLOBAL, USER, inherited as inherited
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_billing_consistency_postgres import server as server


def evaluate():
    return app.evaluate_attention_billing_eligibility(
        0, USER, global_attention_id=GLOBAL, session_id="A"
    )


def test_history_reuses_inherited_receipt_after_first_save(inherited):
    origin_turn = inherited.turn_id
    assert billing_destination(evaluate()) == "claim"
    receipt_id = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    result = evaluate()
    assert result["receipt_id"] == receipt_id
    assert billing_destination(result) == "receipt"
    assert not result["eligible"]
    assert (
        save(
            recibo_id=receipt_id,
            admission_attention=inherited,
            admission_session_id="A",
            verification_bypass=None,
        )
        == receipt_id
    )
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        row = con.execute(
            "SELECT turn_id,source_status,is_deleted FROM admission_attention_projection "
            "WHERE global_attention_id=%s",
            (GLOBAL,),
        ).fetchone()
        assert row["turn_id"] == origin_turn
        assert row["source_status"] == "ACTIVA"
        assert not row["is_deleted"]


@pytest.mark.parametrize(
    "mutation",
    [
        "source_status='ANULADA'",
        "is_deleted=TRUE",
        "service_type='URGENCIA'",
    ],
)
def test_history_does_not_bypass_clinical_guards(inherited, mutation):
    with app.db_connect() as con:
        con.execute(
            f"UPDATE admission_attention_projection SET {mutation} "
            "WHERE global_attention_id=%s",
            (GLOBAL,),
        )
    assert billing_destination(evaluate()) == "blocked"
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 0


def test_uuid_lookup_uses_the_postgres_index(server):
    from tests.test_billing_consistency_postgres import Connection

    with Connection(server) as con:
        con.execute("""CREATE TEMP TABLE admission_attention_projection (
            global_attention_id UUID PRIMARY KEY, attention_id BIGINT,
            source_instance_id TEXT
        )""")
        con.execute("""INSERT INTO admission_attention_projection
            SELECT md5(n::text)::uuid, n, 'TEST'
            FROM generate_series(1, 100000) n""")
        con.execute(
            "INSERT INTO admission_attention_projection VALUES (%s, 0, 'TEST')",
            (GLOBAL,),
        )
        con.execute("ANALYZE admission_attention_projection")
        predicate, parameters = app._billing_projection_lookup(0, "", GLOBAL)
        query = """EXPLAIN (ANALYZE, FORMAT JSON) SELECT *
            FROM admission_attention_projection p WHERE """
        old_plan = con.execute(
            query + "p.global_attention_id::text=%s", (GLOBAL,)
        ).fetchone()["QUERY PLAN"][0]
        new_plan = con.execute(query + predicate, parameters).fetchone()["QUERY PLAN"][
            0
        ]
    print(
        f"UUID, 100000 filas: anterior={old_plan['Execution Time']:.3f} ms, "
        f"actual={new_plan['Execution Time']:.3f} ms"
    )
    assert "Index" in new_plan["Plan"]["Node Type"]
    assert old_plan["Plan"]["Actual Rows"] == new_plan["Plan"]["Actual Rows"] == 1
