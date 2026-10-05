"""Catalog and receipt presentation; existing model values/actions stay intact."""

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app_icons import APP_ICONS


CATEGORY_COLORS = {
    "Medicamentos": ("#67c8ff", "#103d60"),
    "Materiales": ("#ffc068", "#513712"),
    "Laboratorios": ("#ff7bb3", "#4f1837"),
    "Imágenes": ("#e9a0ff", "#442259"),
    "Procedimientos": ("#69e2d9", "#114744"),
    "Honorarios": ("#dfa1ff", "#422052"),
}


def items_palette(dark):
    return (
        ("#0b1d2d", "#10283e", "#edf5ff", "#9bb5ce", "#25435b")
        if dark
        else ("#ffffff", "#e9f1fa", "#17324d", "#526b84", "#c1d3e4")
    )


def items_styles(dark):
    panel, heading, text, muted, border = items_palette(dark)
    return f"""
    QWidget#BillingCatalogCard, QWidget#BillingReceiptCard {{
        background: {panel}; border: 1px solid {border}; border-radius: 9px;
    }}
    QGroupBox#BillingItemsWorkspace, QGroupBox#BillingCartContents {{
        border: none; margin: 0; padding: 0; background: transparent;
    }}
    QSplitter#ReceiptCatalogSplitter::handle {{ background: transparent; }}
    QLabel#BillingItemsHeading {{ color: {text}; font-size: 12pt;
        font-weight: 700; border: none; background: transparent; padding: 4px 0;
    }}
    QLabel#BillingItemsCaption {{ color: {muted}; font-size: 8pt;
        border: none; background: transparent; padding-bottom: 5px;
    }}
    QLabel#BillingItemCount {{ color: #60c5ff; background: #11395a;
        border: 1px solid #226396; border-radius: 6px; padding: 4px 8px;
    }}
    QWidget#CatalogColumnHeadings {{ background: {heading};
        border-bottom: 1px solid {border}; min-height: 30px;
    }}
    QWidget#CatalogColumnHeadings QLabel {{ color: {text};
        background: transparent; border: none; font-size: 8pt; font-weight: 700;
    }}
    QWidget#DesignCatalogRow {{ background: {panel};
        border-bottom: 1px solid {border}; border-radius: 0;
    }}
    QWidget#DesignCatalogRow[selected="true"] {{ background: #15476b;
        border: 1px solid #2596e8; border-radius: 5px;
    }}
    QWidget#DesignCatalogRow QLabel {{ background: transparent; border: none;
        color: {text}; padding: 0; font-size: 9pt;
    }}
    QWidget#DesignCatalogRow[selected="true"] QLabel {{ color: #edf5ff; }}
    QLabel#CatalogItemName {{ font-weight: 600; }}
    QWidget#DesignCatalogRow QLabel#CatalogPrice {{ color: #29afff; font-weight: 700; }}
    QPushButton#DesignAdd {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #1499ff,stop:1 #0877d8); color: white; min-height: 25px;
        padding: 0; border: 1px solid #3da9ff; border-radius: 5px; font-weight: 600;
    }}
    QPushButton#DesignAdd:hover {{ background: #259dff; }}
    QToolButton#DesignStar {{ color: #efb32c; border: none;
        background: transparent; padding: 0; font-size: 17pt;
    }}
    QWidget#BillingSummaryCard {{ background: {panel}; border: 1px solid {border};
        border-radius: 8px;
    }}
    QLabel#BillingMedicationSubtotal {{ background: #103955; color: #bce6ff;
        border: 1px solid #216591; border-radius: 7px; padding: 9px; font-weight: 600;
    }}
    QLabel#BillingMaterialSubtotal {{ background: #3b2a18; color: #ffca83;
        border: 1px solid #795020; border-radius: 7px; padding: 9px; font-weight: 600;
    }}
    QLabel#DesignTotal {{ background: #073f32; color: #37e79f;
        border: 1px solid #18775a; border-radius: 7px; padding: 9px; font-weight: 800;
    }}
    """


def catalog_list_styles(dark):
    panel, _heading, text, _muted, _border = items_palette(dark)
    return f"""
    QListWidget {{ background: {panel}; color: {text}; border: none; padding: 0; }}
    QListWidget::item {{ padding: 0; margin: 0; border: none; background: transparent; }}
    QListWidget::item:selected {{ background: transparent; border: none; }}
    """


def category_tabs_styles(dark):
    _panel, heading, text, _muted, border = items_palette(dark)
    return (
        f"QTabBar::tab {{ padding:7px 5px; min-width:0; font-size:8pt; "
        f"border:1px solid {border}; border-radius:5px; background:{heading}; color:{text}; }}"
        "QTabBar::tab:selected { background:#087ee6; color:white; border-color:#31a7ff; }"
    )


