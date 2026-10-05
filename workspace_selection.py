"""Checkboxes mirror native row selection, including keyboard and context menus."""

from PySide6.QtCore import QObject, Qt, QItemSelectionModel
from PySide6.QtWidgets import QHeaderView, QTableWidgetItem


class RowSelectionChecks(QObject):
    def __init__(self, table, column):
        super().__init__(table)
        self.table = table
        self.column = column
        self.updating = False
        table.setColumnCount(column + 1)
        table.setHorizontalHeaderItem(column, QTableWidgetItem("☑"))
        header = table.horizontalHeader()
        header.setSectionResizeMode(column, QHeaderView.Fixed)
        header.moveSection(header.visualIndex(column), 0)
        table.setColumnWidth(column, 34)
        table.itemChanged.connect(self.check_changed)
        table.itemSelectionChanged.connect(self.sync_checks)
        header.sectionClicked.connect(self.toggle_visible)

    def add_row(self, row):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        self.updating = True
        self.table.setItem(row, self.column, item)
        self.updating = False
        self.sync_checks()

    def check_changed(self, item):
        if self.updating or item.column() != self.column:
            return
        action = (
            QItemSelectionModel.Select
            if item.checkState() == Qt.Checked
            else QItemSelectionModel.Deselect
        )
        index = self.table.model().index(item.row(), 0)
        self.table.selectionModel().select(index, action | QItemSelectionModel.Rows)

    def sync_checks(self):
        self.updating = True
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.column)
            if item:
                item.setCheckState(Qt.Checked if row in selected else Qt.Unchecked)
        self.updating = False

    def toggle_visible(self, column):
        if column != self.column:
            return
        rows = [
            row
            for row in range(self.table.rowCount())
            if not self.table.isRowHidden(row)
        ]
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        action = (
            QItemSelectionModel.Deselect
            if rows and all(row in selected for row in rows)
            else QItemSelectionModel.Select
        )
        for row in rows:
            self.table.selectionModel().select(
                self.table.model().index(row, 0), action | QItemSelectionModel.Rows
            )
