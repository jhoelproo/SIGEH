"""Emergency history must reuse the central identity and billing decision."""

from unittest.mock import Mock

import pytest

from billing_history_handoff import (
    history_identity,
    billing_destination,
    has_inherited_receipt,
    exclude_current_draft,
)


GLOBAL = "11111111-1111-4111-8111-111111111111"


def test_history_identity_never_transmits_patient_details_or_display_number():
    assert history_identity(
        {"id": 399, "global_attention_id": GLOBAL, "nombre": "SYNTHETIC"}
    ) == {"global_attention_id": GLOBAL, "attention_id": 0, "source_instance_id": ""}


@pytest.mark.parametrize("value", [None, "", "399", "invalid"])
def test_history_requires_canonical_identity(value):
    with pytest.raises(ValueError):
        history_identity({"id": 399, "global_attention_id": value})


def test_available_attention_uses_shared_claim():
    assert billing_destination({"eligible": True, "_projection": {}}) == "claim"


@pytest.mark.parametrize(
    "reason,flag",
    [
        ("RECEIPT_PENDING", "can_continue_receipt"),
        ("ALREADY_BILLED", "can_open_receipt"),
    ],
)
def test_existing_receipt_routes_to_existing_document(reason, flag):
    assert (
        billing_destination(
            {"reason_code": reason, flag: True, "receipt_id": 7, "_projection": {}}
        )
        == "receipt"
    )


@pytest.mark.parametrize(
    "projection",
    [
        {"is_deleted": True},
        {"claimed_elsewhere": True},
        {"source_status": "ANULADA"},
    ],
)
def test_existing_receipt_does_not_bypass_central_blocks(projection):
    assert (
        billing_destination(
            {
                "reason_code": "RECEIPT_PENDING",
                "can_continue_receipt": True,
                "receipt_id": 7,
                "_projection": projection,
            }
        )
        == "blocked"
    )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"eligible": False},
        {"reason_code": "HISTORICAL_ROLE_DENIED"},
        {"reason_code": "RECEIPT_PENDING", "receipt_id": 7},
        {"reason_code": "ALREADY_BILLED", "can_open_receipt": True},
    ],
)
def test_denial_does_not_create_receipt(result):
    assert billing_destination(result) == "blocked"


def test_main_window_uses_existing_claim_entry_point(monkeypatch):
    import CALCULOS_QT as app

    attention = object()
    monkeypatch.setattr(app, "_attention_from_projection", Mock(return_value=attention))
    window = Mock()
    app.MainWindow._on_history_billing_resolved(
        window, {"eligible": True, "_projection": {}}, 1.0
    )
    window._use_admission_attention_from_module.assert_called_once_with(
        attention, from_history=True
    )
    window.open_receipt_in_billing.assert_not_called()


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, True),
        ({"linked_receipt_id": None}, False),
        ({"receipt_inheritance_state": "PENDIENTE"}, False),
        ({"receipt_origin_turn": None}, False),
        ({"turn_id": 99}, False),
    ],
)
def test_processed_inheritance_requires_receipt_and_matching_origin(changes, expected):
    row = {
        "linked_receipt_id": 7,
        "receipt_inheritance_state": "HEREDADA_PROCESADA",
        "receipt_origin_turn": 10,
        "turn_id": 10,
    }
    assert has_inherited_receipt(row | changes) is expected


def test_current_draft_removed_only_from_pending_display():
    selected = Mock(global_attention_id=GLOBAL)
    another = Mock(global_attention_id="other")
    rows = [selected, another]
    assert exclude_current_draft(rows, {"global_attention_id": GLOBAL}) == [another]
    assert exclude_current_draft(rows, {}) == rows
    assert rows == [selected, another]


@pytest.mark.parametrize("selected", [[], ["placeholder"], ["row-a"], ["row-b"]])
def test_real_history_callback_uses_row_uuid_not_repeated_visible_id(selected):
    import ast
    import inspect
    import textwrap
    from admission_v15_adapter import load_v15_application_module

    v15 = load_v15_application_module()
    source = ast.parse(textwrap.dedent(inspect.getsource(v15.App.abrir_historial)))
    callback = next(
        n
        for n in ast.walk(source)
        if isinstance(n, ast.FunctionDef) and n.name == "enviar_a_facturacion"
    )
    host, win = Mock(), Mock()
    other = "22222222-2222-4222-8222-222222222222"
    rows = {
        "row-a": {"id": 399, "global_attention_id": GLOBAL},
        "row-b": {"id": 399, "global_attention_id": other},
    }
    namespace = {
        "self": host,
        "win": win,
        "billing_rows": rows,
        "tree": Mock(selection=Mock(return_value=selected)),
    }
    exec(
        compile(ast.Module(body=[callback], type_ignores=[]), v15.__file__, "exec"),
        namespace,
    )
    namespace["enviar_a_facturacion"]()
    if selected and selected[0] in rows:
        host.event_bus.billing_requested.emit.assert_called_once_with(
            history_identity(rows[selected[0]])
        )
        win.destroy.assert_called_once()
    else:
        host.event_bus.billing_requested.emit.assert_not_called()
        win.destroy.assert_not_called()


@pytest.mark.parametrize("destination", ["receipt", "blocked"])
def test_existing_receipt_and_denial_never_start_new_claim(monkeypatch, destination):
    import CALCULOS_QT as app

    monkeypatch.setattr(app.QMessageBox, "information", Mock())
    window = Mock(editing_recibo_id=None)
    window.cart_table.rowCount.return_value = 0
    window.name_edit.text.return_value = ""
    result = {
        "reason_code": "RECEIPT_PENDING",
        "can_continue_receipt": True,
        "receipt_id": 7,
        "_projection": {},
    }
    if destination == "blocked":
        result["_projection"] = {"is_deleted": True}
    app.MainWindow._on_history_billing_resolved(window, result, 0)
    window._use_admission_attention_from_module.assert_not_called()
    if destination == "receipt":
        window.open_receipt_in_billing.assert_called_once_with(7)
    else:
        window.open_receipt_in_billing.assert_not_called()


def test_real_history_window_contains_billing_action(tmp_path, monkeypatch):
    import CALCULOS_QT as app
    from admission_v15_adapter import load_v15_application_module
    from PySide6.QtGui import QFontDatabase

    module = load_v15_application_module()
    original = module.App.abrir_historial
    found = []

    def inspect_history(admission):
        original(admission)
        window = admission.historial_win
        buttons = [
            button
            for button in window.findChildren(app.QPushButton)
            if button.text() == "Enviar a Facturación"
        ]
        assert len(buttons) == 1
        assert buttons[0].isEnabled()
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
        app.QApplication.processEvents()
        assert window.grab().save(str(tmp_path / "history-billing.png"))
        found.append(True)

    monkeypatch.setattr(module.App, "abrir_historial", inspect_history)
    assert app.run_v15_packaging_check(str(tmp_path / "package.json")) == 0
    assert found == [True]
