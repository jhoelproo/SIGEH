from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMISSION_UI = ROOT / "ADMISION_PYSIDE6_V15" / "facturacion_tabs_pyside6.py"


def test_admission_ui_has_no_late_update_prompt_or_manual_update_action():
    source = ADMISSION_UI.read_text(encoding="utf-8")

    assert "buscar_actualizaciones" not in source
    assert "_iniciar_actualizador_externo" not in source
    assert "Actualizacion disponible" not in source
    assert "get_latest_release" not in source


def test_launcher_default_path_is_the_pre_login_update_gate():
    source = (ROOT / "lanzador.py").read_text(encoding="utf-8")

    assert "return run_update_check_ui()" in source
    assert "not _EARLY_UPDATE_OPT_IN" not in source
