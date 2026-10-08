"""Presentation and visible patient identity in the existing billing form."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
    QSizePolicy,
    QPushButton,
    QToolButton,
)

from catalog_workspace_design import CatalogWorkspace
from billing_items_design import BillingItemsPresentation
from workspace_design import title, workspace_styles
from workspace_accents import style_action


def billing_identity_nss(window):
    attention = dict(window.current_admission_attention or {})
    return str(
        getattr(window, "receipt_identity_nss", "")
        or attention.get("nss_clean")
        or attention.get("nss")
        or attention.get("nss_snapshot")
        or attention.get("admission_nss_snapshot")
        or ""
    )


def install_billing_design(window, backend):
    window.billing_workspace.setObjectName("DesignSurface")
    window.billing_workspace.setFont(QFont("Segoe UI", 9))
    window.nav_widget.setFont(QFont("Segoe UI", 9))
    window.billing_group.setObjectName("DesignPatient")
    window.billing_group.setTitle("Datos del paciente")
    window.nss_display = QLineEdit()
    window.nss_display.setReadOnly(True)
    window.nss_display.setPlaceholderText("No registrado")
    window.nss_display.setToolTip("NSS del recibo o de la atención vinculada")
    window.billing_layout.insertRow(1, "NSS:", window.nss_display)
    window.catalog_workspace = CatalogWorkspace(window, backend)
    window.billing_layout.setVerticalSpacing(4)
    window.receipt_catalog_group.setTitle("")
    window.catalog_panel.setObjectName("DesignCard")
    window.catalog_layout.setContentsMargins(12, 10, 12, 10)
    window.catalog_layout.insertWidget(0, title("Catálogo de ítems"))
    window.catalog_layout.insertWidget(1, window.tabs)
    window.tabs.setUsesScrollButtons(True)
    window.tabs.setElideMode(Qt.ElideRight)
    window.cart_table.parentWidget().setTitle("Recibo de facturación")
    window.cart_table.parentWidget().setObjectName("DesignPatient")
    window.cart_count = QLabel("0 ítems")
    window.receipt_layout.insertWidget(0, window.cart_count)
    window.lbl_total.setObjectName("DesignTotal")
    for label in (window.lbl_sub_medicamentos, window.lbl_sub_materiales):
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    _install_patient_summary(window)
    window.items_presentation = BillingItemsPresentation(window)
    _fit_cart_summary(window, window.width() < 1600)
    window.theme_toggled.connect(lambda dark: apply_billing_theme(window, dark))
    apply_billing_theme(window, window.is_dark_mode)
    for control in (
        window.name_edit,
        window.authorization_edit,
        window.ars_combo,
        window.coverage_combo,
        window.sala_spin,
    ):
        signal = (
            control.textChanged
            if isinstance(control, QLineEdit)
            else (
                control.currentTextChanged
                if hasattr(control, "currentTextChanged")
                else control.valueChanged
            )
        )
        signal.connect(lambda _value: update_billing_summary(window))
    window.date_edit.dateChanged.connect(lambda _date: update_billing_summary(window))
    update_billing_summary(window)


def _install_patient_summary(window):
    summary = QWidget()
    summary.setObjectName("DesignCard")
    form = QFormLayout(summary)
    form.setContentsMargins(10, 8, 10, 8)
    form.addRow(title("Resumen del paciente"))
    window.patient_summary_labels = {}
    for key in ("ARS", "Cobertura", "NSS", "Autorización", "Sala", "Fecha"):
        label = QLabel()
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        window.patient_summary_labels[key] = label
        form.addRow(key, label)
    layout = window.billing_group.layout()
    layout.insertWidget(layout.count() - 1, summary)


def apply_billing_theme(window, dark):
    window.billing_workspace.setStyleSheet(workspace_styles(dark))
    window.header_widget.setStyleSheet(
        "QWidget#HeaderWidget {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #0a1e34,stop:1 #143c61);} QWidget#HeaderWidget QLabel {color:white;}"
    )
    apply_billing_action_accents(window, dark)
    window.items_presentation.apply_theme(dark)


def apply_billing_action_accents(window, dark):
    _apply_navigation_accents(window, dark)
    style_action(window.btn_add, "green", dark)
    if not hasattr(window, "catalog_workspace"):
        return
    for button in (window.catalog_workspace.previous, window.catalog_workspace.next):
        style_action(button, "blue", dark, compact=True)
    for button in window.catalog_panel.findChildren(QToolButton, "SpinArrowBtn"):
        style_action(button, "blue", dark, filled=False, compact=True)


def _apply_navigation_accents(window, dark):
    background, border = ("#0a1d30", "#25435f") if dark else ("#f4f8fd", "#c6d6e7")
    window.nav_widget.setStyleSheet(
        f"QWidget#NavWidget {{background:{background};border-right:1px solid {border};}}"
        "QWidget#NavWidget QPushButton {text-align:left;font-size:9pt;}"
    )
    roles = {
        "btn_add_catalog_item": "green",
        "btn_receipts_history": "blue",
        "btn_self_pay_history": "green",
        "btn_audit_workspace": "purple",
        "btn_view_reports": "purple",
        "btn_ars_mgmt": "blue",
        "btn_import_meds": "amber",
        "btn_admin_catalog": "blue",
    }
    tones = {getattr(window, name, None): tone for name, tone in roles.items()}
    for button in window.nav_widget.findChildren(QPushButton):
        filled = button in (
            window.btn_add_catalog_item,
            window.btn_import_meds,
            getattr(window, "btn_receipts_history", None),
        )
        style_action(button, tones.get(button, "blue"), dark, filled=filled)
        button.setStyleSheet(
            button.styleSheet()
            + "QPushButton {text-align:left;font-size:8.5pt;padding:8px 6px;}"
        )


def fit_billing_design(window):
    """Fit the existing splitters without depending on saved wide-screen ratios."""
    if not hasattr(window, "catalog_workspace"):
        return
    compact = window.width() < 1600
    window.tabs.setUsesScrollButtons(True)
    window.tabs.setElideMode(Qt.ElideRight)
    window.header_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    window.header_subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    window.catalog_workspace.sort.setMaximumWidth(140 if compact else 190)
    window.cart_table.parentWidget().setMinimumWidth(0)
    small = window.width() < 1450
    window.catalog_panel.setMinimumWidth(360 if small else 350)
    window.receipt_panel.setMinimumWidth(410 if small else 580)
    if compact:
        _fit_compact_billing(window)
    else:
        window.nav_widget.setFixedWidth(188)
        window.billing_group.setMinimumWidth(260)
        window.patient_scroll.setMinimumWidth(285)
        window.patient_scroll.setMaximumWidth(410)
    _fit_cart_summary(window, compact)
    available = window.main_split.width()
    catalog_width = int(available * 0.48)
    window.main_split.setHandleWidth(10)
    window.main_split.setSizes([catalog_width, available - catalog_width])
    window.items_presentation.fit()
    window.cart_table.setMinimumWidth(0)
    window.lbl_total.setMinimumWidth(0)
    window.lbl_total.setWordWrap(True)
    window.catalog_workspace.counter.setWordWrap(True)
    _fit_catalog_actions(window, compact)
    from billing_responsive import fit_billing_navigation

    fit_billing_navigation(window)


def _fit_catalog_actions(window, compact):
    window.btn_add.setText("Añadir" if compact else "Añadir al recibo (Enter)")
    window.btn_add.setToolTip("Añadir el ítem seleccionado con la cantidad indicada")


def _fit_compact_billing(window):
    small = window.width() < 1450
    patient_width = 240 if small else 285
    window.billing_group.setMinimumWidth(patient_width - 20)
    window.patient_scroll.setMinimumWidth(patient_width)
    window.patient_scroll.setMaximumWidth(patient_width if small else 315)
    window.nav_widget.setFixedWidth(110 if small else 158)
    window.header_title.setStyleSheet("font-size:17pt;font-weight:800;")
    window.lbl_user_top.setMaximumWidth(220)
    window.lbl_user_top.setWordWrap(False)
    full_name = window.current_user.get("full_name", "")
    window.lbl_user_top.setText(
        full_name if len(full_name) <= 24 else full_name[:23] + "…"
    )
    window.lbl_user_top.setToolTip(full_name)
    for control in (
        window.lbl_edit_mode,
        window.btn_cancel_edit,
        window.btn_reset,
        window.btn_generate,
    ):
        window.bottom_layout.removeWidget(control)
    window.lbl_edit_mode.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    window.lbl_edit_mode.setWordWrap(True)
    editing = bool(window.lbl_edit_mode.text().strip())
    if editing:
        window.bottom_layout.addWidget(window.lbl_edit_mode, 0, 0, 1, 5)
    row = int(editing)
    window.bottom_layout.addWidget(window.btn_cancel_edit, row, 0)
    window.bottom_layout.addWidget(window.btn_reset, row, 1)
    window.bottom_layout.addWidget(window.btn_generate, row, 2, 1, 3)
    window.bottom_widget.setMinimumHeight(76 if editing else 48)


def _fit_cart_summary(window, compact):
    for label in (
        window.lbl_sub_medicamentos,
        window.lbl_sub_materiales,
        window.lbl_total,
    ):
        window.cart_summary_layout.removeWidget(label)
    window.cart_summary_layout.addWidget(window.lbl_sub_medicamentos, 0, 0)
    window.cart_summary_layout.addWidget(window.lbl_sub_materiales, 0, 1)
    window.cart_summary_layout.setColumnStretch(0, 1)
    window.cart_summary_layout.setColumnStretch(1, 1)
    if window.receipt_panel.width() >= 650:
        window.cart_summary_layout.addWidget(window.lbl_total, 0, 2)
        window.cart_summary_layout.setColumnStretch(2, 2)
    else:
        window.cart_summary_layout.addWidget(window.lbl_total, 1, 0, 1, 2)
        window.cart_summary_layout.setColumnStretch(2, 0)
    window.lbl_total.setAlignment(Qt.AlignCenter)
    window.lbl_total.setMinimumHeight(54 if compact else 78)
    window.lbl_total.setStyleSheet(
        "font-size:15pt;padding:6px;" if compact else "font-size:18pt;padding:12px;"
    )
    for label in (window.lbl_sub_medicamentos, window.lbl_sub_materiales):
        label.setStyleSheet("font-size:10pt;font-weight:700;padding:2px;")
        label.setMinimumHeight(24)


def update_billing_summary(window):
    if not hasattr(window, "patient_summary_labels"):
        return
    nss = billing_identity_nss(window)
    window.nss_display.setText(nss)
    values = {
        "ARS": window.ars_combo.currentText(),
        "Cobertura": window.coverage_combo.currentText(),
        "NSS": nss or "No registrado",
        "Autorización": window.authorization_edit.text() or "Pendiente",
        "Sala": f"RD$ {window.sala_spin.value():,.2f}",
        "Fecha": window.date_edit.date().toString("dd-MM-yyyy"),
    }
    for key, value in values.items():
        window.patient_summary_labels[key].setText(value)
    window.cart_count.setText(f"{window.cart_table.rowCount()} ítems en el recibo")
    window.items_presentation.refresh_rows()
    for row in range(window.cart_table.rowCount()):
        for column in (0, 1):
            item = window.cart_table.item(row, column)
            if item:
                item.setToolTip(item.text())
