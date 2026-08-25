"""Embeddable facade for the complete, real Admisión V15 interface."""

from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QHideEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QLayout,
    QMainWindow,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .admission_context import AdmissionContext, create_standalone_context
from .facturacion_tabs_pyside6 import (
    APP_LOG,
    App,
    DatabaseManager,
    MainAppGateway,
    load_session_context,
)
from .qt_compat import EmbeddedWindowRoot, create_standalone_application


class AdmissionWidget(QWidget):
    """Hosts the unmodified V15 content inside a regular QWidget."""

    def __init__(self, parent=None, *, context: AdmissionContext):
        super().__init__(parent)
        if QApplication.instance() is None:
            raise RuntimeError(
                "AdmissionWidget requiere la QApplication creada por la aplicación principal."
            )

        self.setObjectName("admissionV15Widget")
        self._last_focus_widget = None
        self._layout_sync_in_progress = False
        self._layout_sync_scheduled = False
        self._layout_sync_force = False
        self._layout_sync_reason = "construction"
        self._last_layout_sync_size = None
        self._host_layout_snapshot = None
        self._first_layout_sync_done = False
        self._application_state_connected = False
        if not isinstance(context, AdmissionContext):
            raise TypeError("AdmissionWidget requiere AdmissionContext.")
        self.context = context
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self.root = EmbeddedWindowRoot(self, themename="superhero")
        layout.addWidget(self.root)
        self.admission = App(standalone=False, root=self.root, context=context)
        # The host's visual snapshot belongs to the context before V15 is
        # constructed.  Reapply that same snapshot to the completed button
        # tree now, while the root is still hidden, so the first frame never
        # depends on a later theme_toggled signal.
        self._apply_initial_host_theme()
        visual_notifier = getattr(self.admission, "set_embedded_visual_notifier", None)
        if callable(visual_notifier):
            visual_notifier(self._on_embedded_visual_event)
        application = QApplication.instance()
        if application is not None:
            application.applicationStateChanged.connect(self._on_application_state_changed)
            self._application_state_connected = True
        self.root.show()
        self._schedule_embedded_layout_sync(force=True, reason="construction")
        preferred_focus = getattr(self.admission, "entry_nombre", None)
        if preferred_focus is not None:
            self.setFocusProxy(preferred_focus)

    def _apply_initial_host_theme(self) -> None:
        """Polish V15 controls with the host snapshot before the first show."""
        configuration = dict(getattr(self.context, "configuration", {}) or {})
        if "host_theme_is_dark" not in configuration:
            return
        apply_theme = getattr(self.admission, "apply_host_theme", None)
        if callable(apply_theme):
            apply_theme(
                bool(configuration.get("host_theme_is_dark")),
                theme_tokens=configuration.get("host_visual_theme"),
            )

    def remember_focus(self, widget=None):
        """Remember an editable descendant before the host changes page."""
        candidate = widget or QApplication.focusWidget()
        if candidate is None:
            return
        if candidate is self or self.isAncestorOf(candidate):
            self._last_focus_widget = candidate

    def minimumSizeHint(self):
        """The embedded viewport may shrink independently of V15's wide hints."""
        return QSize(0, 0)

    @staticmethod
    def _can_receive_focus(widget):
        if widget is None:
            return False
        try:
            if not widget.isEnabled() or not widget.isVisible():
                return False
            if widget.focusPolicy() == Qt.NoFocus:
                return False
            return not (hasattr(widget, "isReadOnly") and widget.isReadOnly())
        except RuntimeError:
            return False

    def restore_focus(self):
        """Restore focus synchronously; no repaint timer or event-loop pump."""
        candidate = self._last_focus_widget
        if not self._can_receive_focus(candidate):
            candidate = getattr(self.admission, "entry_nombre", None)
        if self._can_receive_focus(candidate):
            candidate.setFocus(Qt.OtherFocusReason)
            return True
        return self.focusNextPrevChild(True)

    def shutdown(self):
        """Release V15-owned callbacks/resources without touching QApplication."""
        if self._application_state_connected:
            application = QApplication.instance()
            if application is not None:
                try:
                    application.applicationStateChanged.disconnect(
                        self._on_application_state_changed
                    )
                except (RuntimeError, TypeError):
                    pass
            self._application_state_connected = False
        self.admission.shutdown()

    def apply_host_theme(self, is_dark: bool, theme_tokens=None) -> bool:
        """Apply the shell theme without persisting it as a V15 preference."""
        apply_theme = getattr(self.admission, "apply_host_theme", None)
        changed = (
            bool(apply_theme(bool(is_dark), theme_tokens=theme_tokens))
            if callable(apply_theme)
            else False
        )
        # The V15 palette is already updated above.  Apply the one required
        # responsive/QSS pass now when visible; scheduling a second identical
        # pass used to make the global Day/Night toggle noticeably sluggish.
        if self.isVisible():
            self.sync_embedded_layout(force=True, reason="host_theme")
        else:
            self.request_layout_stabilization("host_theme", force=True)
        return changed

    def apply_layout_profile(self, snapshot) -> None:
        """Receive the host preference/DPI snapshot without owning its geometry."""
        self._host_layout_snapshot = snapshot
        if self.isVisible():
            self._schedule_embedded_layout_sync(force=True, reason="host_profile")

    def request_layout_stabilization(self, reason="visual", *, force=True):
        """Queue one GUI-only layout pass after a visual state transition."""
        self._schedule_embedded_layout_sync(force=force, reason=reason)

    def _entry_metrics(self):
        entries = (
            "entry_nombre",
            "entry_edad",
            "entry_cedula",
            "entry_telefono",
            "entry_direccion",
            "entry_nacionalidad",
            "entry_ars",
            "entry_nss",
        )
        metrics = []
        for name in entries:
            widget = getattr(self.admission, name, None)
            if widget is None:
                continue
            try:
                metrics.append(
                    f"{name}=h{widget.height()}/sh{widget.sizeHint().height()}"
                    f"/min{widget.minimumHeight()}/max{widget.maximumHeight()}"
                )
            except RuntimeError:
                continue
        return ",".join(metrics) or "entries=unavailable"

    def _log_layout_metrics(self, event_name):
        content = getattr(self.admission, "content_area", None)
        frame = getattr(self.admission, "frame", None)
        APP_LOG.info(
            "%s host=%sx%s root=%sx%s content=%sx%s frame=%sx%s %s",
            event_name,
            self.width(),
            self.height(),
            self.root.width(),
            self.root.height(),
            content.width() if content is not None else 0,
            content.height() if content is not None else 0,
            frame.width() if frame is not None else 0,
            frame.height() if frame is not None else 0,
            self._entry_metrics(),
        )

    def _on_embedded_visual_event(self, event_name):
        event_name = str(event_name or "visual")
        if event_name == "before_pdf":
            self._log_layout_metrics("ADMISSION_LAYOUT_BEFORE_PDF")
            return
        if event_name == "pdf_complete":
            self._log_layout_metrics("ADMISSION_LAYOUT_PDF_COMPLETE")
            self.request_layout_stabilization("pdf_complete", force=True)

    def _on_application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            if self.isVisible():
                self._log_layout_metrics("ADMISSION_LAYOUT_APP_REACTIVATE")
                self.request_layout_stabilization("app_reactivate", force=True)
            return
        if self.isVisible():
            self._log_layout_metrics("ADMISSION_LAYOUT_APP_DEACTIVATE")

    def _schedule_embedded_layout_sync(self, *, force=False, reason="resize"):
        """Coalesce geometry work until Qt has laid out the host widget."""
        self._layout_sync_force = self._layout_sync_force or bool(force)
        self._layout_sync_reason = str(reason or "resize")
        if self._layout_sync_scheduled:
            return
        self._layout_sync_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_layout_sync)

    def _run_scheduled_layout_sync(self):
        self._layout_sync_scheduled = False
        force = self._layout_sync_force
        reason = self._layout_sync_reason
        self._layout_sync_force = False
        self.sync_embedded_layout(force=force, reason=reason)

    def sync_embedded_layout(self, *, force=False, reason="resize"):
        """Synchronize V15's nested layouts with the real embedded viewport."""
        if (
            self._layout_sync_in_progress
            or getattr(self, "admission", None) is None
            or not self.isVisible()
        ):
            return False
        available_size = (int(self.width()), int(self.height()))
        if min(available_size) <= 1:
            return False
        if not force and available_size == self._last_layout_sync_size:
            return False

        self._layout_sync_in_progress = True
        self._last_layout_sync_size = available_size
        try:
            # The main shell owns the outside geometry. V15 resolves density
            # against this actual embedded viewport, not the monitor size.
            apply_responsive = getattr(
                self.admission, "apply_embedded_responsive_layout", None
            )
            if callable(apply_responsive):
                apply_responsive(
                    available_size[0],
                    available_size[1],
                    self._host_layout_snapshot,
                )
            else:
                self.admission._aplicar_modo_responsivo()
            ensure_entry_geometry = getattr(
                self.admission, "ensure_embedded_entry_geometry", None
            )
            if callable(ensure_entry_geometry):
                ensure_entry_geometry(
                    establish_baseline=not self._first_layout_sync_done
                )

            containers = (
                getattr(self.admission, "frame", None),
                getattr(self.admission, "content_area", None),
                getattr(self.admission, "main", None),
                getattr(self.root, "_central", None),
                self.root,
                self,
            )
            active_containers = tuple(widget for widget in containers if widget is not None)
            for widget in active_containers:
                layout = widget.layout()
                if layout is not None:
                    layout.invalidate()
                widget.updateGeometry()
            for widget in active_containers:
                layout = widget.layout()
                if layout is not None:
                    layout.activate()

            first_show = not self._first_layout_sync_done
            self._first_layout_sync_done = True
            if first_show:
                self._log_layout_metrics("ADMISSION_LAYOUT_BASELINE")
            elif reason == "app_reactivate":
                self._log_layout_metrics("ADMISSION_LAYOUT_AFTER_REACTIVATE")
            return True
        finally:
            self._layout_sync_in_progress = False

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self.admission.activate()
        self._schedule_embedded_layout_sync(
            force=True,
            reason="show",
        )
        self.root.resume_owned_toplevels()
        self.restore_focus()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._schedule_embedded_layout_sync(reason="resize")

    def hideEvent(self, event: QHideEvent):
        self.remember_focus()
        self.root.suspend_owned_toplevels()
        self.admission.deactivate()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent):
        self.shutdown()
        event.accept()


