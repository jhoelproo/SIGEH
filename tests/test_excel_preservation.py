"""Regression checks use only disposable XLSX artifacts."""

from unittest.mock import Mock

import pytest
from openpyxl import Workbook

from admission_v15_adapter import load_v15_application_module


@pytest.fixture(params=["integrated", "legacy"])
def excel_module(request):
    v15 = load_v15_application_module()
    if request.param == "integrated":
        return v15
    import ast
    from pathlib import Path
    from types import ModuleType

    source = (
        Path(__file__).resolve().parents[1] / "admission_source/facturacion_tabs.py"
    )
    names = {
        "guardar_excel_seguro",
        "abrir_excel_workbook_seguro",
        "recrear_excel_basico_por_corrupcion",
    }
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ModuleType("legacy_excel_callbacks")
    module.__dict__.update(vars(v15))
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"),
        module.__dict__,
    )
    return module


def test_partial_save_does_not_damage_previous_file(tmp_path, excel_module):
    v15 = excel_module
    target = tmp_path / "patients.xlsx"
    workbook = Workbook()
    workbook.save(target)
    previous = target.read_bytes()

    def partial_save(path):
        from pathlib import Path

        Path(path).write_bytes(b"incomplete zip")
        raise OSError("simulated disk failure")

    with pytest.raises(OSError, match="disk failure"):
        v15.guardar_excel_seguro(
            Mock(save=partial_save), str(target), interactivo=False
        )
    assert target.read_bytes() == previous


def test_corrupt_open_preserves_evidence_without_recreation(
    tmp_path, monkeypatch, excel_module
):
    v15 = excel_module
    target = tmp_path / "patients.xlsx"
    target.write_bytes(b"corrupt evidence")
    recreate = Mock()
    monkeypatch.setattr(v15, "recrear_excel_basico_por_corrupcion", recreate)
    monkeypatch.setattr(v15.messagebox, "showwarning", Mock())
    with pytest.raises(Exception):
        v15.abrir_excel_workbook_seguro(str(target))
    recreate.assert_not_called()
    assert target.read_bytes() == b"corrupt evidence"


def test_existing_corrupt_version_is_not_reported_as_empty(tmp_path, monkeypatch):
    import zipfile

    v15 = load_v15_application_module()
    monkeypatch.setattr(v15, "EXCEL_VERSIONED_DIR", str(tmp_path))
    monkeypatch.setattr(v15.messagebox, "showwarning", Mock())
    target = v15._versioned_excel_path(7, "synthetic")
    from pathlib import Path

    Path(target).write_bytes(b"evidence")
    with pytest.raises(zipfile.BadZipFile):
        v15._generate_versioned_excel(Mock(), {}, turn_id=7, transition_id="synthetic")
    assert Path(target).read_bytes() == b"evidence"
