"""날짜 기준 년/월 폴더 정리 — 계획 생성과 실행.

설계: docs/superpowers/specs/2026-08-06-date-organize-design.md §3.2

계획(plan_organize)과 실행(apply_organize)을 분리해 dry-run을 제공한다.
비파괴 원칙에 따라 모든 이동은 action_log에 남고 되돌릴 수 있다.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .actions import _COMMIT_CHUNK, unique_dest
from .database import Database
from .date_source import resolve_date
from .platform_utils import normalize_long_path, to_nfc

DEFAULT_UNKNOWN_DIR = "_날짜미상"


@dataclass
class PlanItem:
    """파일 한 장의 이동 계획. ``skip``이면 이미 올바른 위치에 있다."""

    file_id: int
    src: str
    dest_dir: str
    source: str  # exif | filename | unknown
    skip: bool


def plan_organize(
    db: Database, dest_root: str, unknown_dir: str = DEFAULT_UNKNOWN_DIR
) -> list[PlanItem]:
    """정리 대상 전체의 목적지를 계산한다. 파일시스템을 변경하지 않는다."""
    root = to_nfc(os.path.abspath(dest_root))
    plan: list[PlanItem] = []
    for row in db.iter_files_for_organize():
        src = row["path"]
        taken, source = resolve_date(row["exif_dt"], os.path.basename(src))
        if taken is None:
            dest_dir = os.path.join(root, unknown_dir)
        else:
            dest_dir = os.path.join(
                root, f"{taken.year:04d}", f"{taken.year:04d}-{taken.month:02d}"
            )
        current_dir = to_nfc(os.path.dirname(os.path.abspath(src)))
        plan.append(
            PlanItem(
                file_id=row["id"],
                src=src,
                dest_dir=dest_dir,
                source=source,
                skip=current_dir == to_nfc(dest_dir),
            )
        )
    return plan


def apply_organize(db: Database, plan: list[PlanItem]) -> tuple[int, int, int, int]:
    """계획대로 파일을 이동한다. (이동, 건너뜀, 실패, DB미갱신) 반환.

    quarantine_files와 같은 패턴: 배치 번호 발급 → 파일 단위 예외 격리 →
    action_log('move') 기록 → files.path 갱신. 개별 파일 오류가 전체를 막지
    않는다(NFR-03).

    10만 장 규모에서는 이 함수가 몇 시간 돌 수 있다. 진행분을 ``_COMMIT_CHUNK``
    마다 커밋하고 중단(Ctrl-C 포함) 시에도 ``finally``로 flush 하므로, 이미 옮긴
    파일은 항상 action_log에 남아 되돌릴 수 있고 재실행이 곧 재개가 된다
    (CLAUDE.md 절대원칙 2).

    마지막 ``DB미갱신``은 파일 이동은 성공했지만 path UNIQUE 충돌로 DB 경로를
    갱신하지 못한 건수다. 실패가 아니라 "재스캔이 필요한 상태"라 따로 센다.
    """
    targets = [item for item in plan if not item.skip]
    skipped = len(plan) - len(targets)
    if not targets:
        return 0, skipped, 0, 0

    batch = db.next_action_batch()
    rows: list[tuple[int, str, str, str | None]] = []
    updates: list[tuple[str, int]] = []
    created: set[str] = set()  # 이번 실행이 만들어낸 목적 경로
    moved = failed = stale = 0

    def flush() -> None:
        nonlocal moved, stale
        if not rows:
            return
        db.record_actions(batch, rows)
        stale += db.update_paths(updates)
        moved += len(updates)
        rows.clear()
        updates.clear()

    try:
        for item in targets:
            if to_nfc(os.path.abspath(item.src)) in created:
                # 앞 항목이 방금 만들어낸 파일이다. 계획이 낡았다는 뜻이므로
                # (DB에 남은 유령 행) 건드리지 않고 실패로 집계한다.
                failed += 1
                continue
            try:
                os.makedirs(normalize_long_path(item.dest_dir), exist_ok=True)
                dest = unique_dest(item.dest_dir, os.path.basename(item.src))
                shutil.move(normalize_long_path(item.src), normalize_long_path(dest))
            except OSError:
                failed += 1
                continue
            dest = to_nfc(dest)
            created.add(to_nfc(os.path.abspath(dest)))
            rows.append((item.file_id, "move", item.src, dest))
            updates.append((dest, item.file_id))
            if len(rows) >= _COMMIT_CHUNK:
                flush()
    finally:
        flush()
    return moved, skipped, failed, stale
