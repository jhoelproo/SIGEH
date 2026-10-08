"""Legacy selections without a UUID still require the exact live source row."""

import pytest

import CALCULOS_QT as app
from receipt_attention_link import link_receipt_to_inherited_attention
from tests.test_billing_consistency_postgres import server as server
from tests.test_inherited_receipt_save import inherited as inherited
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


@pytest.mark.parametrize("boundary", ["existing", "missing_attention", "wrong_source"])
def test_selection_without_uuid_requires_existing_attention_in_same_source(
    inherited, boundary
):
    receipt_id = save()
    selection = inherited.snapshot()
    selection.pop("global_attention_id", None)
    with app.db_connect() as con:
        before = dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (receipt_id,)).fetchone()
        )
        action_count = con.execute("SELECT COUNT(*) FROM action_history").fetchone()[0]
        if boundary == "missing_attention":
            con.execute(
                "DELETE FROM admission_attention_projection "
                "WHERE source_instance_id=%s AND attention_id=%s",
                (selection["source_instance_id"], selection["attention_id"]),
            )
        elif boundary == "wrong_source":
            selection["source_instance_id"] = "OTHER_ORIGIN"

    if boundary == "existing":
        assert (
            link_receipt_to_inherited_attention(
                receipt_id, selection, "uuid_test", backend=app, session_id="A"
            )
            == receipt_id
        )
    else:
        with pytest.raises(ValueError, match="La atención ya no existe"):
            link_receipt_to_inherited_attention(
                receipt_id, selection, "uuid_test", backend=app, session_id="A"
            )

    with app.db_connect() as con:
        after = dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (receipt_id,)).fetchone()
        )
        after_action_count = con.execute(
            "SELECT COUNT(*) FROM action_history"
        ).fetchone()[0]
        inheritance = con.execute(
            "SELECT estado,receipt_id FROM admission_shift_inheritances"
        ).fetchone()
    if boundary == "existing":
        assert after["admission_atencion_id"] == inherited.attention_id
        assert after["admission_source_instance_id"] == inherited.source_instance_id
        assert after["revision_version"] == before["revision_version"] + 1
        assert after_action_count == action_count + 1
        assert inheritance["estado"] == "COMPLETADA"
        assert inheritance["receipt_id"] == receipt_id
    else:
        assert after == before
        assert after_action_count == action_count
        assert inheritance["estado"] == "PENDIENTE"
        assert inheritance["receipt_id"] is None
