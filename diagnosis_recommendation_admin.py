"""Administrator-only diagnoses, aliases, priority, blocks and ambiguous pairs."""

from collections import deque
from contextlib import contextmanager
from time import perf_counter

from PySide6.QtCore import QSignalBlocker, QStringListModel, QTimer, Qt

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
POPULATION_BATCH_SIZE = 25
POPULATION_BUDGET_SECONDS = 0.008


def pending_rows(model, catalog):
    reviewed = {
        (row["diagnosis"], row["specialty"], row["category"], text_key(row["item"]))
        for row in model["config"]["rules"]
    }
    listed, result = set(), []
    for group in model["groups"].values():
        diagnosis = catalog.learning_label(group["diagnosis"])
        if text_key(diagnosis) in catalog.excluded:
            continue
        for item in group["items"]:
            if not item["candidates"]:
                continue
            identity = (
                text_key(diagnosis),
                group["specialty"],
                item["category"],
                text_key(item["item"]),
            )
            if identity not in reviewed and identity not in listed:
                listed.add(identity)
                result.append(
                    (diagnosis, group["specialty"], item["category"], item["item"])
                )
    return result


def prepare_admin_data(data):
    config = {
        **normalize_config(data["config"], preserve_conflicts=True),
        "version": data["config"]["version"],
    }
    model = {"config": config, "groups": data["groups"], "items": data["items"]}
    catalog = DiagnosisCatalog(config)
    definitions = {
        text_key(row["name"]): row
        for row in config["diagnoses"]
        if text_key(row["name"]) not in catalog.excluded
    }
    for source in dict.fromkeys(
        group["diagnosis"] for group in data["groups"].values()
    ):
        name = catalog.learning_label(source)
        if (
            text_key(name) not in catalog.aliases
            and text_key(name) not in catalog.excluded
        ):
            definitions.setdefault(text_key(name), {"name": name, "aliases": []})
    return {
        **model,
        "insurers": data.get("insurers", []),
        "definitions": sorted(definitions.values(), key=lambda row: row["name"]),
        "pending_rows": pending_rows(model, catalog),
        "configuration_error": configuration_error(config),
    }


def configuration_error(config):
    try:
        normalize_config(config)
    except ValueError as error:
        return str(error)
    return ""


@contextmanager
def table_batch(tables):
    blockers = [QSignalBlocker(table) for table in tables]
    enabled = [table.updatesEnabled() for table in tables]
    for table in tables:
        table.setUpdatesEnabled(False)
    try:
        yield
    finally:
        for table, previous in zip(tables, enabled):
            table.setUpdatesEnabled(previous)
        for blocker in blockers:
            blocker.unblock()


