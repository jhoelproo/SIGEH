import hashlib
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app_icons import APP_ICONS
from app_resources import get_app_logo_path
from admission_v15_adapter import DEFAULT_V15_ROOT


ROOT = Path(__file__).parents[1]
V15_ASSETS = DEFAULT_V15_ROOT / "assets"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_central_logo_matches_authorized_source_and_uses_no_desktop_runtime_path():
    source = Path(r"C:\Users\ampar\OneDrive\Desktop\logo.jpg")
    internal = ROOT / "assets" / "logo.jpg"
    assert source.is_file()
    assert internal.is_file()
    assert _sha256(source) == _sha256(internal)
    assert Path(get_app_logo_path()).resolve() == internal.resolve()


def test_v15_original_svg_assets_are_available():
    expected = {
        "history.svg", "report.svg", "excel.svg", "uninsured.svg", "edit.svg",
        "config.svg", "turno.svg", "menu.svg", "clear.svg", "pdf.svg",
    }
    assert expected.issubset({path.name for path in V15_ASSETS.glob("*.svg")})
    for name in expected:
        assert not QIcon(str(V15_ASSETS / name)).isNull(), name


def test_original_module_icon_is_not_replaced_by_global_decorator():
    _qt_app()
    root = QWidget()
    root.setProperty("preserveOriginalIcons", True)
    button = QPushButton("Historial", root)
    original = QIcon(str(V15_ASSETS / "history.svg"))
    button.setIcon(original)
    before = button.icon().cacheKey()
    APP_ICONS.register_scope(root)
    assert button.icon().cacheKey() == before
    assert not button.icon().isNull()
