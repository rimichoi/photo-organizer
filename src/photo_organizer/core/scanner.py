"""디렉토리 스캐너 — 재귀 walk로 이미지 파일을 발견해 DB에 기록한다.

docs/SPEC.md 4.3 [1] 디스커버리 단계:
- 빠르고 I/O를 최소화한다. 이미지를 디코딩하지 않고 경로/크기/mtime만 기록.
- ``os.scandir`` 기반(스택 방식)으로 깊은 트리에서도 재귀 한계 없이 순회하고,
  ``scandir``가 캐시한 stat을 재활용해 네트워크 왕복을 줄인다.
- 개별 파일/디렉토리 오류(권한·끊김)는 건너뛰고 전체는 계속한다(NFR-03).
- 긴 경로/시스템 폴더 처리는 platform_utils에 위임(Windows/macOS 공통).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterator, Optional

from .database import Database
from .image_loader import SUPPORTED_EXTS
from .platform_utils import normalize_long_path, should_skip_dir, to_nfc


def _iter_image_files(root: str) -> Iterator[tuple[str, int, float, str]]:
    """root 이하를 순회하며 (원본경로, 크기, mtime, 확장자)를 yield 한다.

    - 심볼릭 링크는 따라가지 않아 링크 루프를 막는다(follow_symlinks=False).
    - scandir는 긴 경로 지원을 위해 정규화된 경로로 열지만, yield 하는 경로는
      접두어 없는 원본(join으로 구성)이라 DB에 깔끔하게 저장된다.
    """
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()  # 접두어 없는 원본 디렉토리 경로
        try:
            it = os.scandir(normalize_long_path(current))
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            continue
        with it:
            for entry in it:
                raw_path = os.path.join(current, entry.name)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not should_skip_dir(entry.name):
                            stack.append(raw_path)
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in SUPPORTED_EXTS:
                            st = entry.stat(follow_symlinks=False)
                            yield raw_path, st.st_size, st.st_mtime, ext
                except OSError:
                    # 개별 엔트리 stat 실패 등은 건너뛴다.
                    continue


def scan_directory(
    db: Database,
    root: str | Path,
    batch_size: int = 500,
    progress: Optional[Callable[[int], None]] = None,
    detect_deletions: bool = False,
) -> dict:
    """root 이하 이미지를 발견해 DB에 upsert 하고 요약 dict 를 반환한다.

    반환: {"new", "updated", "unchanged", "deleted"}. ``deleted`` 는 감지
    미수행 또는 안전 가드 발동(빈 walk) 시 ``None``.

    발견된 경로는 DB 저장 전 NFC로 정규화한다(macOS NFD와 Windows/NAS NFC의
    차이로 인한 중복 기록·증분 재스캔/삭제 감지 오판을 막기 위함).

    scan_sessions에 세션을 기록해 재개(Phase 5)의 토대를 만든다. 발견된 각
    파일은 ``add_file``이 upsert 하여 신규는 추가하고, 변경된 파일은 파생
    데이터(중복/유사/베스트샷 등)를 무효화하며, 무변경 재발견은 그대로
    지나가되 이전에 missing 이었다면 복원한다. 이 반환값의 new/updated/
    unchanged 는 ``add_file``의 반환("new"/"updated"/"unchanged")을 누적한
    것이다.

    ``detect_deletions=True`` 이면 이번 walk에서 발견된 경로 집합과
    ``paths_under_root(root)``(DB상 root 접두어 하위, removed=0 AND missing=0 인
    파일만)를 대조해, walk에서 발견되지 않은 파일을 ``mark_missing``으로
    표시한다. 이미 격리/휴지통으로 정리되었거나 이미 missing 인 파일은 대조
    대상에서 제외되어, 격리 파일이 missing 으로 오염되거나 유령 삭제가
    재스캔마다 반복 카운트되는 것을 막는다. 단, walk 결과가 0개인
    경우(드라이브 언마운트/네트워크 두절 등으로 root 자체에 접근할 수 없는
    상황과 구분이 어려움) 안전 가드가 발동해 삭제 감지를 보류하고
    ``deleted=None`` 을 반환한다(파일들을 missing 잘못 표시하는 것을 막는다).
    """
    root = to_nfc(str(root))
    session_id = db.start_session(root)
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    seen: set[str] = set()
    processed = 0
    try:
        with db.batch() as conn:
            for raw_path, size, mtime, ext in _iter_image_files(root):
                raw_path = to_nfc(raw_path)
                status = db.add_file(raw_path, size, mtime, ext.lstrip("."))
                counts[status] += 1
                seen.add(raw_path)
                processed += 1
                if processed % batch_size == 0:
                    conn.commit()
                    if progress is not None:
                        progress(processed)
        deleted: int | None = None
        if detect_deletions:
            if not seen:
                # 안전 가드: 빈 walk(언마운트/접근불가) → 삭제 보류.
                deleted = None
            else:
                known = db.paths_under_root(root)
                gone = [fid for fid, p in known if p not in seen]
                db.mark_missing(gone)
                deleted = len(gone)
        db.finish_session(session_id, processed, status="done")
    except Exception:
        db.finish_session(session_id, processed, status="error")
        raise
    if progress is not None:
        progress(processed)
    counts["deleted"] = deleted
    return counts
