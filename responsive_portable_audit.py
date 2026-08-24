"""Auditoría reproducible de distribuciones portable responsive."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.relative_to(root)),
        "size": stat.st_size,
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(
            timespec="seconds"
        ),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
            timespec="seconds"
        ),
        "sha256": sha256(path),
    }


def distribution_record(root: Path) -> dict:
    files = [path for path in root.rglob("*") if path.is_file()]
    executables = [
        path
        for path in files
        if path.suffix.casefold() in {".exe", ".pyd", ".dll"}
        and (
            path.parent == root
            or path.name
            in {
                "GENERADOR DE HOJAS 4.1.exe",
                "SistemaAdmision.exe",
            }
        )
    ]
    version_path = root / "VERSION.txt"
    build_info_path = root / "PORTABLE_BUILD_INFO.txt"
    return {
        "root": str(root),
        "created": datetime.fromtimestamp(root.stat().st_ctime).isoformat(
            timespec="seconds"
        ),
        "modified": datetime.fromtimestamp(root.stat().st_mtime).isoformat(
            timespec="seconds"
        ),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "executables": [
            file_record(path, root)
            for path in sorted(executables, key=lambda item: str(item).lower())
        ],
        "version_text": (
            version_path.read_text(encoding="utf-8", errors="replace")
            if version_path.is_file()
            else ""
        ),
        "build_info_text": (
            build_info_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if build_info_path.is_file()
            else ""
        ),
        "portable_env_present": (root / ".env").is_file(),
        "internal_admission_source_present": (
            root
            / "_internal"
            / "admission_source"
            / "facturacion_tabs.py"
        ).is_file(),
    }


def executable_map(record: dict) -> dict:
    return {
        item["path"].casefold(): item
        for item in record["executables"]
    }


def compare_distributions(current: dict, previous: dict) -> dict:
    current_files = executable_map(current)
    previous_files = executable_map(previous)
    common = sorted(set(current_files) & set(previous_files))
    comparisons = []
    for name in common:
        left = current_files[name]
        right = previous_files[name]
        comparisons.append(
            {
                "path": left["path"],
                "same_hash": left["sha256"] == right["sha256"],
                "same_size": left["size"] == right["size"],
                "current_sha256": left["sha256"],
                "previous_sha256": right["sha256"],
            }
        )
    top_level_names = {
        "calculos_qt.exe",
        "iniciar sistema hospital.exe",
        "verificarsistema.exe",
    }
    top_level = [
        item
        for item in comparisons
        if Path(item["path"]).name.casefold() in top_level_names
    ]
    return {
        "same_file_count": current["file_count"] == previous["file_count"],
        "same_total_bytes": (
            current["total_bytes"] == previous["total_bytes"]
        ),
        "common_executables": comparisons,
        "core_executables_identical": bool(top_level)
        and all(item["same_hash"] for item in top_level),
    }


def source_evidence(source_root: Path) -> dict:
    targets = {
        "CALCULOS_QT.py": (
            "class MainWindow",
            "class PreferencesDialog",
            "class MonthlyBillingListsPage",
            "class AdmissionValidationDialog",
            "class FullAdmissionPage",
            "class ReceiptHistoryDialog",
            "class ReceiptTrashDialog",
            "class ReportsDialog",
            "self.display_layout = DisplayLayoutManager",
            "self.full_page = FullAdmissionPage",
            "load_admission_validation_attentions",
            "medication_ars_is_selectable",
        ),
        "display_layout.py": (
            "class DisplayLayoutManager",
            'PROFILE_AUTO = "AUTO"',
            'PROFILE_COMPACT = "COMPACTO"',
            'PROFILE_STANDARD = "ESTANDAR"',
            'PROFILE_WIDE = "AMPLIO"',
            "screenChanged.connect",
            "save_splitter",
            "restore_splitter",
        ),
        "integrated_admission.py": (
            "def resolve_admission_source",
            "def _launch_in_process",
            "def pump_embedded_events",
            "pid=os.getpid()",
        ),
        "build_app.spec": (
            'ROOT / "CALCULOS_QT.py"',
            "ADMISSION_SOURCE_ENTRYPOINT",
            'name="CALCULOS_QT"',
        ),
        "portable_launcher.py": (
            'root / "CALCULOS_QT.exe"',
            "cwd=str(root)",
        ),
    }
    result = {}
    for relative, markers in targets.items():
        path = source_root / relative
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        marker_rows = []
        for marker in markers:
            line_number = next(
                (
                    index
                    for index, line in enumerate(lines, 1)
                    if marker in line
                ),
                None,
            )
            marker_rows.append(
                {
                    "marker": marker,
                    "line": line_number,
                    "found": line_number is not None,
                }
            )
        result[relative] = {
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "markers": marker_rows,
        }
    return result


def render_text(report: dict) -> str:
    audit = report["audited"]
    previous = report["previous"]
    comparison = report["comparison"]
    lines = [
        "AUDITORÍA PORTABLE RESPONSIVE",
        f"Carpeta auditada: {audit['root']}",
        f"Creada: {audit['created']}",
        f"Archivos: {audit['file_count']}",
        f"Bytes: {audit['total_bytes']}",
        "",
        f"Distribución comparada: {previous['root']}",
        (
            "Ejecutables principales idénticos: "
            + (
                "SÍ"
                if comparison["core_executables_identical"]
                else "NO"
            )
        ),
        f"Misma cantidad de archivos: {comparison['same_file_count']}",
        f"Mismo total de bytes: {comparison['same_total_bytes']}",
        "",
        "EJECUTABLES AUDITADOS",
    ]
    for item in audit["executables"]:
        lines.append(
            f"- {item['path']} | {item['size']} bytes | "
            f"{item['modified']} | SHA-256 {item['sha256']}"
        )
    lines.extend(("", "COMPARACIÓN"))
    for item in comparison["common_executables"]:
        lines.append(
            f"- {item['path']} | hash idéntico: {item['same_hash']}"
        )
    lines.extend(("", "EVIDENCIA EN FUENTE"))
    for path, evidence in report["source_evidence"].items():
        lines.append(f"- {path} | SHA-256 {evidence['sha256']}")
        for marker in evidence["markers"]:
            lines.append(
                f"  · línea {marker['line']}: {marker['marker']}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audited", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    audited = distribution_record(Path(args.audited).resolve())
    previous = distribution_record(Path(args.previous).resolve())
    report = {
        "audited": audited,
        "previous": previous,
        "comparison": compare_distributions(audited, previous),
        "source_evidence": source_evidence(Path(args.source).resolve()),
    }
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "auditoria_portable.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "auditoria_portable.txt").write_text(
        render_text(report),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
