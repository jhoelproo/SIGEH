"""Local catalog pagination and native row actions; no additional queries."""

from math import ceil

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
    QSizePolicy,
    QGridLayout,
    QStyledItemDelegate,
    QFrame,
)


class IntegratedCatalogDelegate(QStyledItemDelegate):
    """Rows are drawn exclusively by their native index widgets."""

    def paint(self, painter, option, index):
        return


class CatalogNameLabel(QLabel):
    """Keep long item names inside their column, with the full name on hover."""

    def __init__(self, name):
        super().__init__()
        self.full_name = str(name)
        self.setTextFormat(Qt.PlainText)
        self.setToolTip(self.full_name)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(self.full_name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(
            self.fontMetrics().elidedText(self.full_name, Qt.ElideRight, self.width())
        )


class CatalogRow(QWidget):
    def __init__(self, item):
        super().__init__()
        self.item = item
        self.compact = None
        self.controls = ()
        self.setObjectName("DesignCatalogRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(8, 2, 6, 2)
        self.grid.setSpacing(4)

    def set_controls(self, *controls):
        self.controls = controls
        self.fit(self.width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit(event.size().width())

    def fit(self, width):
        compact = width < 340
        if not self.controls or compact == self.compact:
            return
        self.compact = compact
        for control in self.controls:
            self.grid.removeWidget(control)
        label, price, star, add = self.controls
        self.grid.addWidget(label, 0, 0, 1, 4 if compact else 1)
        for column, control in enumerate((price, star, add), 1):
            self.grid.addWidget(control, 1 if compact else 0, column)
        self.grid.setColumnStretch(0, 1)
        height = 64 if compact else 44
        self.setMinimumHeight(height)
        self.item.setSizeHint(QSize(0, height))


class CatalogWorkspace:
    def __init__(self, window, backend):
        self.window = window
        self.backend = backend
        self.page = 0
        self.page_size = 13
        self.order = "name"
        for widget in window.source_lists.values():
            widget.setItemDelegate(IntegratedCatalogDelegate(widget))
            widget.setSpacing(0)
            widget.setFrameShape(QFrame.NoFrame)
            widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            widget.setStyleSheet("QListWidget::item { padding: 0; border: none; }")
            widget.setUniformItemSizes(False)
            widget.itemSelectionChanged.connect(
                lambda widget=widget: self.mark_selection(widget)
            )
        self.sort = QComboBox()
        self.sort.addItem("Nombre · A a Z", "name")
        self.sort.addItem("Precio · Menor primero", "price")
        self.sort.addItem("Precio · Mayor primero", "price_desc")
        self.sort.setMinimumWidth(0)
        window.catalog_layout.itemAt(0).layout().addWidget(self.sort)
        self.sort.currentIndexChanged.connect(self.order_changed)
        self.header = self.column_headings()
        window.catalog_layout.insertWidget(1, self.header)
        self.footer = QHBoxLayout()
        self.counter = QLabel()
        self.previous = QPushButton("‹")
        self.next = QPushButton("›")
        self.size = QComboBox()
        self.size.addItems(["8", "13", "20"])
        self.size.setCurrentText("13")
        for control in (self.previous, self.next):
            control.setAutoDefault(False)
            control.setFixedWidth(32)
        self.previous.clicked.connect(lambda: self.move(-1))
        self.next.clicked.connect(lambda: self.move(1))
        self.size.currentTextChanged.connect(self.size_changed)
        self.footer.addWidget(self.counter, 1)
        for control in (self.previous, self.next, self.size):
            self.footer.addWidget(control)
        window.catalog_layout.addLayout(self.footer)
        window.tabs.currentChanged.connect(lambda: self.show_page(0))

    def column_headings(self):
        header = QWidget()
        header.setObjectName("CatalogColumnHeadings")
        grid = QGridLayout(header)
        grid.setContentsMargins(8, 0, 6, 0)
        grid.setSpacing(4)
        name = QLabel("Nombre del ítem")
        name.setObjectName("CatalogNameHeading")
        price = QLabel("Precio (RD$)")
        price.setFixedWidth(84)
        price.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        actions = QWidget()
        actions.setFixedWidth(112)
        grid.addWidget(name, 0, 0)
        grid.addWidget(price, 0, 1)
        grid.addWidget(actions, 0, 2)
        grid.setColumnStretch(0, 1)
        return header

    def current_list(self):
        return self.window.source_lists[self.window.get_current_category()]

    def mark_selection(self, widget):
        for index in range(widget.count()):
            item = widget.item(index)
            row = widget.itemWidget(item)
            if row is not None:
                row.setProperty("selected", item.isSelected())
                row.style().unpolish(row)
                row.style().polish(row)
                row.update()

    def order_changed(self):
        self.order = self.sort.currentData()
        for widget in self.window.source_lists.values():
            self.reorder(widget)
        self.show_page(0)

    def reorder(self, widget):
        items = [widget.takeItem(0) for _ in range(widget.count())]
        favorites = self.backend.CATALOG_FAVORITE_ROLE

        def key(item):
            name, price = item.data(Qt.UserRole)
            value = name.casefold() if self.order == "name" else float(price)
            if self.order == "price_desc":
                value = -value
            return not bool(item.data(favorites)), value, name.casefold()

        for item in sorted(items, key=key):
            widget.addItem(item)

    def size_changed(self, value):
        self.page_size = int(value)
        self.show_page(0)

    def move(self, offset):
        self.show_page(self.page + offset)

    def filled(self, widget):
        self.reorder(widget)
        if widget is self.current_list():
            self.show_page(0)
        else:
            self.render_page(widget, 0)

    def show_page(self, page):
        widget = self.current_list()
        pages = max(1, ceil(widget.count() / self.page_size))
        self.page = max(0, min(int(page), pages - 1))
        self.render_page(widget, self.page)
        if widget.count():
            widget.setCurrentItem(widget.item(self.page * self.page_size))
        else:
            widget.clearSelection()
        first = self.page * self.page_size + 1 if widget.count() else 0
        last = min(widget.count(), (self.page + 1) * self.page_size)
        self.counter.setText(
            f"{first}–{last} de {widget.count()} · Página {self.page + 1}/{pages}"
        )
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        widget.scrollToTop()

    def render_page(self, widget, page):
        for index in range(widget.count()):
            item = widget.item(index)
            visible = page * self.page_size <= index < (page + 1) * self.page_size
            item.setHidden(not visible)
            if visible and widget.itemWidget(item) is None:
                item.setSizeHint(QSize(0, 44))
                widget.setItemWidget(item, self.row_widget(widget, item))

    def row_widget(self, widget, item):
        row = CatalogRow(item)
        name, price = item.data(Qt.UserRole)
        label = CatalogNameLabel(name)
        label.setObjectName("CatalogItemName")
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
        price_label = QLabel(f"RD$ {float(price):,.2f}")
        price_label.setObjectName("CatalogPrice")
        price_label.setToolTip(price_label.text())
        price_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        price_label.setFixedWidth(84)
        price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        star = QToolButton()
        star.setObjectName("DesignStar")
        star.setFixedWidth(24)
        favorite = bool(item.data(self.backend.CATALOG_FAVORITE_ROLE))
        star.setText("★" if favorite else "☆")
        star.setToolTip("Quitar de favoritos" if favorite else "Agregar a favoritos")
        star.clicked.connect(
            lambda: widget.favoriteToggled.emit(str(name), float(price), not favorite)
        )
        add = QPushButton("+  Añadir")
        add.setFixedWidth(84)
        add.setToolTip(f"Añadir {name} al recibo")
        add.setObjectName("DesignAdd")
        add.setProperty("preserveOriginalIcons", True)
        add.setAutoDefault(False)
        add.clicked.connect(lambda: self.add(widget, item))
        row.set_controls(label, price_label, star, add)
        return row

    def add(self, widget, item):
        widget.setCurrentItem(item)
        category = item.data(self.backend.CATALOG_CATEGORY_ROLE)
        self.window.add_selected_item(category_override=category)
