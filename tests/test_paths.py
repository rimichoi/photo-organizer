from __future__ import annotations

import os
from pathlib import Path

from photo_organizer.core import paths


def test_app_data_dir_under_home_and_created(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    d = paths.app_data_dir()
    assert d.exists() and d.is_dir()
    assert "PhotoOrganizer" in str(d)
    assert str(d).startswith(str(tmp_path))  # 실제 홈이 아니라 tmp 아래


def test_default_paths_under_app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    base = str(paths.app_data_dir())
    assert paths.default_db_path().startswith(base) and paths.default_db_path().endswith("library.db")
    assert paths.default_thumb_dir().startswith(base) and paths.default_thumb_dir().endswith("thumbnails")
    assert paths.default_quarantine_dir().startswith(base)
