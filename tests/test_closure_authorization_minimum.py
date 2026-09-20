import pytest
import CALCULOS_QT as app


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("123", False),
        ("1234", True),
        ("12345", True),
        (" 0123 ", True),
        ("0000", False),
        ("PENDIENTE", False),
        ("AUTH-123", False),
        ("AUTH-1234", True),
    ],
)
def test_closure_authorization_requires_four_digits(value, expected):
    assert app._valid_shift_authorization(value) is expected


def test_newer_preliminary_does_not_hide_authorized_receipt():
    records = [{"source_instance_id": "PC", "attention_id": 1}]
    receipts = [
        {
            "id": 2,
            "source_id": "PC",
            "admission_atencion_id": 1,
            "numero_autorizacion": "",
        },
        {
            "id": 1,
            "source_id": "PC",
            "admission_atencion_id": 1,
            "numero_autorizacion": "1234",
        },
    ]
    assert app._link_shift_receipts(records, receipts)[("PC", 1)]["id"] == 1