def receipt_table_styles(dark):
    panel, heading, text, _muted, border = items_palette(dark)
    return f"""
    QTableWidget {{ background: {panel}; alternate-background-color: {panel};
        color: {text}; border: 1px solid {border}; border-radius: 7px;
        gridline-color: {border}; selection-background-color: #165b90;
    }}
    QTableWidget::item {{ padding: 6px; border: none; }}
    QHeaderView::section {{ background: {heading}; color: {text};
        border: none; border-right: 1px solid {border};
        border-bottom: 1px solid {border}; padding: 9px 3px;
        font-size: 8pt; font-weight: 700;
    }}
    QTableCornerButton::section {{ background: {heading}; border: none; }}
    QWidget#CartQuantityHolder, QWidget#CartDeleteHolder {{ background: transparent; }}
    QSpinBox#CartQuantitySpin {{ background: {heading}; color: {text};
        border: 1px solid {border}; border-radius: 0; padding: 0; min-height: 0;
    }}
    QToolButton#CartQuantityUp, QToolButton#CartQuantityDown {{
        background: {heading}; color: {text}; border: 1px solid {border};
        border-radius: 3px; padding: 0; font-size: 11pt;
    }}
    QToolButton#CartQuantityUp:hover, QToolButton#CartQuantityDown:hover {{
        background: #216297; color: white;
    }}
    QToolButton#CartDeleteButton {{ background: #492331;
        border: 1px solid #693244; border-radius: 5px; padding: 0;
    }}
    QToolButton#CartDeleteButton:hover {{ background: #78303d; }}
    """


class ReceiptPresentationDelegate(QStyledItemDelegate):
    def __init__(self, table):
        super().__init__(table)
        self.dark = True

    def paint(self, painter, option, index):
        if index.column() == 0:
            self.paint_category(painter, option, str(index.data() or ""))
            return
        clean = QStyleOptionViewItem(option)
        self.initStyleOption(clean, index)
        if index.column() == 2:
            clean.text = ""
        elif index.column() in (3, 4):
            clean.text = clean.text.replace("$", "RD$ ", 1)
            clean.font.setBold(index.column() == 4)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, clean, painter, option.widget)

    def paint_category(self, painter, option, raw):
        category = next((name for name in CATEGORY_COLORS if name in raw), raw.strip())
        foreground, background = CATEGORY_COLORS.get(category, ("#b9cbdd", "#20384d"))
        if not self.dark:
            foreground, background = "#17324d", "#dfedf9"
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = QRectF(option.rect.adjusted(5, 0, -5, 0))
        rect.setTop(rect.center().y() - 12)
        rect.setHeight(24)
        painter.setPen(QColor(foreground))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 11, 11)
        font = option.font
        font.setPointSizeF(8)
        painter.setFont(font)
        text = painter.fontMetrics().elidedText(
            category, Qt.ElideRight, int(rect.width()) - 8
        )
        painter.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignCenter, text)
        painter.restore()


def present_quantity_holder(holder):
    editor = holder.findChild(QWidget, "CartQuantitySpin")
    if editor is None:
        return
    layout = holder.layout()
    down = holder.findChild(QToolButton, "CartQuantityDown")
    up = holder.findChild(QToolButton, "CartQuantityUp")
    if down is None or up is None or layout is None:
        return
    if not holder.property("horizontalQuantity"):
        while layout.count():
            layout.takeAt(0)
        layout.addStretch(1)
        layout.addWidget(down)
        layout.addWidget(editor)
        layout.addWidget(up)
        layout.addStretch(1)
        arrow_column = holder.findChild(QWidget, "CartQuantityArrowColumn")
        if arrow_column is not None:
            arrow_column.hide()
        holder.setProperty("horizontalQuantity", True)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(0)
    editor.setFixedSize(32, 30)
    for button, symbol in ((down, "−"), (up, "+")):
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setText(symbol)
        button.setFixedSize(24, 30)


