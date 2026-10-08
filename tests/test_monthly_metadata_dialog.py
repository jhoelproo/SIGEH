"""Correction controls expose dates and only appropriate insurer identifiers."""

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "ars,kind",
    [
        ("RENACER", "NO. PÓLIZA"),
        ("HUMANO", "NO. AFILIADO"),
        ("PRIMERA", "NO. AFILIADO"),
        ("SEMMA", "NO. CARNET"),
    ],
)
def test_dialog_offers_insurer_document_and_editable_calendar_date(qt, ars, kind):
    original = "2026-10-04"
    dialog = app.MonthlyReceiptEditorDialog(
        dict(
            patient_snapshot="SINTÉTICO",
            numero=100,
            ars_snapshot=ars,
            service_date_snapshot=original,
            specialty_snapshot="GENERAL",
            document_type_snapshot=kind,
            document_number_snapshot="000ABC001",
        )
    )
    try:
        assert [
            dialog.document_type.itemText(i)
            for i in range(dialog.document_type.count())
        ] == ["NSS", "CÉDULA", kind]
        assert dialog.document_type.currentText() == kind
        assert dialog.service_date.calendarPopup()
        assert dialog.service_date.date() == QDate(2026, 10, 4)
        dialog.service_date.setDate(QDate(2026, 10, 6))
        values = dialog.values()
        assert values["service_date"] == "2026-10-06"
        assert values["expected_service_date"] == original
        assert values["document_number"] == "000ABC001"
        assert values["specialty"] == "EMERGENCIOLOGÍA"
        assert dialog.specialty.count() == 3
    finally:
        dialog.close()


def test_unapproved_specialty_cannot_be_submitted(qt):
    dialog = app.MonthlyReceiptEditorDialog(
        dict(
            patient_snapshot="SINTÉTICO",
            numero=100,
            service_date_snapshot="2026-10-04",
            specialty_snapshot="PEDIATRÍA",
        )
    )
    try:
        dialog.specialty.setEditText("NEUROLOGÍA")
        with pytest.raises(ValueError, match="especialidad"):
            dialog.values()
    finally:
        dialog.close()
