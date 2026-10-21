from offline_auth import OfflineAuthCache


def test_offline_login_keeps_individual_deny(tmp_path):
    cache = OfflineAuthCache(tmp_path / "auth.sqlite3")
    user = {
        "id": 1,
        "username": "SYNTHETIC",
        "full_name": "SYNTHETIC",
        "role": "administrador",
        "permission_overrides": {"receipt.bypass_verification": False},
    }
    cache.store_online_auth(user, "synthetic-password", "DEVICE")
    offline = cache.authenticate("SYNTHETIC", "synthetic-password", "DEVICE")
    assert offline["permission_overrides"] == user["permission_overrides"]


def test_live_refresh_preserves_verifier_and_expiration_and_handles_disabled(tmp_path):
    cache = OfflineAuthCache(tmp_path / "auth.sqlite3")
    user = {"id": 1, "username": "SYNTHETIC", "role": "administrador"}
    cache.store_online_auth(user, "synthetic-password", "DEVICE")
    before = cache.authenticate("SYNTHETIC", "synthetic-password", "DEVICE")
    update = {"role": "auxiliar", "permission_overrides": {"receipt.validate": True}}
    cache.refresh_permissions("SYNTHETIC", "DEVICE", update)
    cache.refresh_permissions("SYNTHETIC", "DEVICE", update)
    after = cache.authenticate("SYNTHETIC", "synthetic-password", "DEVICE")
    assert after["_offline_valid_until"] == before["_offline_valid_until"]
    assert after["role"] == "auxiliar"
    assert after["permission_overrides"] == {"receipt.validate": True}
    assert cache.authenticate("SYNTHETIC", "synthetic-password", "OTHER") is None
    cache.refresh_permissions("SYNTHETIC", "DEVICE", {"is_active": 0})
    assert cache.authenticate("SYNTHETIC", "synthetic-password", "DEVICE") is None