class BillingItemsPresentation(QObject):
    def __init__(self, window):
        super().__init__(window.billing_workspace)
        self.window = window
        self.dark = True
        self.pending_resize = False
        self.decorate_cards()
        self.delegate = ReceiptPresentationDelegate(window.cart_table)
        window.cart_table.setItemDelegate(self.delegate)
        window.cart_table.setWordWrap(True)
        window.cart_table.viewport().installEventFilter(self)
        window.cart_table.horizontalHeader().setMinimumSectionSize(30)
        window.cart_table.verticalHeader().setVisible(True)
        window.cart_table.verticalHeader().setFixedWidth(26)
        window.cart_table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)

    def heading(self, text, parent, icon_key="billing_receipts"):
        heading = QWidget(parent)
        layout = QHBoxLayout(heading)
        layout.setContentsMargins(0, 0, 0, 6)
        icon = QLabel(heading)
        icon.setPixmap(
            APP_ICONS.icon(icon_key, size=24, color="#35b4ff").pixmap(24, 24)
        )
        icon.setFixedSize(24, 24)
        layout.addWidget(icon)
        label = QLabel(text, heading)
        label.setObjectName("BillingItemsHeading")
        layout.addWidget(label, 1)
        return heading

    def decorate_cards(self):
        window = self.window
        window.receipt_catalog_group.setObjectName("BillingItemsWorkspace")
        window.catalog_panel.setObjectName("BillingCatalogCard")
        window.receipt_panel.setObjectName("BillingReceiptCard")
        window.catalog_layout.setContentsMargins(12, 10, 12, 10)
        window.receipt_layout.setContentsMargins(12, 10, 12, 10)
        catalog_title = window.catalog_layout.takeAt(0).widget()
        catalog_title.hide()
        catalog_title.deleteLater()
        window.catalog_layout.insertWidget(
            0,
            self.heading("Catálogo de ítems", window.catalog_panel, "billing_catalog"),
        )
        caption = QLabel("Seleccione medicamentos, materiales o servicios")
        caption.setObjectName("BillingItemsCaption")
        window.catalog_layout.insertWidget(1, caption)
        cart_group = window.cart_table.parentWidget()
        cart_group.setTitle("")
        cart_group.setObjectName("BillingCartContents")
        cart_group.layout().setContentsMargins(0, 0, 0, 0)
        self.receipt_heading = self.heading(
            "Recibo de facturación", window.receipt_panel
        )
        window.receipt_layout.removeWidget(window.cart_count)
        window.cart_count.setObjectName("BillingItemCount")
        self.receipt_heading.layout().addWidget(window.cart_count)
        window.receipt_layout.insertWidget(0, self.receipt_heading)
        window.cart_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        window.receipt_layout.setStretch(window.receipt_layout.indexOf(cart_group), 1)
        self.install_summary()

    def install_summary(self):
        window = self.window
        self.summary_card = QWidget(window.receipt_panel)
        self.summary_card.setObjectName("BillingSummaryCard")
        layout = QVBoxLayout(self.summary_card)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.addWidget(
            self.heading("Resumen de facturación", self.summary_card, "billing_ars")
        )
        old_grid = window.cart_summary_layout
        for label in (
            window.lbl_sub_medicamentos,
            window.lbl_sub_materiales,
            window.lbl_total,
        ):
            old_grid.removeWidget(label)
        old_grid.parent().removeItem(old_grid)
        old_grid.deleteLater()
        window.cart_summary_layout = QGridLayout()
        window.cart_summary_layout.setSpacing(8)
        layout.addLayout(window.cart_summary_layout)
        window.lbl_sub_medicamentos.setObjectName("BillingMedicationSubtotal")
        window.lbl_sub_materiales.setObjectName("BillingMaterialSubtotal")
        window.receipt_layout.addWidget(self.summary_card)

    def apply_theme(self, dark):
        self.dark = dark
        window = self.window
        window.receipt_catalog_group.setStyleSheet(items_styles(dark))
        for widget in window.source_lists.values():
            widget.setStyleSheet(catalog_list_styles(dark))
        window.tabs.setStyleSheet(category_tabs_styles(dark))
        window.cart_table.setStyleSheet(receipt_table_styles(dark))
        self.delegate.dark = dark
        window.cart_table.viewport().update()

    def fit_catalog(self):
        tabs = self.window.tabs
        tabs.setFont(QFont("Segoe UI", 8))
        tabs.setElideMode(
            Qt.ElideNone if self.window.catalog_panel.width() >= 430 else Qt.ElideRight
        )
        for index in range(tabs.count()):
            name = tabs.tabToolTip(index)
            if name:
                tabs.setTabText(index, name)
        tabs.setStyleSheet(category_tabs_styles(self.dark))

    def refresh_rows(self):
        table = self.window.cart_table
        for row in range(table.rowCount()):
            holder = table.cellWidget(row, 2)
            if holder is not None:
                present_quantity_holder(holder)
        self.window.cart_count.setText(f"{table.rowCount()} ítems")

    def fit(self):
        self.pending_resize = False
        table = self.window.cart_table
        narrow = table.viewport().width() < 610
        widths = (
            {0: 96, 2: 84, 3: 80, 4: 90, 5: 48}
            if narrow
            else {0: 112, 2: 92, 3: 96, 4: 104, 5: 58}
        )
        header = table.horizontalHeader()
        table.setFont(QFont("Segoe UI", 8 if narrow else 9))
        header.moveSection(header.visualIndex(1), 0)
        table.setColumnWidth(
            1, max(30, table.viewport().width() - sum(widths.values()))
        )
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in widths.items():
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            table.setColumnWidth(column, width)
        header.resizeSections()
        table.verticalHeader().setMinimumSectionSize(52)
        table.verticalHeader().setDefaultSectionSize(52)
        for row in range(table.rowCount()):
            table.setRowHeight(row, 52)
        self.refresh_rows()
        self.fit_catalog()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show) and not self.pending_resize:
            self.pending_resize = True
            QTimer.singleShot(0, self.fit)
        return False