def combo(values, selected="", editable=False, model=None):
    widget = QComboBox()
    widget.setEditable(editable)
    widget.setInsertPolicy(QComboBox.NoInsert)
    widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    widget.setMinimumContentsLength(12)
    if model is None:
        widget.addItems(list(values))
    else:
        widget.setModel(model)
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
        self._operation = None
        self._populating = False
        self._closing = False
        self._population = deque()
        self._search_cache = {}
        self._item_models = {}
        self.population_timer = QTimer(self)
        self.population_timer.setInterval(0)
        self.population_timer.timeout.connect(self._populate_next)
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
        search_bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar diagnóstico, equivalencia o ítem…")
        self.search.setAccessibleName("Buscar en recomendaciones por diagnóstico")
        self.search.setClearButtonEnabled(True)
        self.search_results = QLabel()
        search_bar.addWidget(self.search, 1)
        search_bar.addWidget(self.search_results)
        self.root.addLayout(search_bar)
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
        self.ars_selector = QComboBox()
        self.ars_selector.setAccessibleName("Catálogo ARS de las asociaciones")
        self.ars_selector.addItem("Compartido / asociaciones anteriores", 0)
        self.ars_selector.currentIndexChanged.connect(self._apply_search)
        scope = QHBoxLayout()
        scope.addWidget(QLabel("Catálogo ARS:"))
        scope.addWidget(self.ars_selector, 1)
        self.tabs.widget(1).layout().insertLayout(0, scope)
        self._pending_tab()
        self._settings_tab()
        self.search.textChanged.connect(self._apply_search)
        self.tabs.currentChanged.connect(self._apply_search)
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
        table.itemChanged.connect(lambda *_args: self._invalidate_search(table))
        table.model().rowsRemoved.connect(lambda *_args: self._invalidate_search(table))
        return table

    def _invalidate_search(self, table):
        self._search_cache.pop(table, None)
        self._apply_search()

    @staticmethod
    def _search_cell(table, row, column):
        widget = table.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QSpinBox):
            return str(widget.value())
        item = table.item(row, column)
        return item.text() if item is not None else ""

    def _filter_table(self, table, terms):
        if table not in self._search_cache:
            self._search_cache[table] = [
                text_key(
                    " ".join(
                        self._search_cell(table, row, column)
                        for column in range(table.columnCount())
                    )
                )
                for row in range(table.rowCount())
            ]
        shown = 0
        for row, text in enumerate(self._search_cache[table]):
            matches = all(term in text for term in terms)
            matches = matches and self._matches_ars_scope(table, row)
            if table.isRowHidden(row) == matches:
                table.setRowHidden(row, not matches)
            shown += matches
        return shown

    def _matches_ars_scope(self, table, row):
        if table is not self.rules or not hasattr(self, "ars_selector"):
            return True
        ars_id = int(table.cellWidget(row, 0).property("arsId") or 0)
        return ars_id == int(self.ars_selector.currentData() or 0)

    def _apply_search(self, *_args):
        if self._populating or self._closing or not hasattr(self, "pending"):
            return
        terms = text_key(self.search.text()).split()
        tables = (self.diagnoses, self.rules, self.pending)
        counts = [self._filter_table(table, terms) for table in tables]
        current = self.tabs.currentIndex()
        searching_table = current in range(len(tables))
        self.search.setEnabled(searching_table)
        self.search_results.setVisible(searching_table)
        if searching_table:
            self.search_results.setText(
                f"{counts[current]} de {tables[current].rowCount()}"
            )

    def _reveal_added_row(self, table, row):
        self.search.clear()
        table.setCurrentCell(row, 0)
        table.scrollToItem(table.item(row, 0))
        table.setFocus()
        table.editItem(table.item(row, 0))

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
            {
                index.row()
                for index in table.selectedIndexes()
                if not table.isRowHidden(index.row())
            },
            reverse=True,
        ):
            table.removeRow(row)

    def add_diagnosis(self, name="", aliases=()):
        row = self.diagnoses.rowCount()
        self.diagnoses.insertRow(row)
        self.diagnoses.setItem(row, 0, QTableWidgetItem(name))
        self.diagnoses.item(row, 0).setData(Qt.UserRole, name)
        self.diagnoses.setItem(row, 1, QTableWidgetItem("; ".join(aliases)))
        if not name:
            self._reveal_added_row(self.diagnoses, row)

    def _diagnosis_names(self):
        return [
            self.diagnoses.item(row, 0).text().strip()
            for row in range(self.diagnoses.rowCount())
            if self.diagnoses.item(row, 0).text().strip()
        ]

    def add_rule(self, value=None):
        value = value or {}
        ars_id = int(value.get("ars_id", self.ars_selector.currentData()) or 0)
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        self.rules.setCellWidget(row, 0, self._diagnosis_selector(value))
        self.rules.cellWidget(row, 0).setProperty("arsId", ars_id)
        self.rules.setCellWidget(
            row, 1, combo(SPECIALTIES, value.get("specialty", SPECIALTIES[0]))
        )
        category = combo(CATEGORIES, value.get("category", CATEGORIES[0]))
        self.rules.setCellWidget(row, 2, category)
        item = combo([], editable=True)
        self.rules.setCellWidget(row, 3, item)
        category.currentTextChanged.connect(
            lambda selected, item=item, ars_id=ars_id: self._item_options(
                item, selected, ars_id=ars_id
            )
        )
        self._item_options(
            item, category.currentText(), value.get("item", ""), ars_id=ars_id
        )
        self._rule_controls(row, value)
        if not value:
            self.search.clear()
            self.rules.setCurrentCell(row, 0)
            self.rules.cellWidget(row, 0).setFocus()

    def _diagnosis_selector(self, value):
        names = self._bulk_names if self._populating else self._diagnosis_names()
        labels = (
            self._bulk_name_keys
            if self._populating
            else {text_key(name): name for name in names}
        )
        diagnosis = labels.get(
            text_key(value.get("diagnosis")), value.get("diagnosis", "")
        )
        return combo(
            names, diagnosis, True, self._bulk_names_model if self._populating else None
        )

    def _rule_controls(self, row, value):
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
        for column in range(self.rules.columnCount()):
            field = self.rules.cellWidget(row, column)
            signal = (
                field.valueChanged
                if isinstance(field, QSpinBox)
                else field.currentTextChanged
            )
            signal.connect(lambda *_args: self._invalidate_search(self.rules))
        self._invalidate_search(self.rules)

    def _item_options(self, widget, category, selected="", *, ars_id=0):
        identity = (category, 0 if category == "Medicamentos" else ars_id)
        if identity not in self._item_models:
            self._item_models[identity] = QStringListModel(
                sorted(
                    {
                        row["nombre"]
                        for row in self.items
                        if row["categoria"] == category
                        and int(row.get("ars_id") or 0) == identity[1]
                    }
                ),
                self,
            )
        widget.setModel(self._item_models[identity])
        widget.setCurrentText(selected)

    def review_pending(self, mode):
        for row in sorted(
            {
                index.row()
                for index in self.pending.selectedIndexes()
                if not self.pending.isRowHidden(index.row())
            }
        ):
            values = [self.pending.item(row, column).text() for column in range(4)]
            self.add_rule(
                dict(
                    zip(("diagnosis", "specialty", "category", "item"), values),
                    mode=mode,
                    priority=1,
                    ars_id=int(self.ars_selector.currentData() or 0),
                )
            )
        self.tabs.setCurrentIndex(1)

    def configuration(self):
        diagnoses = [
            self._diagnosis_value(row) for row in range(self.diagnoses.rowCount())
        ]
        excluded = self._removed_diagnoses(diagnoses)
        rules = self._retained_rules(diagnoses, excluded)
        shared, scoped = [], []
        for row in rules:
            ars_id = row.pop("ars_id")
            if ars_id:
                scoped.append({**row, "ars_id": ars_id})
            else:
                shared.append(row)
        return normalize_config(
            {
                "diagnoses": diagnoses,
                "excluded_diagnoses": excluded,
                "rules": shared,
                "ars_rules": scoped,
                "settings": {
                    name: field.value() for name, field in self.settings.items()
                },
            }
        )

    def _removed_diagnoses(self, diagnoses):
        names = {text_key(row["name"]) for row in diagnoses}
        excluded = {
            text_key(name): name
            for name in self.model.get("config", {}).get("excluded_diagnoses", [])
        }
        excluded.update(
            {
                key: name
                for key, name in self._original_diagnoses.items()
                if key not in names
            }
        )
        return [name for key, name in excluded.items() if key not in names]

    def _retained_rules(self, diagnoses, excluded):
        rules = [self._rule_value(row) for row in range(self.rules.rowCount())]
        aliases = {text_key(alias) for row in diagnoses for alias in row["aliases"]}
        removed = {text_key(name) for name in excluded} - aliases
        return [row for row in rules if text_key(row["diagnosis"]) not in removed]

    def _diagnosis_value(self, row):
        item = self.diagnoses.item(row, 0)
        aliases = self.diagnoses.item(row, 1).text().split(";")
        original = item.data(Qt.UserRole)
        if original and text_key(original) != text_key(item.text()):
            aliases.append(original)
        return {"name": item.text(), "aliases": aliases}

    def _rule_value(self, row):
        return {
            "ars_id": int(self.rules.cellWidget(row, 0).property("arsId") or 0),
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
        if self.worker is not None or self._populating:
            return
        self._operation = "load"
        self.status.setText("Cargando configuración compartida…")

        def fetch():
            with recommendation_connection(self.backend) as con:
                items = admin_catalog(con, self.actor)
                config = load_config(con)
                rows = con.execute(
                    "SELECT change_key,payload FROM sigeh_recommendations.changes WHERE change_key LIKE 'group:%%' AND payload IS NOT NULL"
                ).fetchall()
                data = {
                    "config": config,
                    "items": items,
                    "insurers": [
                        dict(row)
                        for row in con.execute(
                            "SELECT id,nombre FROM ars ORDER BY nombre"
                        ).fetchall()
                    ],
                    "groups": {
                        row["change_key"][6:]: dict(row["payload"]) for row in rows
                    },
                }
            return prepare_admin_data(data)

        self._task(fetch, self._loaded)

    def _loaded(self, data):
        if self._closing:
            return
        data = data if "definitions" in data else prepare_admin_data(data)
        self.items = data["items"]
        self._original_diagnoses = {
            text_key(row["name"]): row["name"] for row in data["definitions"]
        }
        selected_ars = self.ars_selector.currentData()
        with QSignalBlocker(self.ars_selector):
            self.ars_selector.clear()
            self.ars_selector.addItem("Compartido / asociaciones anteriores", 0)
            for row in data.get("insurers", []):
                self.ars_selector.addItem(row["nombre"], row["id"])
            self.ars_selector.setCurrentIndex(
                max(0, self.ars_selector.findData(selected_ars))
            )
        self.model = {key: data[key] for key in ("config", "groups", "items")}
        self.version = data["config"]["version"]
        self._configuration_error = data["configuration_error"]
        self._populating = True
        self._set_busy(True)
        self._reset_population(data)
        self._population_total = len(self._population)
        for name, value in data["config"]["settings"].items():
            self.settings[name].setValue(value)
        self._populate_next()

    def _reset_population(self, data):
        with table_batch((self.diagnoses, self.rules, self.pending)):
            for table in (self.diagnoses, self.rules, self.pending):
                table.setRowCount(0)
        self._search_cache.clear()
        for model in self._item_models.values():
            model.deleteLater()
        self._item_models.clear()
        self._bulk_names = [value["name"] for value in data["definitions"]]
        self._bulk_name_keys = {text_key(name): name for name in self._bulk_names}
        if hasattr(self, "_bulk_names_model"):
            self._bulk_names_model.deleteLater()
        self._bulk_names_model = QStringListModel(self._bulk_names, self)
        self._population = deque(
            [
                (self.add_diagnosis, (value["name"], value["aliases"]))
                for value in data["definitions"]
            ]
            + [
                (self.add_rule, (value,))
                for value in [
                    *data["config"]["rules"],
                    *data["config"].get("ars_rules", []),
                ]
            ]
            + [(self._append_pending_row, (value,)) for value in data["pending_rows"]]
        )

    def _populate_next(self):
        if self._closing:
            return
        started = perf_counter()
        with table_batch((self.diagnoses, self.rules, self.pending)):
            for _ in range(min(POPULATION_BATCH_SIZE, len(self._population))):
                callback, args = self._population.popleft()
                callback(*args)
                if perf_counter() - started >= POPULATION_BUDGET_SECONDS:
                    break
        if self._population:
            self.status.setText(
                f"Cargando configuración: {self._population_total - len(self._population)} de {self._population_total} filas…"
            )
            self.population_timer.start()
            return
        self.population_timer.stop()
        self._populating = False
        self._search_cache.clear()
        self._apply_search()
        self.status.setText(
            f"Configuración compartida v{self.version}. Solo el administrador puede modificarla."
        )
        self._show_configuration_warning(
            self.model["config"], self._configuration_error
        )
        if self.worker is None:
            self._operation = None
            self._set_busy(False)

    def _show_configuration_warning(self, config, error=None):
        error = configuration_error(config) if error is None else error
        if error:
            conflicts = self._has_conflicting_rules(config["rules"])
            label = (
                "Asociaciones en conflicto" if conflicts else "Diagnósticos por revisar"
            )
            self.search.clear()
            self.tabs.setCurrentIndex(1 if conflicts else 0)
            self.status.setText(
                f"Configuración compartida v{self.version}. {label}: {error}"
            )

    @staticmethod
    def _has_conflicting_rules(rules):
        identities = [
            (row["diagnosis"], row["specialty"], row["category"], text_key(row["item"]))
            for row in rules
        ]
        return len(identities) != len(set(identities))

    def _fill_pending(self):
        with table_batch((self.pending,)):
            self.pending.setRowCount(0)
            for values in pending_rows(
                self.model, DiagnosisCatalog(self.model["config"])
            ):
                self._append_pending_row(values)
        self._search_cache.pop(self.pending, None)
        self._apply_search()

    def _append_pending_row(self, values):
        row = self.pending.rowCount()
        self.pending.insertRow(row)
        for column, value in enumerate(values):
            self.pending.setItem(row, column, QTableWidgetItem(value))

    def save(self):
        if self.worker is not None or self._populating:
            return
        try:
            value = self.configuration()
        except ValueError as exc:
            self._failed(exc)
            return
        self.status.setText("Guardando configuración compartida…")
        self._operation = "save"

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
        if self._closing:
            return
        self.status.setText(
            "No se guardaron cambios. Revisa el mensaje o actualiza la configuración."
        )
        QMessageBox.warning(self, "Recomendaciones", str(error))

    def _finished(self):
        self.worker = None
        if self._closing:
            return
        if not self._populating:
            self._operation = None
            self._set_busy(False)
        if self.saved:
            self.accept()

    def _set_busy(self, busy):
        self.tabs.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)

    def _cancel_loading(self):
        if self._operation != "load" and not self._populating:
            return False
        self._closing = True
        self.population_timer.stop()
        self._population.clear()
        self._populating = False
        if self.worker is not None:
            worker, self.worker = self.worker, None
            worker.requestInterruption()
            worker.setParent(None)
            worker.completed.disconnect(self._loaded)
            worker.failed.disconnect(self._failed)
            worker.finished.disconnect(self._finished)
        return True

    def closeEvent(self, event):
        if self._cancel_loading():
            super().closeEvent(event)
            return
        if self.worker is not None:
            self.status.setText("Espera a que termine la operación antes de cerrar.")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self._cancel_loading() or self.worker is None:
            super().reject()

    def shutdown_for_logout(self):
        if self._cancel_loading():
            self.close()
            return
        if self.worker is not None:
            self.worker.cancel()
            if not self.worker.wait(5000):
                self.worker.setParent(None)
        self.close()
