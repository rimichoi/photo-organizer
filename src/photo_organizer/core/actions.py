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
from .platform_utils import normalize_long_path, to_nfc

# 대량 작업 중 중단돼도 진행분이 남도록 이 개수마다 DB에 커밋한다(재개 가능성).
_COMMIT_CHUNK = 200


def unique_dest(dest_dir: str, name: str) -> str:
    """대상 폴더에서 이름 충돌 시 ' (1)', ' (2)' … 를 붙여 유일 경로 생성.

    존재 검사도 반드시 normalize_long_path를 거친다. Windows에서 260자를 넘는
    경로는 접두어 없이 stat 하면 파일이 있어도 False가 나오고, 그 경로로
    shutil.move 하면 기존 파일을 조용히 덮어쓴다(비파괴성 위반).
    """
    base, ext = os.path.splitext(name)
    candidate = os.path.join(dest_dir, name)
    n = 1
    while os.path.exists(normalize_long_path(candidate)):
        candidate = os.path.join(dest_dir, f"{base} ({n}){ext}")
        n += 1
    return candidate


def trash_files(db: Database, file_ids: list[int]) -> tuple[int, int, int]:
    """파일들을 OS 휴지통으로 보낸다. (성공, 실패, 보호됨) 반환.

    그룹(중복/유사)이 통째로 비게 되는 경우 대표/베스트샷 1장은 보호되어
    제거되지 않는다(비파괴 안전망).
    """
    protected = db.protected_survivors(file_ids)
    targets = [f for f in file_ids if f not in protected]
    batch = db.next_action_batch()
    rows: list[tuple] = []
    done: list[int] = []
    failed = 0
    for fid, path in db.paths_for_ids(targets):
        try:
            send2trash(normalize_long_path(path))
            rows.append((fid, "trash", path, None))
            done.append(fid)
        except Exception:
            failed += 1
    db.record_actions(batch, rows)
    db.mark_removed(done, 1)
    return len(done), failed, len(protected)


def quarantine_files(
    db: Database, file_ids: list[int], quarantine_dir: str
) -> tuple[int, int, int]:
    """파일들을 격리 폴더로 이동한다. (성공, 실패, 보호됨) 반환.

    그룹(중복/유사)이 통째로 비게 되는 경우 대표/베스트샷 1장은 보호되어
    제거되지 않는다(비파괴 안전망).
    """
    protected = db.protected_survivors(file_ids)
    targets = [f for f in file_ids if f not in protected]
    os.makedirs(quarantine_dir, exist_ok=True)
    batch = db.next_action_batch()
    rows: list[tuple] = []
    done: list[int] = []
    failed = 0
    for fid, path in db.paths_for_ids(targets):
        try:
            dest = unique_dest(quarantine_dir, os.path.basename(path))
            shutil.move(normalize_long_path(path), normalize_long_path(dest))
            rows.append((fid, "quarantine", path, dest))
            done.append(fid)
        except Exception:
            failed += 1
    db.record_actions(batch, rows)
    db.mark_removed(done, 1)
    return len(done), failed, len(protected)


def undo_last(db: Database) -> int:
    """되돌릴 수 있는 가장 최근 배치를 원위치로 복구한다. 복구 수 반환.

    - quarantine: 격리 폴더에서 원위치로 되돌리고 removed 플래그를 해제한다.
    - move: 날짜 정리로 옮긴 파일을 원위치로 되돌리고 DB 경로도 복원한다.
      (removed 를 건드린 적이 없으므로 플래그는 그대로 둔다.)
    휴지통 작업은 OS에서 복구해야 하므로 여기서 되돌리지 않는다.

    원위치에 그 사이 다른 파일이 생겼으면 덮어쓰지 않고 유일 이름으로 되돌린다.
    복구 결과는 청크 단위로 커밋하므로, 중간에 중단돼도 지금까지의 복구가
    DB에 남고 배치는 미완료(undone=0)로 유지돼 다시 시도할 수 있다.
    """
    batch = db.last_undoable_batch()
    if batch is None:
        return 0
    restored: list[int] = []
    path_updates: list[tuple[str, int]] = []
    count = 0

    def flush() -> None:
        db.mark_removed(restored, 0)
        db.update_paths(path_updates)
        restored.clear()
        path_updates.clear()

    try:
        for fid, action, from_path, to_path in db.actions_in_batch(batch):
            if action not in ("quarantine", "move") or not to_path:
                continue
            if not os.path.exists(normalize_long_path(to_path)):
                continue
            try:
                os.makedirs(os.path.dirname(from_path), exist_ok=True)
                target = unique_dest(
                    os.path.dirname(from_path), os.path.basename(from_path)
                )
                shutil.move(normalize_long_path(to_path), normalize_long_path(target))
            except OSError:
                continue
            count += 1
            target = to_nfc(target)
            if action == "quarantine":
                restored.append(fid)
            if action == "move" or target != to_nfc(from_path):
                path_updates.append((target, fid))
            if len(restored) + len(path_updates) >= _COMMIT_CHUNK:
                flush()
    finally:
        flush()
    db.mark_batch_undone(batch)
    return count
