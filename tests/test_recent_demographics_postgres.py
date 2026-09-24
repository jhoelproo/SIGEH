import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from admission_demographics import correct_recent_attentions
from patient_directory import (
    CentralPatientDirectoryRepository,
    POSTGRES_PATIENT_DIRECTORY_SCHEMA,
)
from tests import test_billing_consistency_postgres as pg

server = pg.server


def test_patient_edit_updates_master_and_recent_attention(demographic_database):
    patient_id, attention_id, session, source = (str(uuid4()) for _ in range(4))
    with demographic_database() as con:
        con.execute(
            """INSERT INTO admission_patient_directory(global_patient_id,patient_name)
                       VALUES(%s,'ORIGINAL')""",
            (patient_id,),
        )
        con.execute(
            """INSERT INTO admission_attention_projection(
            global_attention_id,global_patient_id,created_at_effective_utc,attention_id,
            latest_payload_json,operational_session_id,operational_source_id)
            VALUES(%s,%s,NOW()-INTERVAL '1 day',1,%s::jsonb,%s,%s)""",
            (
                attention_id,
                patient_id,
                json.dumps(
                    {
                        "name": "ORIGINAL",
                        "age": 42,
                        "generation": 99,
                        "turn_id": 99,
                        "service_date": "2026-09-23",
                    }
                ),
                session,
                source,
            ),
        )
    repository = CentralPatientDirectoryRepository(demographic_database)
    result = repository.update_patient(
        patient_id,
        {
            "nombre": "CORREGIDO",
            "telefono": "8090000000",
            "ars": "HUMANO",
            "nss": "1234567",
        },
        actor_user="SYNTHETIC",
        actor_role="administrador",
        propagate_recent=True,
    )
    assert result["corrected_attentions"] == 1
    with demographic_database() as con:
        row = con.execute("SELECT * FROM admission_attention_projection").fetchone()
        assert row["patient_name"] == "CORREGIDO"
        assert row["latest_payload_json"]["age"] == 42
        assert row["latest_payload_json"]["generation"] == row["generation"] == 3
        assert row["latest_payload_json"]["turn_id"] == row["turn_id"] == 10
        assert row["canonical_ars"] == "HUMANO"
        assert row["coverage_status"] == "ASEGURADO_VALIDADO"
        assert row["server_revision"] == 2
        event = con.execute("SELECT * FROM admission_sync_events").fetchone()
        assert event["turn_id"] == 10
        assert event["generation"] == 3


@pytest.fixture
def demographic_database(server):
    schema = "demographics_" + uuid4().hex
    with pg.Connection(server) as con:
        con.execute(f'CREATE SCHEMA "{schema}"')
        con.execute(f'SET search_path TO "{schema}"')
        con.execute(POSTGRES_PATIENT_DIRECTORY_SCHEMA)
        con.execute("""
            CREATE TABLE admission_attention_projection(
                global_attention_id UUID PRIMARY KEY, global_patient_id UUID,
                created_at_effective_utc TIMESTAMPTZ, server_revision INT DEFAULT 1,
                version INT DEFAULT 1, latest_payload_json JSONB,
                patient_name TEXT, cedula_snapshot TEXT,nss_snapshot TEXT,canonical_ars TEXT,
                coverage_status TEXT,readiness TEXT,readiness_reasons TEXT,
                snapshot_hash TEXT,source_updated_at TEXT,
                source_instance_id TEXT DEFAULT 'ORIGINAL', attention_id INT,
                operational_session_id UUID, operational_source_id UUID,
                generation INT DEFAULT 3, turn_id INT DEFAULT 10);
            CREATE TABLE admission_sync_events(
                sequence BIGSERIAL PRIMARY KEY,event_uuid UUID UNIQUE,entity_type TEXT,
                entity_uuid UUID,operation TEXT,payload_json JSONB,operational_session_id UUID,
                generation INT,origin_device_id TEXT,base_version INT,resulting_version INT,
                created_at TIMESTAMPTZ,operational_source_id UUID,turn_id INT,
                origin_user_id TEXT,origin_username TEXT,created_at_device TIMESTAMPTZ,
                created_at_effective_utc TIMESTAMPTZ,device_local_sequence BIGINT);
            CREATE TABLE recibos(id INT PRIMARY KEY,patient_name TEXT);
            INSERT INTO recibos VALUES(1,'ORIGINAL');
        """)

    def connect():
        con = pg.Connection(server)
        con.execute(f'SET search_path TO "{schema}"')
        return con

    yield connect
    with pg.Connection(server) as con:
        con.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_master_edit_and_attention_events_roll_back_together(
    demographic_database, monkeypatch
):
    patient_id = str(uuid4())
    with demographic_database() as con:
        con.execute(
            """INSERT INTO admission_patient_directory(global_patient_id,patient_name)
                       VALUES(%s,'ORIGINAL')""",
            (patient_id,),
        )
    repository = CentralPatientDirectoryRepository(demographic_database)

    def fail_after_update(*_args, **_kwargs):
        raise RuntimeError("simulated event persistence failure")

    monkeypatch.setattr(
        "admission_demographics.correct_recent_attentions", fail_after_update
    )
    with pytest.raises(RuntimeError, match="simulated"):
        repository.update_patient(
            patient_id,
            {"nombre": "CORREGIDO"},
            actor_user="SYNTHETIC",
            actor_role="administrador",
            propagate_recent=True,
        )
    with demographic_database() as con:
        row = con.execute(
            "SELECT patient_name,server_revision FROM admission_patient_directory"
        ).fetchone()
        assert row["patient_name"] == "ORIGINAL"
        assert row["server_revision"] == 1
        assert (
            con.execute(
                "SELECT COUNT(*) AS n FROM admission_patient_directory_events"
            ).fetchone()["n"]
            == 0
        )


