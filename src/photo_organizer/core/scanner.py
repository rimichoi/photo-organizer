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
from .platform_utils import normalize_long_path, should_skip_dir


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
) -> int:
    """root 이하 이미지를 발견해 DB에 기록하고, 발견 개수를 반환한다.

    scan_sessions에 세션을 기록해 재개(Phase 5)의 토대를 만든다. 이미 기록된
    파일은 ``add_file``의 ``INSERT OR IGNORE``로 자연히 건너뛴다.
    """
    root = str(root)
    session_id = db.start_session(root)
    count = 0
    try:
        with db.batch() as conn:
            for raw_path, size, mtime, ext in _iter_image_files(root):
                db.add_file(raw_path, size, mtime, ext.lstrip("."))
                count += 1
                if count % batch_size == 0:
                    conn.commit()
                    if progress is not None:
                        progress(count)
        db.finish_session(session_id, count, status="done")
    except Exception:
        db.finish_session(session_id, count, status="error")
        raise
    if progress is not None:
        progress(count)
    return count
