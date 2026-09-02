from types import SimpleNamespace

from admission_v15_adapter import _HybridDatabaseProxy


def test_current_turn_history_is_oldest_first_with_stable_identity_order():
    proxy = object.__new__(_HybridDatabaseProxy)
    object.__setattr__(proxy, "_database", SimpleNamespace())
    object.__setattr__(proxy, "_runtime", SimpleNamespace(
        operational_session=SimpleNamespace(
            turn_id=3942,
            operational_source_id="central-source",
        )
    ))
    object.__setattr__(proxy, "_local_list_rows", lambda *_args, **_kwargs: [
        {
            "id": 30,
            "fecha": "2026-08-27",
            "hora": "07:08:00",
            "created_at_effective_utc": "2026-08-27T07:08:00+00:00",
            "global_attention_id": "00000000-0000-4000-8000-000000000030",
        },
        {
            "id": 31,
            "fecha": "2026-08-27",
            "hora": "07:11:00",
            "created_at_effective_utc": "2026-08-27T07:11:00+00:00",
            "global_attention_id": "00000000-0000-4000-8000-000000000031",
        },
        {
            "id": 32,
            "fecha": "2026-08-27",
            "hora": "07:11:00",
            "created_at_effective_utc": "2026-08-27T07:11:00+00:00",
            "origin_device_id": "PC-1",
            "device_local_sequence": 1,
            "global_attention_id": "00000000-0000-4000-8000-000000000032",
        },
        {
            "id": 33,
            "fecha": "2026-08-27",
            "hora": "07:11:00",
            "created_at_effective_utc": "2026-08-27T07:11:00+00:00",
            "origin_device_id": "PC-1",
            "device_local_sequence": 2,
            "global_attention_id": "00000000-0000-4000-8000-000000000033",
        },
    ])

    rows = proxy.list_history_cache_local(
        "listar_atenciones_filtradas",
        modo="Este turno",
        limite=50,
        offset=0,
    )

    assert [row["id"] for row in rows] == [30, 31, 32, 33]
