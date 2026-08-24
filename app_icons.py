"""Central, resource-free semantic icons for the integrated Qt workflow."""

from __future__ import annotations

import re
import math
import unicodedata
import weakref

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton, QWidget


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _clean_button_text(value: str) -> str:
    text = str(value or "").strip()
    # V15 historically used emoji as text prefixes.  Keep its wording while
    # replacing those glyphs with real QIcons supplied by this module.
    text = re.sub(r"^[^0-9A-Za-zÀ-ɏ¿]+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


class AppIcons(QObject):
    """Assign homogeneous, semantic QIcons inside explicitly registered roots."""

    OBJECT_KEYS = {
        "SidebarNewItemButton": "add",
    }

    # Longest/specific phrases are intentionally evaluated first.
    TEXT_KEYS = (
        ("usar en facturacion", "verify"),
        ("verificar paciente", "verify"),
        ("usar paciente", "verify"),
        ("historial sin seguro", "uninsured"),
        ("reporte estadistico", "report"),
        ("generar reporte", "report"),
        ("abrir listado en excel", "excel"),
        ("generar excel", "excel"),
        ("exportar excel", "excel"),
        ("vista previa", "preview"),
        ("crear / abrir pdf", "pdf"),
        ("generar pdf", "pdf"),
        ("abrir pdf", "pdf"),
        ("guardar pdf", "pdf"),
        ("cambiar turno", "shift"),
        ("mostrar migracion", "shift"),
        ("opciones avanzadas", "settings"),
        ("gestion ars", "settings"),
        ("configuracion", "settings"),
        ("preferencias", "settings"),
        ("buscar", "search"),
        ("actualizar", "refresh"),
        ("recargar", "refresh"),
        ("reintentar", "refresh"),
        ("importar", "import"),
        ("exportar", "export"),
        ("retirar", "delete"),
        ("historial", "history"),
        ("recibos guardados", "history"),
        ("auditoria", "history"),
        ("reportes", "report"),
        ("excel", "excel"),
        ("editar", "edit"),
        ("corregir", "edit"),
        ("menu", "menu"),
        ("limpiar", "clear"),
        ("restablecer", "clear"),
        ("borrar", "clear"),
        ("imprimir", "print"),
        ("reimprimir", "print"),
        ("anular", "delete"),
        ("eliminar", "delete"),
        ("descartar", "delete"),
        ("quitar", "delete"),
        ("papelera", "delete"),
        ("guardar", "save"),
        ("confirmar", "confirm"),
        ("normalizar", "confirm"),
        ("reemplazar", "confirm"),
        ("aplicar", "confirm"),
        ("continuar", "next"),
        ("siguiente", "next"),
        ("anadir", "add"),
        ("agregar", "add"),
        ("crear", "add"),
        ("nuevo", "add"),
        ("anterior", "previous"),
        ("volver", "previous"),
        ("deshacer", "undo"),
        ("abrir", "open"),
        ("mostrar todo", "list"),
        ("seleccionar", "confirm"),
        ("cerrar sesion", "logout"),
        ("cancelar", "cancel"),
        ("cerrar", "cancel"),
        ("salir", "logout"),
    )

    def __init__(self):
        super().__init__()
        self._roots: list[weakref.ReferenceType] = []
        self._filter_installed = False

    @classmethod
    def semantic_key(cls, button) -> str:
        explicit = str(button.property("semanticIcon") or "").strip()
        if explicit:
            return explicit
        object_key = cls.OBJECT_KEYS.get(str(button.objectName() or ""))
        if object_key:
            return object_key
        text = _plain(_clean_button_text(button.text()))
        if not text:
            return ""
        for phrase, key in cls.TEXT_KEYS:
            if phrase in text:
                return key
        return ""

    @staticmethod
    def _foreground(button) -> QColor:
        override = button.property("semanticIconColor")
        if override:
            color = QColor(str(override))
            if color.isValid():
                return color
        style = str(button.styleSheet() or "")
        match = re.search(r"(?<!-)color\s*:\s*(#[0-9A-Fa-f]{6})", style)
        if match:
            return QColor(match.group(1))
        color = button.palette().buttonText().color()
        return color if color.isValid() else QColor("#1F5FAE")

    @staticmethod
    def _line(painter, points):
        painter.drawPolyline(QPolygonF([QPointF(*point) for point in points]))

    @classmethod
    def _paint(cls, key: str, color: QColor, size: int = 18) -> QPixmap:
        scale = 2
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        s = float(size)
        k = s / 24.0
        painter.scale(k, k)

        def line(x1, y1, x2, y2):
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        if key == "search":
            painter.drawEllipse(QRectF(4, 4, 11, 11)); line(14, 14, 20, 20)
        elif key == "refresh":
            painter.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 250 * 16)
            cls._line(painter, [(18, 4), (20, 8), (15.8, 8)])
        elif key == "history":
            painter.drawEllipse(QRectF(4, 4, 16, 16)); line(12, 7, 12, 12); line(12, 12, 8.5, 14)
            cls._line(painter, [(5, 4), (4, 9), (9, 8)])
        elif key == "report":
            line(4, 20, 20, 20); line(4, 20, 4, 5)
            painter.drawRect(QRectF(7, 13, 2.7, 7)); painter.drawRect(QRectF(11.4, 9, 2.7, 11)); painter.drawRect(QRectF(15.8, 5, 2.7, 15))
        elif key == "excel":
            painter.drawRoundedRect(QRectF(4, 3, 16, 18), 1.5, 1.5)
            line(9, 3, 9, 21); line(9, 9, 20, 9); line(9, 15, 20, 15)
            line(5.5, 8, 8, 13); line(8, 8, 5.5, 13)
        elif key == "edit":
            cls._line(painter, [(5, 18.5), (6, 14), (15.5, 4.5), (19.5, 8.5), (10, 18), (5, 18.5)])
            line(13.5, 6.5, 17.5, 10.5)
        elif key in {"settings", "billing_settings"}:
            # Dientes visibles: la versión radial anterior parecía un sol.
            outer = []
            for index in range(24):
                angle = math.radians(-90 + index * 15)
                radius = 10 if index % 3 == 0 else 8.2
                outer.append(
                    QPointF(12 + math.cos(angle) * radius, 12 + math.sin(angle) * radius)
                )
            painter.drawPolygon(QPolygonF(outer))
            painter.drawEllipse(QRectF(8.5, 8.5, 7, 7))
        elif key == "billing_receipts":
            cls._line(painter, [(6,2),(15,2),(20,7),(20,22),(6,22),(6,2)])
            cls._line(painter, [(15,2),(15,7),(20,7)])
            line(9,11,17,11); line(9,15,17,15); line(9,19,14,19)
        elif key == "billing_ars":
            cls._line(painter, [(12,2),(20,5),(19,14),(12,21),(5,14),(4,5),(12,2)])
            painter.drawEllipse(QRectF(9, 7, 6, 6))
            line(12,13,12,17); line(9.5,15,14.5,15)
        elif key == "billing_word_import":
            cls._line(painter, [(5,2),(14,2),(18,6),(18,22),(5,22),(5,2)])
            cls._line(painter, [(14,2),(14,6),(18,6)])
            cls._line(painter, [(7,10),(9,17),(12,12),(15,17),(17,10)])
            line(22,8,16,8); cls._line(painter, [(18.5,5.5),(16,8),(18.5,10.5)])
        elif key == "billing_reports":
            cls._line(painter, [(5,2),(16,2),(20,6),(20,22),(5,22),(5,2)])
            cls._line(painter, [(16,2),(16,6),(20,6)])
            line(8,18,17,18); line(8,18,8,10)
            painter.drawRect(QRectF(10, 14, 1.8, 4))
            painter.drawRect(QRectF(13, 11, 1.8, 7))
            painter.drawRect(QRectF(16, 8, 1.8, 10))
        elif key == "shift":
            line(4, 8, 19, 8); cls._line(painter, [(16,5),(19,8),(16,11)])
            line(20, 16, 5, 16); cls._line(painter, [(8,13),(5,16),(8,19)])
        elif key == "menu":
            line(4, 6, 20, 6); line(4, 12, 20, 12); line(4, 18, 20, 18)
        elif key in {"clear", "billing_clear"}:
            cls._line(painter, [(5,15),(13,5),(20,12),(12,20),(7,20),(5,18),(5,15)])
            line(9, 12, 15, 18)
        elif key == "pdf":
            cls._line(painter, [(6,2),(15,2),(20,7),(20,22),(6,22),(6,2)])
            cls._line(painter, [(15,2),(15,7),(20,7)]); line(9,12,17,12); line(9,16,17,16); line(9,20,14,20)
        elif key == "verify":
            painter.drawEllipse(QRectF(4, 3, 7, 7)); painter.drawArc(QRectF(2, 11, 12, 10), 0, 180 * 16)
            cls._line(painter, [(13,16),(16,19),(22,11)])
        elif key in {"confirm", "save"}:
            if key == "save":
                painter.drawRoundedRect(QRectF(4,3,16,18),1.5,1.5); painter.drawRect(QRectF(7,3,9,6)); painter.drawRect(QRectF(8,14,8,7))
            else:
                cls._line(painter, [(4,12),(9,17),(20,6)])
        elif key in {"cancel", "delete"}:
            if key == "delete":
                painter.drawRect(QRectF(7,8,10,12)); line(5,6,19,6); line(9,3,15,3); line(10,11,10,17); line(14,11,14,17)
            else:
                line(5,5,19,19); line(19,5,5,19)
        elif key in {"previous", "next"}:
            points = [(15,5),(8,12),(15,19)] if key == "previous" else [(9,5),(16,12),(9,19)]
            cls._line(painter, points)
        elif key == "print":
            painter.drawRect(QRectF(7,3,10,6)); painter.drawRoundedRect(QRectF(4,8,16,9),2,2); painter.drawRect(QRectF(7,14,10,7)); painter.drawPoint(QPointF(17,11))
        elif key == "preview":
            path = QPainterPath(); path.moveTo(2,12); path.cubicTo(7,4,17,4,22,12); path.cubicTo(17,20,7,20,2,12); painter.drawPath(path); painter.drawEllipse(QRectF(9,9,6,6))
        elif key == "open":
            cls._line(painter, [(3,7),(10,7),(12,10),(21,10),(18,20),(3,20),(3,7)])
        elif key in {"import", "export"}:
            painter.drawRoundedRect(QRectF(4,3,12,18),1.5,1.5)
            if key == "import":
                line(20,7,11,7); cls._line(painter,[(14,4),(11,7),(14,10)])
            else:
                line(11,7,20,7); cls._line(painter,[(17,4),(20,7),(17,10)])
        elif key == "add":
            painter.drawEllipse(QRectF(3,3,18,18)); line(12,7,12,17); line(7,12,17,12)
        elif key == "logout":
            painter.drawRect(QRectF(4,3,9,18)); line(10,12,21,12); cls._line(painter,[(17,8),(21,12),(17,16)])
        elif key == "undo":
            painter.drawArc(QRectF(5,6,15,14), -50*16, 245*16); cls._line(painter,[(7,5),(3,9),(8,11)])
        elif key == "list":
            for y in (6,12,18): painter.drawEllipse(QRectF(3,y-1,2,2)); line(8,y,21,y)
        elif key == "uninsured":
            cls._line(painter,[(12,2),(20,5),(19,14),(12,21),(5,14),(4,5),(12,2)]); line(7,17,17,7)
        elif key in {"theme", "billing_theme_to_dark"}:
            path = QPainterPath(); path.addEllipse(QRectF(5,3,14,18)); cut = QPainterPath(); cut.addEllipse(QRectF(9,1,13,15)); path = path.subtracted(cut); painter.drawPath(path)
        elif key == "billing_theme_to_light":
            painter.drawEllipse(QRectF(7, 7, 10, 10))
            for x1, y1, x2, y2 in (
                (12, 1, 12, 4), (12, 20, 12, 23), (1, 12, 4, 12),
                (20, 12, 23, 12), (4, 4, 6, 6), (18, 18, 20, 20),
                (20, 4, 18, 6), (6, 18, 4, 20),
            ):
                line(x1, y1, x2, y2)
        else:
            painter.drawEllipse(QRectF(4,4,16,16))
        painter.end()
        return pixmap

    @classmethod
    def icon(
        cls,
        key: str,
        button=None,
        size: int = 18,
        color: QColor | str | None = None,
        active_color: QColor | str | None = None,
    ) -> QIcon:
        base_color = (
            QColor(color)
            if color is not None
            else (cls._foreground(button) if button is not None else QColor("#2563EB"))
        )
        if not base_color.isValid():
            base_color = QColor("#2563EB")
        hover_color = QColor(active_color) if active_color is not None else (
            base_color if color is not None else QColor("#FFFFFF")
        )
        if not hover_color.isValid():
            hover_color = base_color
        icon = QIcon()
        icon.addPixmap(cls._paint(key, base_color, size), QIcon.Normal, QIcon.Off)
        icon.addPixmap(cls._paint(key, hover_color, size), QIcon.Active, QIcon.Off)
        icon.addPixmap(cls._paint(key, QColor("#8A98A8"), size), QIcon.Disabled, QIcon.Off)
        return icon

    def decorate_button(self, button) -> str:
        if not isinstance(button, (QPushButton, QToolButton)):
            return ""
        if self._preserves_original_icon(button):
            return "original"
        key = self.semantic_key(button)
        if not key:
            return ""
        cleaned = _clean_button_text(button.text())
        if cleaned != button.text():
            button.setText(cleaned)
        button.setIcon(self.icon(key, button))
        button.setIconSize(QSize(18, 18))
        button.setProperty("semanticIcon", key)
        return key

    @staticmethod
    def _preserves_original_icon(button) -> bool:
        """Respeta familias propias (V15 y controles nativos de Facturación)."""
        current = button
        while current is not None:
            try:
                if bool(current.property("preserveOriginalIcons")):
                    return True
                current = current.parentWidget()
            except RuntimeError:
                return True
        return False

    def decorate_tree(self, root: QWidget) -> dict[str, int]:
        buttons = []
        if isinstance(root, (QPushButton, QToolButton)):
            buttons.append(root)
        buttons.extend(root.findChildren(QPushButton))
        buttons.extend(root.findChildren(QToolButton))
        seen = set()
        decorated = 0
        unmapped = 0
        for button in buttons:
            if id(button) in seen:
                continue
            seen.add(id(button))
            if self.decorate_button(button):
                decorated += 1
            elif str(button.text() or "").strip():
                unmapped += 1
        return {"decorated": decorated, "unmapped": unmapped}

    def register_scope(self, root: QWidget) -> None:
        self._roots = [ref for ref in self._roots if ref() is not None]
        if not any(ref() is root for ref in self._roots):
            self._roots.append(weakref.ref(root))
        application = QApplication.instance()
        if application is not None and not self._filter_installed:
            application.installEventFilter(self)
            self._filter_installed = True
        self.decorate_tree(root)

    def _in_scope(self, widget) -> bool:
        live = []
        for reference in list(self._roots):
            root = reference()
            if root is None:
                continue
            try:
                if widget is root or root.isAncestorOf(widget):
                    return True
                if widget.isWindow() and widget.parentWidget() is not None:
                    parent = widget.parentWidget()
                    if parent is root or root.isAncestorOf(parent):
                        return True
                live.append(reference)
            except RuntimeError:
                continue
        self._roots = live
        return False

    def eventFilter(self, watched, event):
        if isinstance(watched, (QPushButton, QToolButton)) and self._in_scope(watched):
            if event.type() in (QEvent.Polish, QEvent.Show, QEvent.PaletteChange, QEvent.StyleChange):
                self.decorate_button(watched)
        return False


APP_ICONS = AppIcons()


__all__ = ["APP_ICONS", "AppIcons"]
