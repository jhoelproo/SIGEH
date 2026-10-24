"""Configure quantities of consumables per medication using existing workers."""

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from billing_item_rules import MAX_MATERIAL_UNITS
from monthly_candidate_search import normalized_name


class MedicationMaterialDialog(QDialog):
    def __init__(self, medication_id, *, load, save, worker_factory, parent=None):
        super().__init__(parent)
        self.medication_id = medication_id
        self.load = load
        self.save = save
        self.worker_factory = worker_factory
        self.worker = None
        self.saved = False
        self.setWindowTitle("Materiales vinculados al medicamento")
        self.resize(740, 550)
        layout = QVBoxLayout(self)
        self.title = QLabel("Materiales del medicamento")
        self.title.setObjectName("ModernTitle")
        layout.addWidget(self.title)
        hint = QLabel(
            "Selecciona los materiales y las unidades por cada unidad del medicamento. Se añadirán al recibo automáticamente. Sin selección, este medicamento no añadirá materiales."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar material...")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("ModernDataTable")
        self.table.setHorizontalHeaderLabels(
            ("Usar", "Material", "Precio", "Unidades por medicamento")
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (0, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents
            )
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        self.status = QLabel("Cargando materiales...")
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self.save_button = self.buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("Guardar vinculación")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cerrar")
        layout.addWidget(self.buttons)
        QTimer.singleShot(
            0, lambda: self._run(lambda: self.load(self.medication_id), self._loaded)
        )

    def _run(self, operation, completed):
        if self.worker is not None:
            return
        self.table.setEnabled(False)
        self.buttons.setEnabled(False)
        self.worker = self.worker_factory(operation, self)
        self.worker.completed.connect(completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _loaded(self, data):
        self.title.setText(str(data["medication"]["nombre"]))
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(data["materials"]))
        for row, material in enumerate(data["materials"]):
            self._material_row(row, material)
        del blocker
        self._filter(self.search.text())
        self.status.setText("Selecciona materiales y guarda la vinculación.")

    def _material_row(self, row, material):
        checkbox = QTableWidgetItem()
        checkbox.setData(Qt.UserRole, int(material["id"]))
        checkbox.setFlags(
            Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        )
        checkbox.setCheckState(Qt.Checked if material["units"] else Qt.Unchecked)
        self.table.setItem(row, 0, checkbox)
        label = str(material["nombre"]) + (
            " (inactivo)" if not material["is_active"] else ""
        )
        self.table.setItem(row, 1, QTableWidgetItem(label))
        self.table.setItem(
            row, 2, QTableWidgetItem(f"RD$ {float(material['precio']):,.2f}")
        )
        quantity = QSpinBox()
        quantity.setMinimumHeight(36)
        quantity.setRange(1, MAX_MATERIAL_UNITS)
        quantity.setValue(max(1, int(material["units"])))
        quantity.setEnabled(checkbox.checkState() == Qt.Checked)
        self.table.setCellWidget(row, 3, quantity)

    def _selection_changed(self, item):
        if item.column() == 0:
            self.table.cellWidget(item.row(), 3).setEnabled(
                item.checkState() == Qt.Checked
            )

    def _filter(self, text):
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 1).text()
            self.table.setRowHidden(
                row, normalized_name(text) not in normalized_name(name)
            )

    def quantities(self):
        return {
            int(self.table.item(row, 0).data(Qt.UserRole)): self.table.cellWidget(
                row, 3
            ).value()
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.Checked
        }

    def _save(self):
        quantities = self.quantities()
        self.status.setText("Guardando vinculación...")
        self._run(lambda: self.save(self.medication_id, quantities), self._saved)

    def _saved(self, data):
        self._loaded(data)
        self.saved = True
        self.status.setText("Vinculación guardada.")

    def _failed(self, error):
        self.status.setText("No se guardaron cambios. " + str(error))

    def _finished(self):
        self.worker = None
        self.table.setEnabled(True)
        self.buttons.setEnabled(True)

    def reject(self):
        if self.worker is None:
            super().reject()
