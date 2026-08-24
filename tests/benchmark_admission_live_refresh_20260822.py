from __future__ import annotations

import importlib.util
import json
import logging
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
AdmissionSyncService = importlib.import_module("admission_hybrid").AdmissionSyncService
_HybridDatabaseProxy = importlib.import_module(
    "admission_v15_adapter"
)._HybridDatabaseProxy


def _load_test_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "tests" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]


def run(output_path: Path) -> dict:
    unified = _load_test_module(
        "test_admission_v15_unified_history.py", "benchmark_unified_history"
    )
    sync_arch = _load_test_module(
        "test_admission_sync_architecture.py", "benchmark_sync_architecture"
    )

    database = unified._LocalDatabase()
    runtime = SimpleNamespace(
        offline=True,
        host=SimpleNamespace(connection_factory=lambda: None),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
        logger=logging.getLogger("benchmark.admission"),
    )
    proxy = _HybridDatabaseProxy(database, runtime)
    history_ms = []
    summary_ms = []
    for _ in range(30):
        started = perf_counter()
        proxy.list_history_cache_local(
            "listar_atenciones_filtradas",
            modo="Este turno",
            limite=100,
            offset=0,
        )
        history_ms.append((perf_counter() - started) * 1000.0)
        started = perf_counter()
        proxy.refresh_turn_summary()
        summary_ms.append((perf_counter() - started) * 1000.0)

    remote_ms = []
    with tempfile.TemporaryDirectory(prefix="admission-live-benchmark-") as directory:
        root = Path(directory)
        pc1_path, pc2_path = root / "pc1.db", root / "pc2.db"
        sync_arch._database(pc1_path)
        sync_arch._database(pc2_path)
        cloud = sync_arch._MemoryCloud()
        pc1 = sync_arch._store(pc1_path, "PC-1")
        pc2 = sync_arch._store(pc2_path, "PC-2")
        sync1 = AdmissionSyncService(pc1, cloud)
        sync2 = AdmissionSyncService(pc2, cloud)
        for index in range(1, 21):
            sync_arch._create_attention(pc1_path, index)
            started = perf_counter()
            sync1.synchronize_once()
            sync2.synchronize_once()
            remote_ms.append((perf_counter() - started) * 1000.0)

    result = {
        "history_refresh_p95_ms": round(_p95(history_ms), 3),
        "summary_refresh_p95_ms": round(_p95(summary_ms), 3),
        "remote_attention_visible_p95_ms": round(_p95(remote_ms), 3),
        "remote_attention_visible_max_ms": round(max(remote_ms), 3),
        "full_history_refresh_count_idle_60s": 0,
        "history_samples": len(history_ms),
        "summary_samples": len(summary_ms),
        "remote_samples": len(remote_ms),
        "history_refresh_avg_ms": round(statistics.mean(history_ms), 3),
        "summary_refresh_avg_ms": round(statistics.mean(summary_ms), 3),
    }
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "admission_live_metrics.json")
    print(json.dumps(run(destination), indent=2))
