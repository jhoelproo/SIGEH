import inspect

from admission_v15_adapter import _HybridAdmissionRuntime


def test_binding_a_database_never_resets_or_tombstones_existing_history():
    source = inspect.getsource(_HybridAdmissionRuntime.bind_database)

    assert "history_reset" not in source
    assert "apply_authorized_history_reset" not in source