def test_demographics_inclusive_seven_days_and_identity(demographic_database):
    edited_at = datetime(2026, 9, 23, 16, tzinfo=timezone.utc)
    patient_id = str(uuid4())
    corrections = {
        "global_patient_id": patient_id,
        "server_revision": 2,
        "nombre": "CORREGIDO",
        "telefono": "8090000000",
        "cedula": "NUEVA",
        "nss": "NUEVO",
        "direccion": "CORREGIDA",
        "ars": "ARS CORREGIDA",
    }
    offsets = [
        timedelta(0),
        timedelta(days=1),
        timedelta(days=7),
        timedelta(days=7, microseconds=1),
        timedelta(seconds=-1),
    ]
    with demographic_database() as con:
        for index, offset in enumerate(offsets):
            payload = {
                "age": 32,
                "phone": "ORIGINAL",
                "diagnosis": "INTACTO",
                "service_time": "12:00",
                "admission_username": "ORIGINAL",
                "service_type": "URGENCIA",
                "generation": 3,
                "turn_id": 10,
            }
            con.execute(
                """INSERT INTO admission_attention_projection(
                global_attention_id,global_patient_id,created_at_effective_utc,
                latest_payload_json,attention_id,operational_session_id,
                operational_source_id) VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s)""",
                (
                    str(uuid4()),
                    patient_id,
                    edited_at - offset,
                    json.dumps(payload),
                    index,
                    str(uuid4()),
                    str(uuid4()),
                ),
            )
        con.execute(
            """INSERT INTO admission_attention_projection(
            global_attention_id,global_patient_id,created_at_effective_utc,attention_id,
            latest_payload_json) VALUES(%s,%s,%s,99,'{}')""",
            (str(uuid4()), str(uuid4()), edited_at),
        )
        before = con.execute(
            "SELECT global_attention_id,turn_id,generation FROM admission_attention_projection ORDER BY attention_id"
        ).fetchall()
        assert correct_recent_attentions(con, corrections, edited_at=edited_at) == 3
        after = con.execute(
            "SELECT global_attention_id,turn_id,generation FROM admission_attention_projection ORDER BY attention_id"
        ).fetchall()
        assert before == after
        rows = con.execute(
            "SELECT * FROM admission_attention_projection ORDER BY attention_id"
        ).fetchall()
        for row in rows[:3]:
            payload = row["latest_payload_json"]
            assert payload["phone"] == "8090000000"
            assert payload["diagnosis"] == "INTACTO"
            assert payload["service_type"] == "URGENCIA"
            assert payload["age"] == 32
            assert payload["admission_username"] == "ORIGINAL"
        assert rows[3]["latest_payload_json"]["phone"] == "ORIGINAL"
        assert rows[4]["latest_payload_json"]["phone"] == "ORIGINAL"
        assert (
            con.execute("SELECT COUNT(*) AS n FROM admission_sync_events").fetchone()[
                "n"
            ]
            == 3
        )
        assert (
            con.execute("SELECT patient_name FROM recibos").fetchone()["patient_name"]
            == "ORIGINAL"
        )
