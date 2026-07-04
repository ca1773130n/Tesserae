"""cognee has no schema migration across versions, so a system_root seeded by an
older cognee (missing e.g. users.tenant_id) breaks cognify. The version-stamp
guard must reset such a store and preserve a same-version one.
"""

from __future__ import annotations

from pathlib import Path

from tesserae import cognee_direct


def _seed_store(root: Path, stamp: str | None) -> Path:
    dbs = root / "databases"
    dbs.mkdir(parents=True)
    (dbs / "cognee_db").write_bytes(b"SQLite format 3\x00 (pretend stale)")
    if stamp is not None:
        (root / ".cognee_version").write_text(stamp, encoding="utf-8")
    return dbs


def test_reset_wipes_store_from_a_different_cognee_version(tmp_path, monkeypatch):
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    root = tmp_path / "cognee_system"
    dbs = _seed_store(root, stamp="0.1.44")  # older cognee
    assert cognee_direct.reset_stale_cognee_system_db(root) is True
    assert not dbs.exists()  # stale store dropped
    assert (root / ".cognee_version").read_text() == "1.1.0"  # restamped


def test_reset_wipes_unstamped_store(tmp_path, monkeypatch):
    # The real-world case this fixes: a pre-existing dir with no version stamp.
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    root = tmp_path / "cognee_system"
    dbs = _seed_store(root, stamp=None)
    assert cognee_direct.reset_stale_cognee_system_db(root) is True
    assert not dbs.exists()
    assert (root / ".cognee_version").read_text() == "1.1.0"


def test_reset_preserves_same_version_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    root = tmp_path / "cognee_system"
    dbs = _seed_store(root, stamp="1.1.0")  # matching → keep the accumulated store
    assert cognee_direct.reset_stale_cognee_system_db(root) is False
    assert dbs.exists()


def test_reset_noop_when_version_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: None)
    root = tmp_path / "cognee_system"
    dbs = _seed_store(root, stamp="0.1.44")
    assert cognee_direct.reset_stale_cognee_system_db(root) is False
    assert dbs.exists()  # can't tell → leave it alone


def test_fresh_dir_stamps_without_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    root = tmp_path / "cognee_system"  # no databases dir at all
    assert cognee_direct.reset_stale_cognee_system_db(root) is False
    assert (root / ".cognee_version").read_text() == "1.1.0"


def test_reset_refuses_non_cognee_databases_dir(tmp_path, monkeypatch):
    # A user-supplied root whose 'databases' child is NOT a cognee store must be
    # left untouched (codex MAJOR: destructive footgun).
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    root = tmp_path / "someones_dir"
    dbs = root / "databases"
    dbs.mkdir(parents=True)
    (dbs / "important.txt").write_text("not a cognee store")
    (root / ".cognee_version").write_text("0.1.44")
    assert cognee_direct.reset_stale_cognee_system_db(root) is False
    assert (dbs / "important.txt").exists()  # not wiped


def test_reset_refuses_symlinked_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    real = tmp_path / "real_store"
    _seed_store(real, stamp="0.1.44")  # looks like a cognee store, older version
    link = tmp_path / "linked_store"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unavailable")
    assert cognee_direct.reset_stale_cognee_system_db(link) is False
    assert (real / "databases" / "cognee_db").exists()  # never deleted through the symlink


def test_failed_delete_is_not_stamped(tmp_path, monkeypatch):
    # If rmtree fails, we must NOT stamp current — else the broken store gets
    # marked same-version and preserved forever (codex MAJOR).
    monkeypatch.setattr(cognee_direct, "_installed_cognee_version", lambda: "1.1.0")
    root = tmp_path / "cognee_system"
    _seed_store(root, stamp="0.1.44")

    def boom(*a, **k):
        raise OSError("permission denied")
    monkeypatch.setattr(cognee_direct.shutil, "rmtree", boom)
    assert cognee_direct.reset_stale_cognee_system_db(root) is False
    # Stamp still the OLD version → next run retries instead of masking.
    assert (root / ".cognee_version").read_text() == "0.1.44"
