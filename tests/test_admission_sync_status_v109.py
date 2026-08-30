from admission_v15_adapter import _online_sync_status


def test_online_status_is_synchronized_only_when_outbox_is_empty():
    text, colors = _online_sync_status("PRIMARY", 0)

    assert text == "Conectado · Principal · Sincronizado"
    assert colors[0] == "#E8F7EE"


def test_online_status_exposes_pending_outbox_instead_of_claiming_synchronized():
    text, colors = _online_sync_status("SECONDARY", 2)

    assert text == "Conectado · Secundaria · Pendiente de sincronización (2)"
    assert colors[0] == "#FFF4D6"
