from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QDialog,
    QDialogButtonBox,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .context import AppContext
from .controller import AdmissionController
from .history import AdmissionHistoryDialog
from .documents import PendingPrintsDialog
from .operations import (
    AdmissionInternalConfigDialog,
    AdmissionPreferencesDialog,
    AdmissionReportDialog,
)


class AdmissionWidget(QWidget):
    """Pantalla PySide6 nativa basada en la estructura de la Admisión original."""

    QUICK_ACTIONS = (
        ("Reporte estadístico", "report"),
        ("Abrir listado en Excel", "excel"),
        ("Historial sin seguros", "uninsured"),
        ("Editar paciente", "edit"),
        ("Impresiones pendientes", "prints"),
        ("Configuración interna", "settings"),
    )

    def __init__(self, context: AppContext, controller: AdmissionController, parent=None):
        super().__init__(parent)
        self.context = context
        self.controller = controller
        self.editing_attention_id: int | None = None
        self._operation_in_progress = False
        self._history_dialogs = []
        self._is_dark = False
        self._layout_profile = "ESTANDAR"
        self.setObjectName("AdmissionWidget")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()
        self._connect_architecture_signals()
        self._apply_phase3_style()
        self._configure_tab_order()

    @staticmethod
    def _card(title: str, object_name: str) -> tuple[QGroupBox, QVBoxLayout]:
        card = QGroupBox(title)
        card.setObjectName(object_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        return card, layout

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(12)
        root.addWidget(self._build_header())

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setObjectName("AdmissionContentSplitter")
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.addWidget(self._build_form_area())
        self.content_splitter.addWidget(self._build_side_panel())
        self.content_splitter.setStretchFactor(0, 4)
        self.content_splitter.setStretchFactor(1, 2)
        self.content_splitter.setSizes([1040, 430])
        root.addWidget(self.content_splitter, 1)
        root.addWidget(self._build_summary())
        root.addWidget(self._build_bottom_bar())

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("AdmissionHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 12, 16, 12)
        layout.setSpacing(16)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("ADMISIÓN DE EMERGENCIAS")
        title.setObjectName("AdmissionTitle")
        subtitle = QLabel("Hospital Provincial Dr. Ángel Contreras Mejía")
        subtitle.setObjectName("AdmissionSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box, 1)

        self.date_label = QLabel(datetime.now().strftime("%d-%m-%Y"))
        self.date_label.setObjectName("AdmissionHeaderValue")
        self.shift_label = QLabel(self._shift_text())
        self.shift_label.setObjectName("AdmissionHeaderValue")
        self.user_label = QLabel(self.context.username or "Sin identificar")
        self.user_label.setObjectName("AdmissionHeaderValue")
        for caption, value in (
            ("Fecha", self.date_label),
            ("Turno", self.shift_label),
            ("Usuario", self.user_label),
        ):
            block = QVBoxLayout()
            label = QLabel(caption)
            label.setObjectName("AdmissionHeaderCaption")
            block.addWidget(label, alignment=Qt.AlignCenter)
            block.addWidget(value, alignment=Qt.AlignCenter)
            layout.addLayout(block)

        self.change_shift_button = QPushButton("Cambiar turno")
        self.change_shift_button.setObjectName("AdmissionSecondaryButton")
        self.change_shift_button.setEnabled(True)
        layout.addWidget(self.change_shift_button)
        self.menu_button = QToolButton()
        self.menu_button.setText("Menú")
        self.menu_button.setObjectName("AdmissionMenuButton")
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.menu_button)
        self.menu_history_action = menu.addAction("Historial")
        self.menu_preferences_action = menu.addAction("Preferencias")
        self.menu_close_action = menu.addAction("Cerrar módulo")
        self.menu_button.setMenu(menu)
        layout.addWidget(self.menu_button)
        return header

    def _shift_text(self) -> str:
        shift = dict(self.context.current_shift or {})
        return str(
            shift.get("label")
            or shift.get("tipo_turno")
            or shift.get("shift_type")
            or "Sin turno"
        )

    def _build_form_area(self) -> QWidget:
        container = QWidget()
        container.setObjectName("AdmissionFormArea")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 6, 0)
        outer.setSpacing(12)
        patient_card, patient_layout = self._card(
            "DATOS DEL PACIENTE", "AdmissionPatientCard"
        )
        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 3)
        form.setColumnStretch(3, 2)

        self.name_edit = self._line("Nombre completo del paciente")
        self.age_spin = QSpinBox()
        self.age_spin.setRange(0, 130)
        self.age_unit_combo = QComboBox()
        self.age_unit_combo.addItems(["Años", "Meses", "Días"])
        age_row = QWidget()
        age_layout = QHBoxLayout(age_row)
        age_layout.setContentsMargins(0, 0, 0, 0)
        age_layout.setSpacing(6)
        age_layout.addWidget(self.age_spin, 1)
        age_layout.addWidget(self.age_unit_combo, 1)
        self.sex_combo = QComboBox()
        self.sex_combo.addItems(["Femenino", "Masculino"])
        self.urgency_check = QCheckBox("Atención de urgencia")
        self.cedula_edit = self._line("000-0000000-0")
        self.phone_edit = self._line("(000) 000-0000")
        self.address_edit = self._line("Dirección del paciente")
        self.nationality_edit = self._line("Nacionalidad")
        self.ars_combo = QComboBox()
        self.ars_combo.setEditable(True)
        self.ars_combo.setInsertPolicy(QComboBox.NoInsert)
        self.ars_combo.lineEdit().setPlaceholderText("Seleccione la ARS")
        self.nss_edit = self._line("Número de seguridad social")
        self.history_button = QPushButton("Historial")
        self.history_button.setObjectName("AdmissionInfoButton")
        self.history_button.setEnabled(True)
        self.history_button.setToolTip("Abrir historial de atenciones")

        self._add_field(form, 0, "Nombre:", self.name_edit, 0, 1, 1, 3)
        self._add_field(form, 1, "Edad:", age_row, 0)
        self._add_field(form, 1, "Sexo:", self.sex_combo, 2)
        form.addWidget(self.urgency_check, 2, 1)
        form.addWidget(self.history_button, 2, 3)
        self._add_field(form, 3, "Cédula:", self.cedula_edit, 0)
        self._add_field(form, 3, "Teléfono:", self.phone_edit, 2)
        self._add_field(form, 4, "Dirección:", self.address_edit, 0, 1, 1, 3)
        self._add_field(form, 5, "Nacionalidad:", self.nationality_edit, 0)
        self._add_field(form, 5, "ARS:", self.ars_combo, 2)
        self._add_field(form, 6, "NSS:", self.nss_edit, 0, 1, 1, 3)
        patient_layout.addLayout(form)
        outer.addWidget(patient_card)

        attention_card, attention_layout = self._card(
            "DATOS DE LA ATENCIÓN", "AdmissionAttentionCard"
        )
        attention_grid = QGridLayout()
        attention_grid.setHorizontalSpacing(14)
        self.service_type_combo = QComboBox()
        self.service_type_combo.addItems(["EMERGENCIA", "CONSULTA"])
        self.specialty_combo = QComboBox()
        self.specialty_combo.addItems(["GENERAL", "PEDIATRÍA", "GINECOLOGÍA"])
        self.representative_value = QLabel(
            str((self.context.current_shift or {}).get("representative") or "Sin asignar")
        )
        self.representative_value.setObjectName("AdmissionRepresentativeValue")
        self._add_field(attention_grid, 0, "Tipo:", self.service_type_combo, 0)
        self._add_field(attention_grid, 0, "Especialidad:", self.specialty_combo, 2)
        self._add_field(attention_grid, 1, "Representante:", self.representative_value, 0, 1, 1, 3)
        attention_layout.addLayout(attention_grid)
        outer.addWidget(attention_card)
        outer.addStretch(1)
        return container

    @staticmethod
    def _line(placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        return edit

    @staticmethod
    def _add_field(
        grid: QGridLayout,
        row: int,
        label: str,
        widget: QWidget,
        column: int,
        row_span: int = 1,
        column_span: int = 1,
        field_span: int | None = None,
    ):
        label_widget = QLabel(label)
        label_widget.setObjectName("AdmissionFieldLabel")
        grid.addWidget(label_widget, row, column, row_span, column_span)
        actual_span = field_span if field_span is not None else 1
        grid.addWidget(widget, row, column + 1, row_span, actual_span)

    def _build_side_panel(self) -> QWidget:
        side = QWidget()
        side.setObjectName("AdmissionSidePanel")
        side.setMinimumWidth(300)
        side.setMaximumWidth(520)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(12)
        actions_card, actions_layout = self._card(
            "ACCIONES RÁPIDAS", "AdmissionActionsCard"
        )
        self.actions_grid = QGridLayout()
        self.actions_grid.setSpacing(8)
        self.quick_action_buttons = {}
        for index, (text, key) in enumerate(self.QUICK_ACTIONS):
            button = QPushButton(text)
            button.setObjectName("AdmissionQuickAction")
            button.setMinimumHeight(42)
            button.setEnabled(key == "uninsured")
            button.setToolTip(
                "Abrir historial sin seguros"
                if key == "uninsured"
                else "Se habilitará al migrar su flujo funcional"
            )
            self.actions_grid.addWidget(button, index // 2, index % 2)
            self.quick_action_buttons[key] = button
        actions_layout.addLayout(self.actions_grid)
        layout.addWidget(actions_card)

        info_card, info_layout = self._card("INFORMACIÓN", "AdmissionInfoCard")
        self.side_info = QLabel(
            "Complete los datos del paciente. La atención se asociará al turno y "
            "representante visibles en la cabecera."
        )
        self.side_info.setWordWrap(True)
        self.side_info.setObjectName("AdmissionInfoText")
        info_layout.addWidget(self.side_info)
        self.validation_status = QLabel("Formulario listo")
        self.validation_status.setObjectName("AdmissionStatusPill")
        self.validation_status.setAlignment(Qt.AlignCenter)
        info_layout.addWidget(self.validation_status)
        layout.addWidget(info_card)
        layout.addStretch(1)
        return side

    def _build_summary(self) -> QWidget:
        summary = QFrame()
        summary.setObjectName("AdmissionSummaryBar")
        layout = QHBoxLayout(summary)
        layout.setContentsMargins(16, 9, 16, 9)
        layout.setSpacing(20)
        self.summary_total = QLabel("Atenciones: 0")
        self.summary_emergency = QLabel("Emergencias: 0")
        self.summary_consultation = QLabel("Consultas: 0")
        self.summary_uninsured = QLabel("Sin seguro: 0")
        for label in (
            self.summary_total,
            self.summary_emergency,
            self.summary_consultation,
            self.summary_uninsured,
        ):
            label.setObjectName("AdmissionSummaryValue")
            layout.addWidget(label)
        layout.addStretch(1)
        self.connection_status = QLabel("Conexión central compartida")
        self.connection_status.setObjectName("AdmissionConnectionStatus")
        layout.addWidget(self.connection_status)
        return summary

    def _build_bottom_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("AdmissionBottomBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        self.version_label = QLabel("Admisión PySide6")
        layout.addWidget(self.version_label)
        layout.addStretch(1)
        self.clear_button = QPushButton("Limpiar")
        self.clear_button.setObjectName("AdmissionSecondaryButton")
        self.register_button = QPushButton("Registrar e imprimir")
        self.register_button.setObjectName("AdmissionPrimaryButton")
        self.register_button.setText(self._default_register_text())
        self.register_button.setEnabled(bool(self.context.current_shift))
        self.register_button.setToolTip(
            "Registra la atención y aplica las preferencias documentales vigentes."
        )
        layout.addWidget(self.clear_button)
        layout.addWidget(self.register_button)
        return bar

    def _connect_architecture_signals(self):
        self.clear_button.clicked.connect(self.clear_form)
        self.history_button.clicked.connect(lambda: self.open_history(False))
        self.quick_action_buttons["uninsured"].clicked.connect(
            lambda: self.open_history(True)
        )
        self.menu_history_action.triggered.connect(lambda: self.open_history(False))
        self.menu_preferences_action.triggered.connect(self.open_preferences)
        self.menu_close_action.triggered.connect(self.hide)
        self.change_shift_button.clicked.connect(self._open_shift_dialog)
        self.register_button.clicked.connect(self._register_or_update)
        self.cedula_edit.returnPressed.connect(
            lambda: self._search_patient(self.cedula_edit.text())
        )
        self.nss_edit.returnPressed.connect(
            lambda: self._search_patient(self.nss_edit.text())
        )
        self.controller.operation_failed.connect(self._show_error)
        self.controller.search_completed.connect(self._search_finished)
        self.controller.attention_created.connect(self._registered)
        self.controller.attention_updated.connect(self._updated)
        self.controller.state_changed.connect(
            lambda state: self.validation_status.setText(str(getattr(state, "value", state)))
        )
        self.controller.shift_changed.connect(self._shift_changed)
        self.controller.shift_summary_loaded.connect(self._summary_changed)
        if self.controller.document_service is not None:
            self.quick_action_buttons["prints"].setEnabled(True)
            self.quick_action_buttons["prints"].setToolTip("Abrir impresiones pendientes")
            self.quick_action_buttons["prints"].clicked.connect(self.open_pending_prints)
        self._connect_administrative_actions()

    def _connect_administrative_actions(self):
        permissions = {
            "report": ("admission.reports", self.open_report),
            "excel": ("admission.excel", self.open_excel),
            "settings": ("admission.config", self.open_internal_config),
        }
        for key, (permission, slot) in permissions.items():
            button = self.quick_action_buttons[key]
            allowed = self.context.has_permission(permission)
            button.setEnabled(allowed)
            button.setToolTip("" if allowed else "Permiso insuficiente")
            if allowed:
                button.clicked.connect(slot)

    def _track_dialog(self, dialog):
        if hasattr(dialog, "apply_theme"):
            dialog.apply_theme(self._is_dark)
        self._history_dialogs.append(dialog)
        dialog.finished.connect(
            lambda _result, current=dialog: self._forget_history(current)
        )
        dialog.show()
        return dialog

    def open_report(self):
        return self._track_dialog(
            AdmissionReportDialog(self.controller.service.repository, self)
        )

    def open_preferences(self):
        return self._track_dialog(
            AdmissionPreferencesDialog(self.controller.service.repository, self)
        )

    def open_internal_config(self):
        return self._track_dialog(
            AdmissionInternalConfigDialog(
                self.controller.service.repository, self.context, self
            )
        )

    def open_excel(self):
        try:
            path = self.controller.service.repository.open_current_excel()
            self.validation_status.setText(f"Listado abierto: {Path(path).name}")
            return path
        except Exception as exc:
            self._show_error(str(exc))
            return ""

    def open_pending_prints(self):
        if self.controller.document_service is None:
            return None
        dialog = PendingPrintsDialog(self.controller.document_service, self)
        if hasattr(dialog, "apply_theme"):
            dialog.apply_theme(self._is_dark)
        self._history_dialogs.append(dialog)
        dialog.finished.connect(
            lambda _result, current=dialog: self._forget_history(current)
        )
        dialog.show()
        return dialog

    def open_history(self, uninsured=False):
        dialog = AdmissionHistoryDialog(
            self.controller, uninsured=bool(uninsured), parent=self
        )
        dialog.apply_theme(self._is_dark)
        dialog.edit_requested.connect(self._edit_from_history)
        dialog.open_sheet_requested.connect(self.controller.request_detail_sheet)
        dialog.finished.connect(
            lambda _result, current=dialog: self._forget_history(current)
        )
        self._history_dialogs.append(dialog)
        dialog.show()
        return dialog

    def _forget_history(self, dialog):
        if dialog in self._history_dialogs:
            self._history_dialogs.remove(dialog)

    def _edit_from_history(self, attention_id: int):
        record = self.controller.get_attention(attention_id)
        if record:
            self.load_attention(record, editing=True)
            self.validation_status.setText("Atención cargada para corregir.")

    def _configure_tab_order(self):
        ordered = [
            self.name_edit,
            self.age_spin,
            self.age_unit_combo,
            self.sex_combo,
            self.urgency_check,
            self.cedula_edit,
            self.phone_edit,
            self.address_edit,
            self.nationality_edit,
            self.ars_combo,
            self.nss_edit,
            self.service_type_combo,
            self.specialty_combo,
            self.clear_button,
        ]
        for current, following in zip(ordered, ordered[1:]):
            QWidget.setTabOrder(current, following)

    def clear_form(self):
        for edit in (
            self.name_edit,
            self.cedula_edit,
            self.phone_edit,
            self.address_edit,
            self.nationality_edit,
            self.nss_edit,
        ):
            edit.clear()
        self.age_spin.setValue(0)
        self.sex_combo.setCurrentIndex(0)
        self.age_unit_combo.setCurrentIndex(0)
        self.urgency_check.setChecked(False)
        if self.ars_combo.isEditable():
            self.ars_combo.setCurrentText("")
        self.name_edit.setFocus(Qt.OtherFocusReason)
        self.editing_attention_id = None
        self.register_button.setText(self._default_register_text())
        self.register_button.setEnabled(bool(self.context.current_shift))
        self.validation_status.setText("Formulario limpio")
        self.validation_status.setProperty("error", False)
        self.validation_status.style().unpolish(self.validation_status)
        self.validation_status.style().polish(self.validation_status)

    @staticmethod
    def _record_value(record, *names, default=""):
        if not isinstance(record, dict):
            try:
                record = dict(record)
            except Exception:
                record = vars(record) if hasattr(record, "__dict__") else {}
        normalized = {str(key).casefold(): value for key, value in record.items()}
        for name in names:
            if name in record:
                return record[name]
            if str(name).casefold() in normalized:
                return normalized[str(name).casefold()]
        return default

    def _search_patient(self, text: str):
        text = str(text or "").strip()
        if not text:
            self._show_error("Ingrese una cédula o NSS para buscar.")
            return
        self.validation_status.setText("Buscando paciente…")
        self.controller.search(text)

    def _search_finished(self, rows):
        rows = list(rows or [])
        if not rows:
            self.validation_status.setText("No se encontraron coincidencias.")
            return
        if len(rows) > 1:
            self.validation_status.setText(
                f"Se encontraron {len(rows)} coincidencias; refine la búsqueda."
            )
            return
        self.load_attention(rows[0], editing=False)
        self.validation_status.setText("Paciente cargado.")

    def load_attention(self, record, *, editing=True):
        self.name_edit.setText(str(self._record_value(record, "name", "nombre")))
        self.age_spin.setValue(int(self._record_value(record, "age", "edad_num", default=0) or 0))
        self.age_unit_combo.setCurrentText(
            str(self._record_value(record, "age_unit", "unidad", default="Años"))
        )
        self.sex_combo.setCurrentText(
            str(self._record_value(record, "sex", "sexo", default="Femenino"))
        )
        self.cedula_edit.setText(str(self._record_value(record, "cedula", "Cédula")))
        self.phone_edit.setText(str(self._record_value(record, "phone", "telefono", "Teléfono")))
        self.address_edit.setText(str(self._record_value(record, "address", "direccion", "Dirección")))
        self.nationality_edit.setText(
            str(self._record_value(record, "nationality", "nacionalidad", "Nacionalidad"))
        )
        self.ars_combo.setCurrentText(
            str(self._record_value(record, "ars", "Aseguradora (ARS)"))
        )
        self.nss_edit.setText(str(self._record_value(record, "nss", "NSS")))
        self.service_type_combo.setCurrentText(
            str(self._record_value(record, "attention_type", "tipo_atencion", "TipoAtencion", default="EMERGENCIA")).upper()
        )
        self.specialty_combo.setCurrentText(
            str(self._record_value(record, "specialty", "hoja", "Hoja", default="GENERAL")).upper()
        )
        attention_id = self._record_value(record, "attention_id", "id", default=0)
        self.editing_attention_id = int(attention_id or 0) if editing else None
        self.register_button.setText(
            "Guardar corrección" if self.editing_attention_id else self._default_register_text()
        )

    def _default_register_text(self):
        return (
            "Registrar e imprimir"
            if self.controller.document_service is not None
            else "Registrar atención"
        )

    def _form_payload(self) -> dict:
        return {
            "name": self.name_edit.text(),
            "age": self.age_spin.value(),
            "age_unit": self.age_unit_combo.currentText(),
            "sex": self.sex_combo.currentText(),
            "attention_type": self.service_type_combo.currentText(),
            "cedula": self.cedula_edit.text(),
            "phone": self.phone_edit.text(),
            "address": self.address_edit.text(),
            "nationality": self.nationality_edit.text(),
            "ars": self.ars_combo.currentText(),
            "nss": self.nss_edit.text(),
            "specialty": self.specialty_combo.currentText(),
            "metadata": {"urgency": self.urgency_check.isChecked()},
        }

    def _register_or_update(self):
        if self._operation_in_progress:
            return
        self._operation_in_progress = True
        self.register_button.setEnabled(False)
        try:
            if self.editing_attention_id:
                result = self.controller.update(
                    self.editing_attention_id, self._form_payload()
                )
            else:
                result = self.controller.register(self._form_payload())
            if not result.ok:
                self.register_button.setEnabled(bool(self.context.current_shift))
        finally:
            self._operation_in_progress = False

    def _registered(self, result):
        self.clear_form()
        self.validation_status.setText("Atención registrada correctamente.")

    def _updated(self, result):
        self.validation_status.setText("Atención actualizada correctamente.")
        self.register_button.setEnabled(True)

    def _shift_changed(self, shift):
        self.shift_label.setText(self._shift_text())
        self.representative_value.setText(
            str((shift or {}).get("representative") or "Sin asignar")
        )
        self.register_button.setEnabled(bool(shift))
        self.controller.refresh_shift_summary()

    def _summary_changed(self, summary):
        summary = dict(summary or {})
        self.summary_total.setText(f"Atenciones: {int(summary.get('total', summary.get('total_general', 0)) or 0)}")
        self.summary_emergency.setText(f"Emergencias: {int(summary.get('emergencies', summary.get('URGENCIAS', 0)) or 0)}")
        self.summary_consultation.setText(f"Consultas: {int(summary.get('consultations', summary.get('CONSULTAS', 0)) or 0)}")
        self.summary_uninsured.setText(f"Sin seguro: {int(summary.get('uninsured', summary.get('sin_seguro', 0)) or 0)}")

    def _open_shift_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Cambiar turno")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "Selecciona el turno y su representante. El cierre documental del "
            "turno anterior requiere confirmación separada."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QGridLayout()
        shift_type = QComboBox()
        shift_type.addItems(["8AM_8AM", "8AM_8PM", "8PM_8AM"])
        representative = QComboBox()
        representative.setEditable(True)
        try:
            representative.addItems(
                [str(item) for item in self.controller.service.representatives()]
            )
        except Exception:
            pass
        form.addWidget(QLabel("Turno:"), 0, 0)
        form.addWidget(shift_type, 0, 1)
        form.addWidget(QLabel("Representante:"), 1, 0)
        form.addWidget(representative, 1, 1)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Aplicar turno")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        value = {
            "turno_codigo": shift_type.currentText(),
            "tipo_turno": shift_type.currentText(),
            "representative": representative.currentText().strip(),
            "representante": representative.currentText().strip(),
        }
        result = (
            self.controller.change_shift(value)
            if self.context.current_shift
            else self.controller.open_shift(value)
        )
        if result.ok:
            self.validation_status.setText("Turno actualizado.")

    def _show_error(self, message: str):
        self.validation_status.setText(message or "Error")
        self.validation_status.setProperty("error", True)
        self.validation_status.style().unpolish(self.validation_status)
        self.validation_status.style().polish(self.validation_status)

    def apply_layout_profile(self, snapshot):
        profile = str(getattr(snapshot, "applied_profile", "ESTANDAR") or "ESTANDAR")
        density = str(getattr(snapshot, "density", "NORMAL") or "NORMAL")
        text_percent = max(85, min(int(getattr(snapshot, "text_percent", 100) or 100), 125))
        self._layout_profile = profile
        very_compact = profile == "MUY_COMPACTO"
        compact = very_compact or profile == "COMPACTO" or density in {"MUY_COMPACTA", "COMPACTA"}
        margin = 6 if very_compact else 9 if compact else 14
        spacing = 6 if very_compact else 8 if compact else 12
        root = self.layout()
        if root is not None:
            root.setContentsMargins(margin, margin, margin, margin)
            root.setSpacing(spacing)
        self.content_splitter.setSizes(
            [1180, 360] if profile == "AMPLIO" else [1030, 410] if not compact else [900, 360]
        )
        side = self.content_splitter.widget(1)
        side.setMinimumWidth(280 if compact else 300)
        side.setMaximumWidth(430 if compact else 520)
        one_action_column = very_compact and self.width() < 1180
        for button in self.quick_action_buttons.values():
            self.actions_grid.removeWidget(button)
        for index, (_text, key) in enumerate(self.QUICK_ACTIONS):
            self.actions_grid.addWidget(
                self.quick_action_buttons[key],
                index if one_action_column else index // 2,
                0 if one_action_column else index % 2,
            )
        for group in self.findChildren(QGroupBox):
            if group.layout() is not None:
                card_margin = 10 if very_compact else 13 if compact else 18
                group.layout().setContentsMargins(
                    card_margin, card_margin, card_margin, max(9, card_margin - 2)
                )
                group.layout().setSpacing(7 if compact else 12)
        font = self.font()
        font.setPointSizeF(max(8.0, 10.0 * text_percent / 100.0))
        self.setFont(font)
        self._apply_phase3_style()

    def apply_theme(self, is_dark: bool):
        self._is_dark = bool(is_dark)
        self._apply_phase3_style()
        for dialog in tuple(self._history_dialogs):
            if hasattr(dialog, "apply_theme"):
                dialog.apply_theme(self._is_dark)

    def shutdown(self):
        """Cierra solo recursos hijos; nunca finaliza QApplication ni sesión."""
        for dialog in tuple(self._history_dialogs):
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
        self._history_dialogs.clear()

    def _apply_phase3_style(self):
        dark = self._is_dark
        bg = "#111827" if dark else "#F2F6FB"
        card = "#182235" if dark else "#FFFFFF"
        text = "#E8EEF8" if dark else "#13233F"
        muted = "#B8C6DA" if dark else "#405570"
        border = "#34445E" if dark else "#CEDAE9"
        input_bg = "#101827" if dark else "#FFFFFF"
        secondary = "#24324A" if dark else "#FFFFFF"
        secondary_hover = "#304360" if dark else "#EAF2FF"
        summary = "#202C40" if dark else "#E8EFF8"
        success_bg = "#16382F" if dark else "#E8F4EE"
        success_text = "#72D7AA" if dark else "#12623E"
        success_border = "#2C6B58" if dark else "#BDE1CF"
        error_bg = "#482327" if dark else "#FDE9E9"
        error_text = "#FF9D9D" if dark else "#A32121"
        error_border = "#774047" if dark else "#EABBBB"
        representative_bg = "#1D3454" if dark else "#EEF5FF"
        representative_text = "#8EC1FF" if dark else "#1459B8"
        representative_border = "#345A87" if dark else "#C8DCF8"
        font_size = "9pt" if self._layout_profile == "MUY_COMPACTO" else "9.5pt" if self._layout_profile == "COMPACTO" else "10.5pt"
        self.setStyleSheet(
            f"""
            QWidget#AdmissionWidget {{ background: {bg}; color: {text}; font: {font_size} 'Segoe UI'; }}
            QFrame#AdmissionHeader {{ background: #102B62; border: 1px solid #31548C; border-radius: 12px; }}
            QLabel#AdmissionTitle {{ color: white; font-size: 19pt; font-weight: 800; }}
            QLabel#AdmissionSubtitle {{ color: #C9D8F3; font-size: 10pt; }}
            QLabel#AdmissionHeaderCaption {{ color: #AFC5EB; font-size: 8.5pt; font-weight: 700; }}
            QLabel#AdmissionHeaderValue {{ color: white; font-size: 10.5pt; font-weight: 700; }}
            QPushButton#AdmissionSecondaryButton, QToolButton#AdmissionMenuButton, QPushButton#AdmissionInfoButton {{
                background: {secondary}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 8px 13px;
            }}
            QPushButton#AdmissionSecondaryButton:hover, QToolButton#AdmissionMenuButton:hover, QPushButton#AdmissionInfoButton:hover {{ background: {secondary_hover}; border-color: #3C7BEA; }}
            QGroupBox {{ background: {card}; border: 1px solid {border}; border-radius: 12px; margin-top: 13px; font-weight: 800; color: {text}; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 15px; padding: 0 7px; }}
            QLabel#AdmissionFieldLabel {{ color: {muted}; font-weight: 700; }}
            QLineEdit, QComboBox, QSpinBox {{ min-height: 32px; background: {input_bg}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 2px 9px; }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid #1F6FEB; }}
            QCheckBox {{ spacing: 8px; color: {text}; font-weight: 600; }}
            QPushButton#AdmissionQuickAction {{ text-align: left; background: {secondary}; color: #4B91F7; border: 1px solid {border}; border-radius: 8px; padding: 8px 11px; font-weight: 700; }}
            QPushButton#AdmissionQuickAction:disabled {{ color: #7C8CA3; background: {bg}; border-color: {border}; }}
            QLabel#AdmissionInfoText {{ color: {muted}; line-height: 1.3; }}
            QLabel#AdmissionStatusPill {{ background: {success_bg}; color: {success_text}; border: 1px solid {success_border}; border-radius: 8px; padding: 8px; font-weight: 700; }}
            QLabel#AdmissionStatusPill[error="true"] {{ background: {error_bg}; color: {error_text}; border-color: {error_border}; }}
            QLabel#AdmissionRepresentativeValue {{ min-height: 32px; background: {representative_bg}; color: {representative_text}; border: 1px solid {representative_border}; border-radius: 7px; padding: 2px 10px; font-weight: 700; }}
            QFrame#AdmissionSummaryBar {{ background: {card}; border: 1px solid {border}; border-radius: 10px; }}
            QLabel#AdmissionSummaryValue {{ color: {text}; font-weight: 750; }}
            QLabel#AdmissionConnectionStatus {{ color: #08794C; font-weight: 700; }}
            QFrame#AdmissionBottomBar {{ background: {summary}; border: 1px solid {border}; border-radius: 10px; }}
            QPushButton#AdmissionPrimaryButton {{ background: #1469D8; color: white; border: 1px solid #0D5CBE; border-radius: 8px; padding: 9px 18px; font-weight: 800; }}
            QPushButton#AdmissionPrimaryButton:disabled {{ background: #AEBFD3; color: #E8EEF5; border-color: #AEBFD3; }}
            QSplitter::handle {{ background: transparent; width: 5px; }}
            """
        )


class AdmissionStandaloneWindow(QMainWindow):
    """Wrapper exclusivo de pruebas; reutiliza la QApplication existente."""

    def __init__(self, context: AppContext, controller: AdmissionController, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Admisión PySide6 — Prueba")
        self.setCentralWidget(AdmissionWidget(context, controller, self))
        self.resize(1600, 900)
