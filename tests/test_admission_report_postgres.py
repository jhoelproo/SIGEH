from datetime import date

import pytest

from admission_report_tools import load_billing_followup
from admission_turn_history import adjacent_turn, search_turns
from tests.test_billing_consistency_postgres import GLOBAL, SOURCE


pytest_plugins = ["tests.test_billing_consistency_postgres"]


@pytest.fixture
def report_database(database):
    with database() as connection:
        connection.execute("DELETE FROM admission_attention_projection")
        connection.execute("DELETE FROM recibos")
        connection.execute(
            "ALTER TABLE admission_operational_sessions ADD COLUMN turn_ends_at TIMESTAMPTZ"
        )
        connection.execute(
            "ALTER TABLE admission_operational_turn_intervals ADD COLUMN nominal_ends_at TIMESTAMPTZ, ADD COLUMN active_username TEXT"
        )
        connection.execute("CREATE TABLE users(username TEXT UNIQUE,full_name TEXT)")
        connection.execute("INSERT INTO users VALUES('operator','Operator')")
        connection.execute(
            "ALTER TABLE recibos ADD COLUMN dx TEXT, ADD COLUMN numero_autorizacion TEXT, ADD COLUMN tipo_cobertura TEXT, ADD COLUMN ars TEXT"
        )
        yield connection


def test_adjacent_turn_reads_one_metadata_row_with_stable_boundaries(report_database):
    connection = report_database
    connection.execute("""INSERT INTO admission_operational_turn_intervals(
        operational_session_id,turn_id,started_at,ended_at,active_username)
        SELECT 'test',n,'2026-09-18 08:00:00-04'::TIMESTAMPTZ + n*INTERVAL '1 second',
               '2026-09-19 08:00:00-04','operator' FROM generate_series(1,3) n""")
    turns = search_turns(connection, date(2026, 9, 18), date(2026, 9, 18))
    assert adjacent_turn(connection, turns[1], "previous")["turn_id"] == 1
    assert adjacent_turn(connection, turns[1], "next")["turn_id"] == 3
    assert adjacent_turn(connection, turns[0], "next") is None
    assert adjacent_turn(connection, turns[-1], "previous") is None
    with pytest.raises(ValueError):
        adjacent_turn(connection, turns[0], "bad")


def test_followup_links_global_identity_and_keeps_all_active_receipts(report_database):
    connection = report_database
    connection.execute(
        """INSERT INTO admission_attention_projection(
        attention_id,global_attention_id,operational_source_id)
        VALUES(1,%s,%s),(2,NULL,%s)""",
        (GLOBAL, SOURCE, SOURCE),
    )
    connection.execute(
        """INSERT INTO recibos(
        id,numero,admission_global_attention_id,admission_atencion_id,
        admission_source_instance_id,is_deleted,dx,numero_autorizacion)
        VALUES(10,10,%s,99,'OTHER',0,'CEFALEA','0012'),
              (11,11,%s,99,'OTHER',0,'CEFALEA','0013'),
              (12,12,%s,1,'ORIGIN',1,'CEFALEA','0014'),
              (13,13,NULL,2,'ORIGIN',0,'CEFALEA','0015'),
              (14,14,NULL,2,'OTHER',0,'CEFALEA','0016')""",
        (GLOBAL, GLOBAL, GLOBAL),
    )
    records = [
        {"attention_id": 1, "global_attention_id": GLOBAL},
        {"attention_id": 2, "source_instance_id": "ORIGIN"},
    ]
    result = load_billing_followup(connection, records)
    assert {row["receipt_id"] for row in result} == {10, 11, 13}
    assert (
        next(row for row in result if row["receipt_id"] == 10)["numero_autorizacion"]
        == "0012"
    )
