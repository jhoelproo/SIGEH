"""Arquitectura PySide6 nativa para el módulo de Admisión."""

from .context import AppContext, SharedEventBus
from .controller import AdmissionController
from .legacy_backend import LegacyAdmissionBackend
from .models import AdmissionFormState, AdmissionInput, AdmissionResult
from .repository import AdmissionRepository, AdmissionRepositoryError
from .service import AdmissionService, AdmissionValidationError
from .widget import AdmissionStandaloneWindow, AdmissionWidget
from .history import AdmissionHistoryDialog, HistoryWorker
from .documents import (
    AdmissionDocumentError,
    AdmissionDocumentService,
    DocumentResult,
    PendingPrintsDialog,
)
from .operations import (
    AdmissionInternalConfigDialog,
    AdmissionPreferencesDialog,
    AdmissionReportDialog,
)

__all__ = [
    "AdmissionController",
    "AdmissionFormState",
    "AdmissionInput",
    "LegacyAdmissionBackend",
    "AdmissionRepository",
    "AdmissionRepositoryError",
    "AdmissionResult",
    "AdmissionService",
    "AdmissionStandaloneWindow",
    "AdmissionValidationError",
    "AdmissionWidget",
    "AdmissionHistoryDialog",
    "HistoryWorker",
    "AdmissionDocumentError",
    "AdmissionDocumentService",
    "DocumentResult",
    "PendingPrintsDialog",
    "AdmissionInternalConfigDialog",
    "AdmissionPreferencesDialog",
    "AdmissionReportDialog",
    "AppContext",
    "SharedEventBus",
]
