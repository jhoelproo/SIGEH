"""Identification alternatives never replace the patient's national identifiers."""

import pytest
from unittest.mock import Mock

from receipt_list_consistency import effective_list_entry, normalized_document_type
from monthly_receipt_fields import (
    ensure_receipt_insurance_schema,
    list_document_options,
    optional_service_date,
    receipt_document_changes,
    require_expected_date,
    displayed_list_document,
    monthly_metadata_changes,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("NO.POLIZA", "NO. PÓLIZA"),
        ("no. afiliado", "NO. AFILIADO"),
        ("NO. CARNET", "NO. CARNET"),
    ],
)
def test_alternative_document_types_are_accepted(value, expected):
    assert normalized_document_type(value) == expected


def test_alternate_number_survives_receipt_nss_readthrough():
    row = dict(
        recibo_id=1,
        batch_status="PENDIENTE",
        document_type_snapshot="NO. PÓLIZA",
        document_number_snapshot="000ABC001",
        receipt_nss="123456789",
        receipt_cedula="00000000001",
        receipt_document_type="NO. PÓLIZA",
        receipt_document_number="000ABC002",
        receipt_edited_at="2026-10-08T15:00:00+00:00",
        last_edited_at="2026-10-08T14:00:00+00:00",
    )
    assert effective_list_entry(row)["document_number_snapshot"] == "000ABC002"
    assert effective_list_entry(row)["nss_snapshot"] == "123456789"


def test_latest_receipt_date_updates_only_pending_list():
    row = dict(
        recibo_id=1,
        batch_status="PENDIENTE",
        service_date_snapshot="2026-10-04",
        receipt_service_date="2026-10-06",
        receipt_edited_at="2026-10-08T15:00:00+00:00",
        last_edited_at="2026-10-08T14:00:00+00:00",
    )
    assert effective_list_entry(row)["service_date_snapshot"] == "2026-10-06"
    row["batch_status"] = "ENVIADO"
    assert effective_list_entry(row)["service_date_snapshot"] == "2026-10-04"


@pytest.mark.parametrize(
    "ars,alternate",
    [
        ("RENACER", "NO. PÓLIZA"),
        ("Humano", "NO. AFILIADO"),
        ("PRIMERA ARS", "NO. AFILIADO"),
        ("SEMMA", "NO. CARNET"),
        ("FUTURO", None),
    ],
)
def test_identifier_options_are_specific_to_each_insurer(ars, alternate):
    assert list_document_options(ars) == [
        "NSS",
        "CÉDULA",
        *([alternate] if alternate else []),
    ]
    if alternate:
        assert receipt_document_changes(alternate, "000ABC001", ars) == {
            "insurance_document_type": alternate,
            "insurance_document_number": "000ABC001",
        }


@pytest.mark.parametrize(
    "kind,field",
    [("NSS", "admission_nss_snapshot"), ("CEDULA", "admission_cedula_snapshot")],
)
def test_national_identification_updates_only_its_own_field(kind, field):
    changes = receipt_document_changes(kind, "000001", "RENACER")
    assert changes[field] == "000001"
    assert len(changes) == 3


@pytest.mark.parametrize("kind", ["PASAPORTE", "NO. CARNET"])
def test_invalid_or_incompatible_identification_is_rejected(kind):
    with pytest.raises(ValueError):
        receipt_document_changes(kind, "000001", "RENACER")


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("08/10/2026", "2026-10-08"), ("2026-10-08", "2026-10-08")],
)
def test_optional_dates_preserve_iso_and_ignore_absent_value(value, expected):
    assert optional_service_date(value) == expected


def test_service_date_invalid_and_stale_are_rejected():
    with pytest.raises(ValueError):
        optional_service_date("31/02/2026")
    receipt = {"fecha": "2026-10-08"}
    require_expected_date(receipt, None)
    require_expected_date(receipt, "08/10/2026")
    with pytest.raises(ValueError, match="otra pantalla"):
        require_expected_date(receipt, "2026-10-07")


def test_empty_legacy_insurance_fields_do_not_clear_national_list_selection():
    row = dict(
        recibo_id=1,
        batch_status="PENDIENTE",
        document_type_snapshot="CÉDULA",
        document_number_snapshot="old",
        receipt_cedula="00123",
        receipt_edited_at="2026-10-08",
        receipt_document_type=None,
        receipt_document_number=None,
    )
    assert effective_list_entry(row)["document_number_snapshot"] == "00123"
    assert displayed_list_document({}) is None
    assert (
        displayed_list_document(
            {"document_type_snapshot": "NSS", "document_number_snapshot": "001"}
        )
        == "001"
    )


def test_schema_hook_only_adds_the_two_columns():
    connection = Mock()
    ensure_receipt_insurance_schema(connection)
    sql = connection.execute.call_args.args[0]
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 2
    assert "UPDATE" not in sql and "DROP" not in sql


@pytest.mark.parametrize(
    "name,date",
    [
        (None, None),
        (" NOMBRE  CORREGIDO ", None),
        (None, "08/10/2026"),
        ("NOMBRE CORREGIDO", "08/10/2026"),
    ],
)
def test_metadata_assembler_only_updates_the_requested_header_fields(name, date):
    changes = monthly_metadata_changes(
        {"ars": "RENACER"},
        document_type="NO.POLIZA",
        document_number="000ABC001",
        authorization="00001234",
        specialty="PEDIATRÍA",
        patient_name=name,
        service_date=date,
    )
    assert ("nombre" in changes) == (name is not None)
    assert ("fecha" in changes) == (date is not None)
    if name is not None:
        assert changes["nombre"] == "NOMBRE CORREGIDO"
    if date is not None:
        assert changes["fecha"] == "2026-10-08"
    assert changes["insurance_document_number"] == "000ABC001"
    assert "admission_nss_snapshot" not in changes
