"""Reentry boundaries use a real local replica and stable global identities."""

import sqlite3

import pytest

from admission_reentry import local_reentry_fields, reentry_payload

ORIGIN_UUID = "765fac16-ecf2-4dc3-adbc-00903176af3f"
ALIAS_UUID = "31764afe-806b-40ee-a5ba-97167795d802"


@pytest.fixture
def replica():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE atenciones(
            id INTEGER PRIMARY KEY,global_attention_id TEXT UNIQUE,
            paciente_id INTEGER,dia_operativo_id INTEGER
        );
        CREATE TABLE sync_attention_aliases(
            remote_global_attention_id TEXT PRIMARY KEY,local_attention_id INTEGER
        );
    """)
    connection.execute(
        "INSERT INTO atenciones VALUES(?,?,?,?)", (41, ORIGIN_UUID, 12, 6)
    )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def payload(**changes):
    return {
        "is_reentry": 1,
        "reentry_origin_global_attention_id": ORIGIN_UUID,
        "reentry_reason": "Nuevo episodio clínico",
        "reentry_authorized_by": "ADMIN",
        **changes,
    }


@pytest.mark.parametrize("state", [-1, 2, "invalid"])
def test_invalid_reentry_state_cannot_materialize_or_write(replica, state):
    before = replica.total_changes
    with pytest.raises(ValueError):
        local_reentry_fields(replica, payload(is_reentry=state), 12, 6)
    assert replica.total_changes == before


@pytest.mark.parametrize("state", [0, None, "0"])
def test_explicit_normal_visit_does_not_resolve_stale_origin(replica, state):
    assert local_reentry_fields(
        replica,
        payload(is_reentry=state, reentry_origin_global_attention_id="invalid"),
        12,
        6,
    ) == {"es_reingreso": 0}


def test_old_snapshot_without_reentry_metadata_preserves_existing_fields(replica):
    assert local_reentry_fields(replica, {"service_date": "06/10/2026"}, 12, 6) == {}


@pytest.mark.parametrize("origin", [None, "", "not-a-uuid", "' OR 1=1 --"])
def test_malformed_or_missing_global_origin_never_falls_back_to_remote_local_id(
    replica, origin
):
    with pytest.raises(ValueError):
        local_reentry_fields(
            replica,
            payload(reentry_origin_global_attention_id=origin, atencion_origen_id=41),
            12,
            6,
        )


@pytest.mark.parametrize(
    "reason,actor", [("1234567", "ADMIN"), ("12345678", ""), ("12345678", "   ")]
)
def test_reentry_requires_eight_character_reason_and_nonempty_actor(
    replica, reason, actor
):
    with pytest.raises(ValueError, match="motivo y autorización"):
        local_reentry_fields(
            replica,
            payload(reentry_reason=reason, reentry_authorized_by=actor),
            12,
            6,
        )


def test_exact_minimum_reason_and_trimmed_actor_materialize_local_origin(replica):
    assert local_reentry_fields(
        replica,
        payload(reentry_reason=" 12345678 ", reentry_authorized_by=" ADMIN "),
        12,
        6,
    ) == {
        "es_reingreso": 1,
        "atencion_origen_id": 41,
        "motivo_reingreso": "12345678",
        "autorizado_por": "ADMIN",
    }


@pytest.mark.parametrize("patient,day", [(13, 6), (12, 4), (13, 4)])
def test_origin_patient_or_clinical_day_mismatch_is_rejected(replica, patient, day):
    before = replica.total_changes
    with pytest.raises(ValueError, match="paciente y día"):
        local_reentry_fields(replica, payload(), patient, day)
    assert replica.total_changes == before


def test_unsynchronized_origin_stays_unresolved_even_if_integer_id_exists(replica):
    with pytest.raises(ValueError, match="todavía no se ha sincronizado"):
        local_reentry_fields(
            replica,
            payload(
                reentry_origin_global_attention_id=ALIAS_UUID, atencion_origen_id=41
            ),
            12,
            6,
        )


def test_legacy_alias_resolves_its_own_local_id_and_keeps_clinical_guard(replica):
    replica.execute(
        "INSERT INTO sync_attention_aliases VALUES(?,?)", (ALIAS_UUID.upper(), 41)
    )
    fields = local_reentry_fields(
        replica,
        payload(reentry_origin_global_attention_id=ALIAS_UUID.replace("-", "")),
        12,
        6,
    )
    assert fields["atencion_origen_id"] == 41
    with pytest.raises(ValueError, match="paciente y día"):
        local_reentry_fields(
            replica, payload(reentry_origin_global_attention_id=ALIAS_UUID), 12, 4
        )


def test_capture_snapshot_uses_global_origin_instead_of_local_integer(replica):
    snapshot = reentry_payload(
        replica,
        {
            "es_reingreso": 1,
            "atencion_origen_id": 41,
            "motivo_reingreso": "Nuevo episodio clínico",
            "autorizado_por": "ADMIN",
        },
    )
    assert snapshot == payload()


def test_missing_capture_origin_is_explicit_instead_of_assigning_another_visit(replica):
    assert (
        reentry_payload(replica, {"es_reingreso": 1, "atencion_origen_id": 999})[
            "reentry_origin_global_attention_id"
        ]
        is None
    )


@pytest.mark.parametrize(
    "attention", [{}, {"es_reingreso": 0, "atencion_origen_id": 41}]
)
def test_normal_capture_snapshot_has_no_reentry_origin(replica, attention):
    assert reentry_payload(replica, attention) == {
        "is_reentry": 0,
        "reentry_origin_global_attention_id": None,
        "reentry_reason": None,
        "reentry_authorized_by": None,
    }
