"""안전 작업 — 휴지통/격리 이동 + 되돌리기 (docs/SPEC.md 3.2 데이터 안전성, FR-08).

절대 원칙(비파괴성): 자동 완전삭제 없음. 삭제는 항상 OS 휴지통 또는 격리 폴더
이동이며, 모든 작업을 action_log에 기록해 추적·복구가 가능하다.

- 휴지통(send2trash): OS 휴지통으로 보냄. 복구는 OS(파인더/탐색기)에서.
- 격리(quarantine): 우리가 관리하는 폴더로 이동 → 앱 내 '되돌리기'로 원위치 복구 가능.
개별 파일 오류가 전체를 막지 않도록 파일 단위로 예외를 격리한다(NFR-03).
"""
from __future__ import annotations

import os
import shutil

from send2trash import send2trash

from .database import Database
from .platform_utils import normalize_long_path


def _unique_dest(dest_dir: str, name: str) -> str:
    """격리 폴더에서 이름 충돌 시 ' (1)', ' (2)' … 를 붙여 유일 경로 생성."""
    base, ext = os.path.splitext(name)
    candidate = os.path.join(dest_dir, name)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base} ({n}){ext}")
        n += 1
    return candidate


def trash_files(db: Database, file_ids: list[int]) -> tuple[int, int]:
    """파일들을 OS 휴지통으로 보낸다. (성공 수, 실패 수) 반환."""
    batch = db.next_action_batch()
    rows: list[tuple] = []
    done: list[int] = []
    failed = 0
    for fid, path in db.paths_for_ids(file_ids):
        try:
            send2trash(normalize_long_path(path))
            rows.append((fid, "trash", path, None))
            done.append(fid)
        except Exception:
            failed += 1
    db.record_actions(batch, rows)
    db.mark_removed(done, 1)
    return len(done), failed


def quarantine_files(
    db: Database, file_ids: list[int], quarantine_dir: str
) -> tuple[int, int]:
    """파일들을 격리 폴더로 이동한다. (성공 수, 실패 수) 반환."""
    os.makedirs(quarantine_dir, exist_ok=True)
    batch = db.next_action_batch()
    rows: list[tuple] = []
    done: list[int] = []
    failed = 0
    for fid, path in db.paths_for_ids(file_ids):
        try:
            dest = _unique_dest(quarantine_dir, os.path.basename(path))
            shutil.move(normalize_long_path(path), normalize_long_path(dest))
            rows.append((fid, "quarantine", path, dest))
            done.append(fid)
        except Exception:
            failed += 1
    db.record_actions(batch, rows)
    db.mark_removed(done, 1)
    return len(done), failed


def undo_last(db: Database) -> int:
    """되돌릴 수 있는 가장 최근 배치(격리 이동)를 원위치로 복구한다. 복구 수 반환.

    휴지통 작업은 OS에서 복구해야 하므로 여기서 되돌리지 않는다.
    """
    batch = db.last_undoable_batch()
    if batch is None:
        return 0
    restored: list[int] = []
    for fid, action, from_path, to_path in db.actions_in_batch(batch):
        if action != "quarantine" or not to_path:
            continue
        if not os.path.exists(normalize_long_path(to_path)):
            continue
        try:
            os.makedirs(os.path.dirname(from_path), exist_ok=True)
            shutil.move(normalize_long_path(to_path), normalize_long_path(from_path))
            restored.append(fid)
        except OSError:
            continue
    db.mark_removed(restored, 0)
    db.mark_batch_undone(batch)
    return len(restored)
