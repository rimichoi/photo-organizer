"""사용자 데이터 폴더 경로 — 실행 위치가 아니라 OS별 쓰기 가능 폴더에 DB/썸네일/
격리 폴더를 둔다(패키징된 앱이 읽기전용 위치에 설치돼도 안전, docs/PACKAGING.md)."""
from __future__ import annotations

import os
from pathlib import Path

from .platform_utils import IS_MACOS, IS_WINDOWS

_APP = "PhotoOrganizer"


def app_data_dir() -> Path:
    """OS별 사용자 데이터 폴더(없으면 생성해 반환).

    - Windows: %APPDATA%/PhotoOrganizer
    - macOS:   ~/Library/Application Support/PhotoOrganizer
    - Linux:   $XDG_DATA_HOME 또는 ~/.local/share /PhotoOrganizer
    """
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = Path(base) / _APP
    elif IS_MACOS:
        d = Path.home() / "Library" / "Application Support" / _APP
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
        d = Path(base) / _APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_db_path() -> str:
    return str(app_data_dir() / "library.db")


def default_thumb_dir() -> str:
    return str(app_data_dir() / "thumbnails")


def default_quarantine_dir() -> str:
    return str(app_data_dir() / "격리보관함")
