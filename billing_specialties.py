"""Persistent clinical specialty values and accent-insensitive completion."""

from difflib import SequenceMatcher
from typing import cast

from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtWidgets import QComboBox, QCompleter, QLineEdit

from monthly_candidate_search import normalized_name

SPECIALTIES = ("EMERGENCIOLOGÍA", "PEDIATRÍA", "GINECOLOGÍA")
MAX_SPECIALTY_LENGTH = 100
MIN_COMPLETION_LENGTH = 2
MIN_CORRECTION_SIMILARITY = 0.8
MIN_CORRECTION_MARGIN = 0.1


def specialty_values(receipts):
    return [receipt.get("specialty_snapshot") for receipt in receipts]


def specialty_options(values=()):
    return list(SPECIALTIES)


def receipt_specialty_snapshot(value: object) -> str:
    """Canonicalize known Admission values while retaining explicit legacy data."""
    text = str(value or "")
    key = normalized_name(text)
    if key == "general":
        return SPECIALTIES[0]
    return next((item for item in SPECIALTIES if normalized_name(item) == key), text)


def specialty_matches(value, choices=SPECIALTIES):
    key = normalized_name(value)
    if key == "general":
        return [SPECIALTIES[0]]
    if not key:
        return list(choices)
    scores = [
        (SequenceMatcher(None, key, normalized_name(item)).ratio(), item)
        for item in choices
    ]
    return [
        item
        for score, item in sorted(scores, reverse=True)
        if key in normalized_name(item) or score >= MIN_CORRECTION_SIMILARITY
    ]


def normalized_specialty(value):
    text = str(value or "").strip().upper()
    if len(text) > MAX_SPECIALTY_LENGTH:
        raise ValueError("La especialidad no puede exceder 100 caracteres.")
    key = normalized_name(text)
    if not key:
        return ""
    if key == "general":
        return SPECIALTIES[0]
    return _known_specialty(key) or _correct_specialty_typo(key)


def _known_specialty(key):
    exact = next((item for item in SPECIALTIES if normalized_name(item) == key), None)
    if exact:
        return exact
    prefixes = [item for item in SPECIALTIES if normalized_name(item).startswith(key)]
    if len(key) >= MIN_COMPLETION_LENGTH and len(prefixes) == 1:
        return prefixes[0]
    return None


def _correct_specialty_typo(key):
    scores = sorted(
        (
            (SequenceMatcher(None, key, normalized_name(item)).ratio(), item)
            for item in SPECIALTIES
        ),
        reverse=True,
    )
    best, runner_up = scores[0], scores[1]
    if (
        best[0] >= MIN_CORRECTION_SIMILARITY
        and best[0] - runner_up[0] >= MIN_CORRECTION_MARGIN
    ):
        return best[1]
    raise ValueError(
        "Selecciona una especialidad: EMERGENCIOLOGÍA, GINECOLOGÍA o PEDIATRÍA."
    )


class SpecialtyComboBox(QComboBox):
    def __init__(self, value="", choices=(), parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.addItems(specialty_options([*choices, value]))
        try:
            initial = normalized_specialty(value) or SPECIALTIES[0]
        except ValueError:
            initial = str(value or "")
        self.setCurrentText(initial)
        line_edit = cast(QLineEdit, self.lineEdit())
        line_edit.setMaxLength(MAX_SPECIALTY_LENGTH)
        self._completion_model = QStringListModel(self)
        completer = QCompleter(self._completion_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.setCompleter(completer)
        line_edit.textEdited.connect(self._update_completions)
        line_edit.editingFinished.connect(self._normalize_text)

    def _update_completions(self, text):
        choices = [self.itemText(index) for index in range(self.count())]
        self._completion_model.setStringList(specialty_matches(text, choices))

    def _normalize_text(self):
        try:
            value = normalized_specialty(self.currentText())
        except ValueError:
            return
        self.setEditText(value)
