"""TS-24 · is_active column on users (FR-22)."""
from __future__ import annotations


def test_is_active_column_exists():
    from app.services import storage
    cols = [r["name"] for r in storage._conn().execute("PRAGMA table_info(users)").fetchall()]
    assert "is_active" in cols, f"users table missing is_active; has {cols}"


def test_new_user_is_active_by_default():
    from app.services import storage
    u = storage.create_user(name="active-by-default", api_key="k1")
    fetched = storage.get_user(u.id)
    assert fetched.is_active is True


def test_disabled_user_cannot_authenticate():
    from app.services import storage
    u = storage.create_user(name="to-revoke", api_key="rev-key")
    assert storage.get_user_by_api_key("rev-key") is not None
    storage.set_user_active(u.id, False)
    assert storage.get_user_by_api_key("rev-key") is None, (
        "Revoked user should no longer be looked up by their key."
    )


def test_reenable_user():
    from app.services import storage
    u = storage.create_user(name="flip-flop", api_key="ff-key")
    storage.set_user_active(u.id, False)
    assert storage.get_user_by_api_key("ff-key") is None
    storage.set_user_active(u.id, True)
    assert storage.get_user_by_api_key("ff-key") is not None
