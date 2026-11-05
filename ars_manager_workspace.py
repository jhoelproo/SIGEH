"""Searchable insurer navigation and a canonical-name merge workflow."""

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QTabWidget,
    QWidget,
    QSplitter,
    QComboBox,
    QPushButton,
    QDialogButtonBox,
)

from admission_contract import CANONICAL_ARS_ALIASES, canonicalize_ars


class InsurerMergeDialog(QDialog):
    def __init__(self, names, preview, merge, worker_factory, parent=None):
        super().__init__(parent)
        self.preview, self.merge = preview, merge
        self.worker_factory = worker_factory
        self.worker = None
        self.plan = None
        self.setWindowTitle("Fusionar ARS equivalentes")
        self.resize(620, 390)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Usa como principal el nombre de Admisión. Se conservan las tarifas del destino y los importes de los recibos guardados."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.source = QComboBox()
        self.source.addItems(names)
        self.destination = QComboBox()
        self.destination.addItems(
            sorted(
                name for name in CANONICAL_ARS_ALIASES if name != "SENASA SUBSIDIADO"
            )
        )
        form.addRow("Origen en Facturación", self.source)
        form.addRow("Nombre principal de Admisión", self.destination)
        layout.addLayout(form)
        self.check = QPushButton("Revisar fusión")
        layout.addWidget(self.check)
        self.status = QLabel(
            "Selecciona el origen y revisa los datos antes de fusionar."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("Fusionar ARS")
        self.save_button.setEnabled(False)
        buttons.button(QDialogButtonBox.Cancel).setText("Cerrar")
        layout.addWidget(buttons)
        self.check.clicked.connect(self._preview)
        buttons.accepted.connect(self._merge)
        buttons.rejected.connect(self.reject)
        self.source.currentTextChanged.connect(self._source_changed)
        self.destination.currentTextChanged.connect(self._invalidate)
        self._source_changed(self.source.currentText())

    def _source_changed(self, name):
        canonical = canonicalize_ars(name)
        if canonical:
            self.destination.setCurrentText(canonical)
        self._invalidate()

    def _invalidate(self, *_args):
        self.plan = None
        self.save_button.setEnabled(False)

    def _run(self, operation, callback):
        if self.worker is not None:
            return
        self.source.setEnabled(False)
        self.destination.setEnabled(False)
        self.check.setEnabled(False)
        self.save_button.setEnabled(False)
        self.worker = self.worker_factory(operation, self)
        self.worker.completed.connect(callback)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _preview(self):
        source, destination = self.source.currentText(), self.destination.currentText()
        self.status.setText("Revisando catálogos y tarifas…")
        self._run(lambda: self.preview(source, destination), self._preview_ready)

    def _preview_ready(self, plan):
        self.plan = plan
        target = (
            plan["insurers"].get(plan["destination"])
            or plan["insurers"][plan["source"]]
        )
        self.status.setText(
            f"{plan['source']} → {plan['destination']}\n"
            f"Sala del destino: RD$ {float(target['sala_emergencia']):,.2f}.\n"
            "Los ítems ausentes se incorporan; los precios ya existentes del destino se conservan. "
            "Los recibos mantienen sus importes y sus identificadores."
        )

    def _merge(self):
        if self.plan is None:
            return
        plan = self.plan
        self.status.setText("Guardando la fusión…")
        self._run(
            lambda: self.merge(plan["source"], plan["destination"], plan["token"]),
            self._merged,
        )

    def _merged(self, _result):
        self._merge_done = True

    def _failed(self, message):
        self.plan = None
        self.status.setText("No se guardó la fusión. " + str(message))

    def _finished(self):
        self.worker = None
        self.source.setEnabled(True)
        self.destination.setEnabled(True)
        self.check.setEnabled(True)
        self.save_button.setEnabled(self.plan is not None)
        if getattr(self, "_merge_done", False):
            self.accept()

    def reject(self):
        if self.worker is None:
            super().reject()


class InsurerWorkspace(QWidget):
    def __init__(self, owner, groups, parent=None):
        super().__init__(parent)
        self.owner = owner
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        rail = QWidget()
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 8, 0)
        rail_layout.addWidget(QLabel("Aseguradoras"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar ARS…")
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.setMinimumWidth(155)
        rail_layout.addWidget(self.search)
        rail_layout.addWidget(self.list, 1)
        splitter.addWidget(rail)
        self.tabs = QTabWidget()
        for title, widgets in groups:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 8, 8, 8)
            for widget in widgets:
                page_layout.addWidget(widget)
            page_layout.addStretch(1)
            self.tabs.addTab(page, title)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 850])
        layout.addWidget(splitter)
        self.search.textChanged.connect(self._filter)
        self.list.currentTextChanged.connect(owner.ars_combo.setCurrentText)
        owner.ars_combo.currentTextChanged.connect(self._select)
        self.refresh_names(owner._ars_names or [])

    def refresh_names(self, names):
        with QSignalBlocker(self.list):
            self.list.clear()
            self.list.addItems(names)
            self._select(self.owner.ars_combo.currentText())
        self._filter(self.search.text())

    def _select(self, name):
        matches = self.list.findItems(name, Qt.MatchExactly)
        if matches:
            with QSignalBlocker(self.list):
                self.list.setCurrentItem(matches[0])

    def _filter(self, text):
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(text.casefold() not in item.text().casefold())