class AdmissionStandaloneWindow(QMainWindow):
    """Development-only QMainWindow wrapper around AdmissionWidget."""

    def __init__(self, parent=None, *, context=None):
        super().__init__(parent)
        self.setWindowTitle("Generador de Formularios de Emergencia - Hospital General")
        standalone_context = context or create_standalone_context(
            session_context=load_session_context(),
            main_app_gateway=MainAppGateway.from_environment(),
            admission_database_factory=lambda session: DatabaseManager(
                session_context=session,
                event_bus=None,
            ),
            logger=APP_LOG,
        )
        self.admission_widget = AdmissionWidget(self, context=standalone_context)
        self.setCentralWidget(self.admission_widget)

        requested = self.admission_widget.admission.app_settings.get(
            "window_size", "1280x740"
        )
        match = re.match(r"\s*(\d+)x(\d+)", str(requested))
        if match:
            self.resize(int(match.group(1)), int(match.group(2)))
        else:
            self.resize(1280, 740)
        self.setMinimumSize(1220, 700)

    def closeEvent(self, event: QCloseEvent):
        self.admission_widget.shutdown()
        event.accept()


def run_standalone():
    """Run the embeddable facade as a standalone development application."""
    qt_app = create_standalone_application()
    window = AdmissionStandaloneWindow()
    window.show()
    return qt_app.exec()


__all__ = ["AdmissionStandaloneWindow", "AdmissionWidget", "run_standalone"]
