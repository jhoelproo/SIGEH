"""Role defaults and explicit allow/deny in the existing user manager."""

from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from user_permissions import overrides_for


class UserPermissionsDialog(QDialog):
    def __init__(self, user, permissions, role_defaults, save, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Permisos individuales · " + user["username"])
        self.resize(660, 600)
        self.original = dict(overrides_for(user))
        self.save_operation = save
        self.controls = {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Predeterminado usa el rol. Permitir o denegar prevalece sobre él.")
        )
        self.table = QTableWidget(len(permissions), 3)
        self.table.setHorizontalHeaderLabels(["Permiso", "Rol", "Excepción individual"])
        for row, (key, caption) in enumerate(permissions.items()):
            self.table.setItem(row, 0, QTableWidgetItem(caption))
            self.table.setItem(
                row,
                1,
                QTableWidgetItem("Permitido" if key in role_defaults else "Denegado"),
            )
            control = QComboBox()
            for label, value in [
                ("Predeterminado", None),
                ("Permitir", True),
                ("Denegar", False),
            ]:
                control.addItem(label, value)
            value = self.original.get(key)
            control.setCurrentIndex(0 if value is None else (1 if value else 2))
            self.controls[key] = control
            self.table.setCellWidget(row, 2, control)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 180)
        layout.addWidget(self.table)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        changes = {
            key: control.currentData()
            for key, control in self.controls.items()
            if control.currentIndex() != 0
        }
        try:
            self.save_operation(changes, self.original)
        except (ValueError, PermissionError) as exc:
            self.message.setText(str(exc))
            return
        self.accept()
