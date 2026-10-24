"""Administrator-only diagnoses, aliases, priority, blocks and ambiguous pairs."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from diagnosis_recommendations import (
    CATEGORIES,
    DiagnosisCatalog,
    DEFAULT_SETTINGS,
    SETTING_LIMITS,
    SPECIALTIES,
    normalize_config,
    text_key,
)
from diagnosis_recommendation_store import admin_catalog, load_config, save_config
from diagnosis_recommendation_ui import RecommendationTask, recommendation_connection

SETTING_LABELS = {
    "min_receipts": "Mínimo de recibos distintos",
    "frequency_percent": "Frecuencia mínima (%)",
    "invoiced_weight": "Peso de facturados",
    "pending_weight": "Peso de pendientes",
    "lab_limit": "Laboratorios visibles",
    "image_limit": "Imágenes visibles",
}


def combo(values, selected="", editable=False):
    widget = QComboBox()
    widget.setEditable(editable)
    widget.addItems(list(values))
    widget.setCurrentText(str(selected))
    widget.setToolTip(widget.currentText())
    widget.currentTextChanged.connect(widget.setToolTip)
    return widget


class RecommendationAdminDialog(QDialog):
    def __init__(self, backend, actor, model, parent=None):
        super().__init__(parent)
        if not backend.user_is_admin(actor):
            raise PermissionError(
                "Solo el administrador puede configurar recomendaciones."
            )
        self.backend, self.actor = backend, dict(actor)
        self.model, self.items = model, []
        self.version = 0
        self.worker = None
        self.saved = False
        self.setWindowTitle("Recomendaciones por diagnóstico")
        screen = parent.screen() if parent is not None else self.screen()
        available = screen.availableGeometry()
        self.resize(
            min(1120, available.width() - 40), min(680, available.height() - 60)
        )
        self.root = QVBoxLayout(self)
        title = QLabel("Recomendaciones por diagnóstico")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#bd85ff;")
        self.root.addWidget(title)
        self.status = QLabel("Cargando configuración compartida…")
        self.status.setWordWrap(True)
        self.root.addWidget(self.status)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self.diagnoses = self._table(
            ["Diagnóstico correcto", "Abreviaturas y equivalencias (;)"]
        )
        self.rules = self._table(
            ["Diagnóstico", "Especialidad", "Categoría", "Ítem", "Acción", "Prioridad"]
        )
        self.pending = self._table(
            ["Diagnóstico", "Especialidad", "Categoría", "Ítem pendiente de asociación"]
        )
        self.diagnoses.setColumnWidth(0, 280)
        self.rules.horizontalHeader().setStretchLastSection(False)
        for column, width in (
            (0, 190),
            (1, 175),
            (2, 140),
            (3, 260),
            (4, 120),
            (5, 80),
        ):
            self.rules.setColumnWidth(column, width)
        self.pending.setEditTriggers(QTableWidget.NoEditTriggers)
        self._editable_tab(self.diagnoses, "Diagnósticos", self.add_diagnosis)
        self._editable_tab(self.rules, "Asociaciones", self.add_rule)
        self._pending_tab()
        self._settings_tab()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("Guardar configuración")
        self.save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.reload_button = QPushButton("Actualizar")
        self.reload_button.clicked.connect(self.reload)
        buttons.addButton(self.reload_button, QDialogButtonBox.ActionRole)
        self.root.addWidget(buttons)
        self._style_buttons(parent)
        self.reload()

    def _style_buttons(self, parent):
        style_button = getattr(self.backend, "set_button_role", None)
        if style_button is None:
            return
        is_dark = bool(getattr(parent, "is_dark_mode", False))
        for button in self.findChildren(QPushButton):
            role = "primary" if button is self.save_button else "secondary"
            style_button(button, role, is_dark=is_dark)

    def _table(self, headings):
        table = QTableWidget(0, len(headings))
        table.setHorizontalHeaderLabels(headings)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _editable_tab(self, table, label, add):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        create = QPushButton("Añadir")
        create.clicked.connect(lambda _checked=False: add())
        remove = QPushButton("Eliminar selección")
        remove.clicked.connect(lambda: self.remove_rows(table))
        actions.addWidget(create)
        actions.addWidget(remove)
        actions.addStretch()
        layout.addLayout(actions)
        self.tabs.addTab(page, label)

    def _pending_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(
            "Estas asociaciones requieren decisión del administrador. No se asignan automáticamente al facturador."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(self.pending, 1)
        actions = QHBoxLayout()
        for label, mode in (
            ("Recomendar selección", "pin"),
            ("Bloquear selección", "block"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, mode=mode: self.review_pending(mode)
            )
            actions.addWidget(button)
        layout.addLayout(actions)
        self.tabs.addTab(page, "Por revisar")

    def _settings_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        self.settings = {}
        for name, value in DEFAULT_SETTINGS.items():
            field = QSpinBox()
            field.setRange(*SETTING_LIMITS[name])
            field.setValue(value)
            self.settings[name] = field
            form.addRow(SETTING_LABELS[name], field)
        note = QLabel(
            "Se utiliza un medicamento principal por cada uno de los dos primeros diagnósticos. Añadir al carrito o reimprimir no genera aprendizaje."
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.tabs.addTab(page, "Parámetros")

    @staticmethod
    def remove_rows(table):
        for row in sorted(
            {index.row() for index in table.selectedIndexes()}, reverse=True
        ):
            table.removeRow(row)

    def add_diagnosis(self, name="", aliases=()):
        row = self.diagnoses.rowCount()
        self.diagnoses.insertRow(row)
        self.diagnoses.setItem(row, 0, QTableWidgetItem(name))
        self.diagnoses.setItem(row, 1, QTableWidgetItem("; ".join(aliases)))

    def _diagnosis_names(self):
        return [
            self.diagnoses.item(row, 0).text().strip()
            for row in range(self.diagnoses.rowCount())
            if self.diagnoses.item(row, 0).text().strip()
        ]

    def add_rule(self, value=None):
        value = value or {}
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        names = self._diagnosis_names()
        diagnosis = next(
            (
                name
                for name in names
                if text_key(name) == text_key(value.get("diagnosis"))
            ),
            value.get("diagnosis", ""),
        )
        self.rules.setCellWidget(row, 0, combo(names, diagnosis, True))
        self.rules.setCellWidget(
            row, 1, combo(SPECIALTIES, value.get("specialty", SPECIALTIES[0]))
        )
        category = combo(CATEGORIES, value.get("category", CATEGORIES[0]))
        self.rules.setCellWidget(row, 2, category)
        item = combo([], editable=True)
        self.rules.setCellWidget(row, 3, item)
        category.currentTextChanged.connect(
            lambda selected, item=item: self._item_options(item, selected)
        )
        self._item_options(item, category.currentText(), value.get("item", ""))
        mode = combo(
            ["Recomendar", "Bloquear"],
            "Bloquear" if value.get("mode") == "block" else "Recomendar",
        )
        self.rules.setCellWidget(row, 4, mode)
        priority = QSpinBox()
        priority.setRange(0, 9999)
        priority.setValue(value.get("priority", 1))
        self.rules.setCellWidget(row, 5, priority)
        height = max(
            self.rules.cellWidget(row, column).sizeHint().height()
            for column in range(self.rules.columnCount())
        )
        self.rules.setRowHeight(row, height + 8)

    def _item_options(self, widget, category, selected=""):
        widget.clear()
        widget.addItems(
            [row["nombre"] for row in self.items if row["categoria"] == category]
        )
        widget.setCurrentText(selected)

    def review_pending(self, mode):
        for row in sorted({index.row() for index in self.pending.selectedIndexes()}):
            values = [self.pending.item(row, column).text() for column in range(4)]
            self.add_rule(
                dict(
                    zip(("diagnosis", "specialty", "category", "item"), values),
                    mode=mode,
                    priority=1,
                )
            )
        self.tabs.setCurrentIndex(1)

    def configuration(self):
        diagnoses = [
            {
                "name": self.diagnoses.item(row, 0).text(),
                "aliases": self.diagnoses.item(row, 1).text().split(";"),
            }
            for row in range(self.diagnoses.rowCount())
        ]
        rules = [self._rule_value(row) for row in range(self.rules.rowCount())]
        return normalize_config(
            {
                "diagnoses": diagnoses,
                "rules": rules,
                "settings": {
                    name: field.value() for name, field in self.settings.items()
                },
            }
        )

    def _rule_value(self, row):
        return {
            "diagnosis": self.rules.cellWidget(row, 0).currentText(),
            "specialty": self.rules.cellWidget(row, 1).currentText(),
            "category": self.rules.cellWidget(row, 2).currentText(),
            "item": self.rules.cellWidget(row, 3).currentText(),
            "mode": "block"
            if self.rules.cellWidget(row, 4).currentText() == "Bloquear"
            else "pin",
            "priority": self.rules.cellWidget(row, 5).value(),
        }

    def _task(self, callback, completed):
        self.tabs.setEnabled(False)
        self.save_button.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.worker = RecommendationTask(callback, self)
        self.worker.completed.connect(completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def reload(self):
        if self.worker is not None:
            return
        self.status.setText("Cargando configuración compartida…")

        def fetch():
            with recommendation_connection(self.backend) as con:
                items = admin_catalog(con, self.actor)
                config = load_config(con)
                rows = con.execute(
                    "SELECT change_key,payload FROM sigeh_recommendations.changes WHERE change_key LIKE 'group:%%' AND payload IS NOT NULL"
                ).fetchall()
                return {
                    "config": config,
                    "items": items,
                    "groups": {
                        row["change_key"][6:]: dict(row["payload"]) for row in rows
                    },
                }

        self._task(fetch, self._loaded)

    def _loaded(self, data):
        self.items = data["items"]
        self.model = data
        config = data["config"]
        self.version = config["version"]
        self.diagnoses.setRowCount(0)
        self.rules.setRowCount(0)
        definitions = {text_key(row["name"]): row for row in config["diagnoses"]}
        catalog = DiagnosisCatalog(config)
        for group in data["groups"].values():
            name = group["diagnosis"]
            if text_key(name) in catalog.aliases:
                continue
            definitions.setdefault(text_key(name), {"name": name, "aliases": []})
        for value in sorted(definitions.values(), key=lambda row: row["name"]):
            self.add_diagnosis(value["name"], value["aliases"])
        for value in config["rules"]:
            self.add_rule(value)
        for name, value in config["settings"].items():
            self.settings[name].setValue(value)
        self._fill_pending()
        self.status.setText(
            f"Configuración compartida v{self.version}. Solo el administrador puede modificarla."
        )

    def _fill_pending(self):
        self.pending.setRowCount(0)
        reviewed = {
            (row["diagnosis"], row["specialty"], row["category"], text_key(row["item"]))
            for row in self.model["config"]["rules"]
        }
        for group in self.model["groups"].values():
            for item in group["items"]:
                identity = (
                    text_key(group["diagnosis"]),
                    group["specialty"],
                    item["category"],
                    text_key(item["item"]),
                )
                if not item["candidates"] or identity in reviewed:
                    continue
                row = self.pending.rowCount()
                self.pending.insertRow(row)
                for column, value in enumerate(
                    (
                        group["diagnosis"],
                        group["specialty"],
                        item["category"],
                        item["item"],
                    )
                ):
                    self.pending.setItem(row, column, QTableWidgetItem(value))

    def save(self):
        if self.worker is not None:
            return
        try:
            value = self.configuration()
        except ValueError as exc:
            self._failed(exc)
            return
        self.status.setText("Guardando configuración compartida…")

        def persist():
            with recommendation_connection(self.backend) as con:
                return save_config(
                    con,
                    value,
                    self.actor,
                    self.version,
                    audit=self.backend._audit_billing_item_rule,
                )

        self._task(persist, self._saved)

    def _saved(self, version):
        self.version = version
        self.saved = True

    def _failed(self, error):
        self.status.setText(
            "No se guardaron cambios. Revisa el mensaje o actualiza la configuración."
        )
        QMessageBox.warning(self, "Recomendaciones", str(error))

    def _finished(self):
        self.worker = None
        self.tabs.setEnabled(True)
        self.save_button.setEnabled(True)
        self.reload_button.setEnabled(True)
        if self.saved:
            self.accept()

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText("Espera a que termine la operación antes de cerrar.")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self.worker is None:
            super().reject()

    def shutdown_for_logout(self):
        if self.worker is not None:
            self.worker.cancel()
            if not self.worker.wait(5000):
                self.worker.setParent(None)
        self.close()
