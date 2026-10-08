"""Only approved specialties can be saved; matching ignores accents."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from billing_specialties import (
    SpecialtyComboBox,
    normalized_specialty,
    receipt_specialty_snapshot,
    specialty_matches,
    specialty_options,
)
from monthly_candidate_search import filter_candidates
from receipt_list_consistency import effective_list_entry, normalize_list_metadata
from receipt_patient_correction import normalized_patient_name


@pytest.mark.parametrize(
    "value,expected",
    [
        ("PEDIATRIA", "PEDIATRÍA"),
        ("  ginecología ", "GINECOLOGÍA"),
        ("emergenciologia", "EMERGENCIOLOGÍA"),
        ("general", "EMERGENCIOLOGÍA"),
        ("gineco", "GINECOLOGÍA"),
        ("pe", "PEDIATRÍA"),
        ("pediatra", "PEDIATRÍA"),
        ("", ""),
        (None, ""),
    ],
)
def test_specialty_values_preserve_known_custom_empty_and_boundary(value, expected):
    assert normalized_specialty(value) == expected


def test_specialty_too_long_is_rejected():
    with pytest.raises(ValueError, match="100"):
        normalized_specialty("x" * 101)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("GENERAL", "EMERGENCIOLOGÍA"),
        ("  general ", "EMERGENCIOLOGÍA"),
        ("EMERGENCIOLOGIA", "EMERGENCIOLOGÍA"),
        ("PEDIATRIA", "PEDIATRÍA"),
        ("ginecologia", "GINECOLOGÍA"),
        ("NEUROLOGÍA", "NEUROLOGÍA"),
        ("  Especialidad histórica ", "  Especialidad histórica "),
        ("gineco", "gineco"),
        ("", ""),
        (None, ""),
    ],
)
def test_new_receipt_snapshot_canonicalizes_known_admission_specialty(value, expected):
    import CALCULOS_QT as app

    attention = {
        "attention_id": 1,
        "patient_id": 2,
        "specialty": value,
        "snapshot_hash": "original-admission-hash",
    }
    values = app._admission_values(attention)
    assert values[13] == expected
    assert values[9] == "original-admission-hash"
    assert attention["specialty"] == value


@pytest.mark.parametrize(
    "value,expected",
    [
        ("GENERAL", "EMERGENCIOLOGÍA"),
        ("EMERGENCIOLOGÍA", "EMERGENCIOLOGÍA"),
        (" pediatría ", "PEDIATRÍA"),
        ("GINECOLOGIA", "GINECOLOGÍA"),
        ("NEUROLOGÍA", "NEUROLOGÍA"),
        ("pediatra", "pediatra"),
        (None, ""),
    ],
)
def test_admission_snapshot_normalization_preserves_explicit_legacy_values(
    value, expected
):
    assert receipt_specialty_snapshot(value) == expected


def test_options_only_include_the_three_approved_specialties():
    assert specialty_options(["PEDIATRIA", "neurología", "NEUROLOGIA", None, ""]) == [
        "EMERGENCIOLOGÍA",
        "PEDIATRÍA",
        "GINECOLOGÍA",
    ]
    assert specialty_matches("") == specialty_options()
    assert specialty_matches("pedia") == ["PEDIATRÍA"]
    assert specialty_matches("zzzz") == []
    assert specialty_matches("general") == ["EMERGENCIOLOGÍA"]


@pytest.mark.parametrize("value", ["NEUROLOGÍA", "P", "x" * 100])
def test_unapproved_specialty_cannot_be_saved(value):
    with pytest.raises(ValueError, match="especialidad"):
        normalized_specialty(value)


def test_specialty_combo_completion_and_reopening_preserve_saved_values():
    application = QApplication.instance() or QApplication([])
    combo = SpecialtyComboBox("NEUROLOGÍA", ["GENERAL", "CARDIOLOGÍA"])
    assert combo.count() == 3
    assert combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    combo.lineEdit().setText("gineco")
    combo.lineEdit().textEdited.emit("gineco")
    assert combo.completer().model().stringList() == ["GINECOLOGÍA"]
    combo.lineEdit().editingFinished.emit()
    assert combo.currentText() == "GINECOLOGÍA"
    reopened = SpecialtyComboBox(combo.currentText())
    assert reopened.currentText() == "GINECOLOGÍA"
    assert SpecialtyComboBox().currentText() == "EMERGENCIOLOGÍA"
    combo.setEditText("NEUROLOGÍA")
    combo.lineEdit().editingFinished.emit()
    assert combo.count() == 3
    with pytest.raises(ValueError):
        normalized_specialty(combo.currentText())
    combo.close()
    reopened.close()
    application.processEvents()


@pytest.mark.parametrize("value", ["", None, " " * 2, "x" * 161])
def test_patient_name_rejects_empty_and_over_limit(value):
    with pytest.raises(ValueError):
        normalized_patient_name(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("N", "N"),
        (" ANA  PIÑA ", "ANA PIÑA"),
        ("x" * 160, "x" * 160),
        ("<nombre>", "<nombre>"),
        ("O'NEILL", "O'NEILL"),
    ],
)
def test_patient_name_preserves_unicode_punctuation_and_boundary(value, expected):
    assert normalized_patient_name(value) == expected


@pytest.mark.parametrize(
    "mode,term,expected,warning",
    [
        ("ALL", "", [1, 2], None),
        ("NAME", "maria perez", [1], None),
        ("NSS", "12", [], "Escriba al menos 4 dígitos."),
        ("CEDULA", "402-1111", [1], None),
        ("NSS", "1234", [1], None),
        ("ALL", "1234", [1], None),
        ("ALL", "999", [], "Escriba al menos 4 dígitos."),
        ("RECEIPT", "no numero", [], "Escriba el número de recibo."),
        ("RECEIPT", "1", [1], None),
        ("RECEIPT", "99", [2], None),
        ("ALL", "ABC", [], None),
        (None, "990834", [2], None),
        ("NSS", "zzzz", [], "Escriba al menos 4 dígitos."),
    ],
)
def test_candidate_search_modes_empty_invalid_exact_and_partial(
    mode, term, expected, warning
):
    candidates = [
        {
            "id": 1,
            "nombre": "MARÍA PÉREZ",
            "numero": "1",
            "nss_snapshot": "123456",
            "cedula_snapshot": "402-1111111-1",
        },
        {"id": 2, "nombre": "JUAN", "numero": "990834", "nss_snapshot": ""},
    ]
    rows, message = filter_candidates(candidates, mode, term)
    assert [row["id"] for row in rows] == expected
    assert message == warning
    assert filter_candidates([], mode, term)[0] == []


def test_exact_receipt_number_precedes_partial_matches():
    candidates = [{"numero": 12}, {"numero": 123}, {"numero": None}]
    assert filter_candidates(candidates, "RECEIPT", "12") == ([candidates[0]], None)


@pytest.mark.parametrize("specialty", ["PEDIATRIA", "GINECOLOGIA", "GENERAL"])
def test_linked_admission_specialty_is_recovered_only_for_editable_lists(specialty):
    row = {
        "recibo_id": 1,
        "batch_status": "PENDIENTE",
        "receipt_specialty": None,
        "linked_admission_specialty": {
            "latest_payload_json": {"detail_sheet": specialty}
        },
    }
    assert effective_list_entry(row)["specialty_snapshot"] == normalized_specialty(
        specialty
    )
    assert effective_list_entry({**row, "batch_status": "ENVIADO"}) == {
        **row,
        "batch_status": "ENVIADO",
    }
    explicit = effective_list_entry({**row, "receipt_specialty": "NEUROLOGÍA"})
    assert explicit["specialty_snapshot"] == "NEUROLOGÍA"
    missing = effective_list_entry(
        {**row, "linked_admission_specialty": {"specialty": ""}}
    )
    assert missing["specialty_snapshot"] is None


def test_name_only_correction_does_not_require_fake_identification():
    assert normalize_list_metadata("NSS", "", "", "", allow_incomplete=True) == (
        "NSS",
        "",
        "",
        "EMERGENCIOLOGÍA",
    )
