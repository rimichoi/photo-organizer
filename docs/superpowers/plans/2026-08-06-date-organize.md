# 날짜 기준 년/월 폴더 정리 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `analyze`가 끝난 라이브러리의 사진을 촬영 날짜 기준으로 `<dest>/YYYY/YYYY-MM/` 폴더에 이동시키는 CLI `organize` 명령을 만든다.

**Architecture:** 날짜 판단은 파일 I/O 없는 순수 함수(`core/date_source.py`)로 분리해 촘촘히 테스트하고, 이동은 검증된 `quarantine_files` 패턴(배치 → 파일 단위 예외 격리 → `action_log` 기록)을 따르는 `core/organize.py`가 담당한다. 계획(`plan_organize`)과 실행(`apply_organize`)을 분리해 dry-run을 공짜로 얻고, `move` 액션을 기존 되돌리기 경로에 편입시켜 GUI 변경 없이 undo가 동작하게 한다.

**Tech Stack:** Python 3.11+ / SQLite(WAL) / pytest. 새 의존성 없음.

**설계 문서:** `docs/superpowers/specs/2026-08-06-date-organize-design.md`

## Global Constraints

- 실행은 **반드시 `.venv/bin/python`** (시스템 Python 3.9 금지). 테스트: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` (`tests/conftest.py`가 `src`를 `sys.path`에 넣으므로 `PYTHONPATH` 불필요).
- 모든 새 모듈은 `from __future__ import annotations` 로 시작한다 (배포 타깃 3.11, 개발 3.14).
- **비파괴성**: 자동 완전삭제 없음. 모든 이동은 `action_log`에 기록하고 되돌릴 수 있어야 한다.
- **파일 단위 예외 격리**(NFR-03): 한 파일의 실패가 전체 작업을 중단시키지 않는다.
- DB에 저장하는 경로 문자열은 항상 `platform_utils.to_nfc()`로 NFC 정규화한다. 파일시스템 접근 시에는 `platform_utils.normalize_long_path()`를 통과시킨다.
- 주석·독스트링은 한국어, 기존 모듈 스타일(요약 1줄 + 빈 줄 + 상세)을 따른다.
- 각 태스크 커밋 후 `git push origin main` 한다(이 저장소의 기존 관행).
- 테스트에서 `sample_photos`를 직접 수정하지 않는다. 이동·삭제가 있는 테스트는 `tmp_path`에만 파일을 만든다.

---

### Task 1: 날짜 결정 순수 함수 (`core/date_source.py`)

**Files:**
- Create: `src/photo_organizer/core/date_source.py`
- Test: `tests/test_date_source.py`

**Interfaces:**
- Consumes: 없음 (순수 함수, 표준 라이브러리만)
- Produces:
  - `resolve_date(exif_dt: float | None, filename: str) -> tuple[date | None, str]`
  - `date_from_filename(name: str) -> date | None`
  - 상수 `SOURCE_EXIF = "exif"`, `SOURCE_FILENAME = "filename"`, `SOURCE_UNKNOWN = "unknown"`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_date_source.py`:

```python
"""촬영 날짜 결정 테스트 (spec §3.1): EXIF 우선 → 파일명 → 미상."""
from datetime import date, datetime, timedelta

import pytest

from photo_organizer.core.date_source import (
    SOURCE_EXIF,
    SOURCE_FILENAME,
    SOURCE_UNKNOWN,
    date_from_filename,
    resolve_date,
)


def _epoch(y: int, m: int, d: int) -> float:
    return datetime(y, m, d, 12, 0, 0).timestamp()


def test_exif_wins_over_filename():
    # exif_dt가 있으면 파일명 날짜(2020-01-01)는 무시한다.
    got, source = resolve_date(_epoch(2023, 4, 12), "IMG_20200101_090000.jpg")
    assert got == date(2023, 4, 12)
    assert source == SOURCE_EXIF


@pytest.mark.parametrize(
    "name",
    [
        "IMG_20230412_183000.jpg",       # 안드로이드/카메라
        "VID_20230412_183000.mp4",
        "PXL_20230412_183000123.jpg",    # 픽셀
        "KakaoTalk_20230412_183000.jpg", # 카카오톡
        "IMG-20230412-WA0001.jpg",       # 왓츠앱
        "Screenshot 2023-04-12 at 18.30.00.png",  # macOS/iOS 스크린샷
        "2023-04-12 18.30.00.jpg",
        "20230412_183000.jpg",
        "2023_04_12.jpg",
        "2023.04.12.jpg",
    ],
)
def test_filename_patterns(name):
    assert date_from_filename(name) == date(2023, 4, 12)


def test_filename_used_when_no_exif():
    got, source = resolve_date(None, "IMG_20230412_183000.jpg")
    assert got == date(2023, 4, 12)
    assert source == SOURCE_FILENAME


@pytest.mark.parametrize(
    "name",
    [
        "12345678901234.jpg",   # 숫자 경계 — 긴 일련번호 안의 8자리는 무시
        "20231345_1200.jpg",    # 13월 — 유효성 탈락
        "20230230_1200.jpg",    # 2월 30일 — 유효성 탈락
        "18990101_1200.jpg",    # 범위 밖(1990 이전)
        "DSC_0001.jpg",         # 날짜 없음
        "2023-0412.jpg",        # 구분자 불일치
    ],
)
def test_no_false_positives(name):
    assert date_from_filename(name) is None


def test_future_date_rejected():
    future = date.today() + timedelta(days=30)
    assert date_from_filename(f"IMG_{future:%Y%m%d}_120000.jpg") is None


def test_unknown_when_nothing_available():
    got, source = resolve_date(None, "DSC_0001.jpg")
    assert got is None
    assert source == SOURCE_UNKNOWN


def test_first_valid_match_wins():
    # 앞쪽 후보가 무효(13월)면 뒤쪽 유효 후보를 쓴다.
    assert date_from_filename("20231345_20230412.jpg") == date(2023, 4, 12)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_date_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'photo_organizer.core.date_source'`

- [ ] **Step 3: 구현 작성**

`src/photo_organizer/core/date_source.py`:

```python
"""촬영 날짜 결정 — EXIF 우선, 없으면 파일명, 그것도 없으면 미상.

설계: docs/superpowers/specs/2026-08-06-date-organize-design.md §3.1

파일을 열지 않는 순수 함수만 둔다. EXIF 읽기는 analyze 단계가 이미 끝냈고
(`files.exif_dt`), 여기서는 그 값과 파일명만 보고 날짜를 정한다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

SOURCE_EXIF = "exif"
SOURCE_FILENAME = "filename"
SOURCE_UNKNOWN = "unknown"

# YYYY[구분자]MM[구분자]DD. 구분자는 없음/-/_/. 를 허용하되 앞뒤가 같아야 한다
# (`2023-0412` 같은 혼합 형태 차단). 앞뒤 숫자 경계를 막아 긴 일련번호 안의
# 8자리가 날짜로 둔갑하지 않게 한다. 월/일의 실제 유효성은 date()가 검증한다.
_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})([-_.]?)(\d{2})\2(\d{2})(?!\d)")

# 디지털 사진 이전 연도나 epoch 숫자·해상도 문자열을 걸러내는 하한.
_MIN_DATE = date(1990, 1, 1)


def date_from_filename(name: str) -> date | None:
    """파일명에서 촬영 날짜를 추정한다. 찾지 못하면 ``None``.

    확장자를 뗀 stem만 검사하며, 유효하고 범위 안에 드는 **첫 매치**를 쓴다.
    """
    stem = Path(name).stem
    upper = date.today() + timedelta(days=1)
    for m in _DATE_RE.finditer(stem):
        try:
            candidate = date(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            continue  # 13월·2월 30일 등
        if _MIN_DATE <= candidate <= upper:
            return candidate
    return None


def resolve_date(exif_dt: float | None, filename: str) -> tuple[date | None, str]:
    """(날짜, 출처)를 반환한다. 출처는 exif/filename/unknown.

    ``exif_dt``는 analyze가 기록한 epoch초다. parse_exif_datetime이 EXIF의
    로컬 시간 문자열을 로컬 타임존 기준으로 epoch화했으므로, 로컬 시간으로
    되돌리면 원래 촬영 시각이 그대로 복원된다.
    """
    if exif_dt is not None:
        try:
            return datetime.fromtimestamp(exif_dt).date(), SOURCE_EXIF
        except (OverflowError, OSError, ValueError):
            pass  # 손상된 값 → 파일명으로 폴백
    from_name = date_from_filename(filename)
    if from_name is not None:
        return from_name, SOURCE_FILENAME
    return None, SOURCE_UNKNOWN
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_date_source.py -q`
Expected: PASS (21 passed — parametrize 16 + 단일 5)

- [ ] **Step 5: 커밋**

```bash
git add src/photo_organizer/core/date_source.py tests/test_date_source.py
git commit -m "feat(date): EXIF·파일명 기반 촬영 날짜 결정 함수"
git push origin main
```

---

### Task 2: DB 확장 — 정리 대상 조회와 경로 갱신

**Files:**
- Modify: `src/photo_organizer/core/database.py` (`paths_for_ids` 뒤, `next_action_batch` 앞에 두 메서드 추가)
- Test: `tests/test_organize_db.py`

**Interfaces:**
- Consumes: 기존 `Database.batch()` 컨텍스트 매니저 (`database.py:520`)
- Produces:
  - `Database.iter_files_for_organize()` → `sqlite3.Cursor` (행: `id`, `path`, `exif_dt`)
  - `Database.update_paths(rows: list[tuple[str, int]]) -> int` — `(new_path, file_id)` 목록을 반영하고 **UNIQUE 충돌로 갱신하지 못한 건수**를 반환

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_organize_db.py`:

```python
"""날짜 정리용 DB 확장 테스트 (spec §3.3)."""
from photo_organizer.core.database import Database


def _add(db: Database, path: str, exif_dt: float | None = None) -> int:
    db.add_file(path, 4, 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if exif_dt is not None:
        db.set_exif_dates([(exif_dt, fid)])
    db.conn.commit()
    return fid


def test_iter_files_for_organize_excludes_removed_and_missing(tmp_path):
    db = Database(tmp_path / "t.db")
    keep = _add(db, "/photos/keep.jpg", 1_700_000_000.0)
    gone = _add(db, "/photos/gone.jpg")
    trashed = _add(db, "/photos/trashed.jpg")
    db.mark_missing([gone], 1)
    db.mark_removed([trashed], 1)

    rows = list(db.iter_files_for_organize())
    assert [r["id"] for r in rows] == [keep]
    assert rows[0]["path"] == "/photos/keep.jpg"
    assert rows[0]["exif_dt"] == 1_700_000_000.0
    db.close()


def test_update_paths_moves_row(tmp_path):
    db = Database(tmp_path / "t.db")
    fid = _add(db, "/photos/a.jpg")

    conflicts = db.update_paths([("/sorted/2023/2023-04/a.jpg", fid)])

    assert conflicts == 0
    row = db.conn.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()
    assert row["path"] == "/sorted/2023/2023-04/a.jpg"
    db.close()


def test_update_paths_replaces_ghost_row(tmp_path):
    # 목적 경로에 과거 스캔의 유령(missing) 행이 있으면 그 행을 치우고 갱신한다.
    db = Database(tmp_path / "t.db")
    ghost = _add(db, "/sorted/2023/2023-04/a.jpg")
    db.mark_missing([ghost], 1)
    fid = _add(db, "/photos/a.jpg")

    conflicts = db.update_paths([("/sorted/2023/2023-04/a.jpg", fid)])

    assert conflicts == 0
    rows = db.conn.execute("SELECT id FROM files WHERE path=?",
                           ("/sorted/2023/2023-04/a.jpg",)).fetchall()
    assert [r["id"] for r in rows] == [fid]
    assert db.conn.execute("SELECT 1 FROM files WHERE id=?", (ghost,)).fetchone() is None
    db.close()


def test_update_paths_reports_live_conflict(tmp_path):
    # 목적 경로에 살아있는 행이 있으면 갱신을 포기하고 충돌로 집계한다.
    db = Database(tmp_path / "t.db")
    live = _add(db, "/sorted/2023/2023-04/a.jpg")
    fid = _add(db, "/photos/a.jpg")

    conflicts = db.update_paths([("/sorted/2023/2023-04/a.jpg", fid)])

    assert conflicts == 1
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == "/photos/a.jpg"
    assert db.conn.execute("SELECT 1 FROM files WHERE id=?", (live,)).fetchone() is not None
    db.close()


def test_update_paths_empty_is_noop(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.update_paths([]) == 0
    db.close()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_db.py -q`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'iter_files_for_organize'`

(테스트가 쓰는 `set_exif_dates`는 `database.py:356`에 이미 있는 메서드다.)

- [ ] **Step 3: 구현 작성**

`src/photo_organizer/core/database.py`의 `paths_for_ids` 메서드(`:238`) 바로 뒤에 추가:

```python
    def iter_files_for_organize(self):
        """날짜 정리 대상 — 사용자 정리(removed)·외부 삭제(missing)분 제외."""
        return self.conn.execute(
            "SELECT id, path, exif_dt FROM files WHERE removed=0 AND missing=0"
        )

    def update_paths(self, rows: list[tuple[str, int]]) -> int:
        """rows: (new_path, file_id). 이동 후 경로를 갱신하고 충돌 건수를 반환.

        path 는 UNIQUE 이므로 목적 경로가 과거 스캔의 다른 행과 겹칠 수 있다.
        - 충돌 행이 missing(유령) → 그 행과 의존 행을 지우고 갱신한다.
        - 충돌 행이 살아있음 → 갱신을 포기하고 충돌로 집계한다. 파일은 이미
          이동했으므로 다음 재스캔이 정리한다.
        """
        if not rows:
            return 0
        conflicts = 0
        with self.batch() as conn:
            for new_path, fid in rows:
                try:
                    conn.execute("UPDATE files SET path=? WHERE id=?", (new_path, fid))
                    continue
                except sqlite3.IntegrityError:
                    pass
                other = conn.execute(
                    "SELECT id, missing FROM files WHERE path=?", (new_path,)
                ).fetchone()
                if other is None or not other["missing"]:
                    conflicts += 1
                    continue
                rid = other["id"]
                conn.execute("DELETE FROM duplicate_groups WHERE file_id=?", (rid,))
                conn.execute("DELETE FROM similar_groups WHERE file_id=?", (rid,))
                conn.execute("DELETE FROM action_log WHERE file_id=?", (rid,))
                conn.execute("DELETE FROM files WHERE id=?", (rid,))
                conn.execute("UPDATE files SET path=? WHERE id=?", (new_path, fid))
        return conflicts
```

`sqlite3`는 `database.py` 상단에서 이미 import 되어 있다(`_migrate`가 `sqlite3.IntegrityError`를 사용). 아니라면 추가한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_db.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/photo_organizer/core/database.py tests/test_organize_db.py
git commit -m "feat(db): 정리 대상 조회·이동 후 경로 갱신 메서드"
git push origin main
```

---

### Task 3: 이동 계획 생성 (`core/organize.py` — `plan_organize`)

**Files:**
- Create: `src/photo_organizer/core/organize.py`
- Test: `tests/test_organize_plan.py`

**Interfaces:**
- Consumes: `date_source.resolve_date`, `Database.iter_files_for_organize`, `platform_utils.to_nfc`
- Produces:
  - `PlanItem` 데이터클래스: `file_id: int`, `src: str`, `dest_dir: str`, `source: str`, `skip: bool`
  - `plan_organize(db, dest_root: str, unknown_dir: str = DEFAULT_UNKNOWN_DIR) -> list[PlanItem]`
  - 상수 `DEFAULT_UNKNOWN_DIR = "_날짜미상"`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_organize_plan.py`:

```python
"""이동 계획 생성 테스트 (spec §3.2)."""
import os
from datetime import datetime

from photo_organizer.core.database import Database
from photo_organizer.core.organize import DEFAULT_UNKNOWN_DIR, plan_organize


def _add(db: Database, path: str, exif_dt: float | None = None) -> int:
    db.add_file(path, 4, 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if exif_dt is not None:
        db.set_exif_dates([(exif_dt, fid)])
    db.conn.commit()
    return fid


def _by_src(plan):
    return {os.path.basename(p.src): p for p in plan}


def test_plan_uses_exif_then_filename_then_unknown(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _add(db, "/photos/exif.jpg", datetime(2023, 4, 12, 10, 0, 0).timestamp())
    _add(db, "/photos/IMG_20220105_090000.jpg")
    _add(db, "/photos/DSC_0001.jpg")

    plan = _by_src(plan_organize(db, dest))

    assert plan["exif.jpg"].dest_dir == os.path.join(dest, "2023", "2023-04")
    assert plan["exif.jpg"].source == "exif"
    assert plan["IMG_20220105_090000.jpg"].dest_dir == os.path.join(dest, "2022", "2022-01")
    assert plan["IMG_20220105_090000.jpg"].source == "filename"
    assert plan["DSC_0001.jpg"].dest_dir == os.path.join(dest, DEFAULT_UNKNOWN_DIR)
    assert plan["DSC_0001.jpg"].source == "unknown"
    db.close()


def test_plan_marks_already_sorted_as_skip(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    already = os.path.join(dest, "2023", "2023-04", "a.jpg")
    _add(db, already, datetime(2023, 4, 12, 10, 0, 0).timestamp())

    (item,) = plan_organize(db, dest)

    assert item.skip is True
    db.close()


def test_plan_honors_custom_unknown_dir(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _add(db, "/photos/DSC_0001.jpg")

    (item,) = plan_organize(db, dest, unknown_dir="_nodate")

    assert item.dest_dir == os.path.join(dest, "_nodate")
    db.close()


def test_plan_excludes_removed(tmp_path):
    db = Database(tmp_path / "t.db")
    fid = _add(db, "/photos/a.jpg")
    db.mark_removed([fid], 1)

    assert plan_organize(db, str(tmp_path / "sorted")) == []
    db.close()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_plan.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'photo_organizer.core.organize'`

- [ ] **Step 3: 구현 작성**

`src/photo_organizer/core/organize.py`:

```python
"""날짜 기준 년/월 폴더 정리 — 계획 생성과 실행.

설계: docs/superpowers/specs/2026-08-06-date-organize-design.md §3.2

계획(plan_organize)과 실행(apply_organize)을 분리해 dry-run을 제공한다.
비파괴 원칙에 따라 모든 이동은 action_log에 남고 되돌릴 수 있다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .database import Database
from .date_source import resolve_date
from .platform_utils import to_nfc

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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_plan.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/photo_organizer/core/organize.py tests/test_organize_plan.py
git commit -m "feat(organize): 날짜 기준 이동 계획 생성"
git push origin main
```

---

### Task 4: 이동 실행 (`apply_organize`) + `unique_dest` 공유

**Files:**
- Modify: `src/photo_organizer/core/actions.py:21` (`_unique_dest` → `unique_dest` rename), `:73` (호출부)
- Modify: `tests/test_actions.py:5`, `:74` (rename 반영)
- Modify: `src/photo_organizer/core/organize.py` (`apply_organize` 추가)
- Test: `tests/test_organize_apply.py`

**Interfaces:**
- Consumes: `PlanItem`(Task 3), `Database.update_paths`(Task 2), `Database.next_action_batch`/`record_actions`(기존), `platform_utils.normalize_long_path`/`to_nfc`
- Produces:
  - `actions.unique_dest(dest_dir: str, name: str) -> str` (기존 `_unique_dest`의 새 이름)
  - `organize.apply_organize(db, plan: list[PlanItem]) -> tuple[int, int, int]` — `(moved, skipped, failed)`

- [ ] **Step 1: rename부터 처리 (테스트 먼저 깨뜨리고 고치기)**

`actions.py`에서 `_unique_dest` → `unique_dest`로 이름을 바꾸고(정의 `:21`, 호출 `:73`), 독스트링을 "격리 폴더에서" → "대상 폴더에서"로 일반화한다. `tests/test_actions.py`의 import(`:5`)와 호출(`:74`)도 새 이름으로 바꾼다.

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_actions.py -q`
Expected: PASS (기존 테스트 전부)

```bash
git add src/photo_organizer/core/actions.py tests/test_actions.py
git commit -m "refactor(actions): _unique_dest를 unique_dest로 공개"
git push origin main
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_organize_apply.py`:

```python
"""이동 실행 테스트 (spec §3.2, §4). 파일은 tmp_path에만 만든다."""
import os
from datetime import datetime

from photo_organizer.core.database import Database
from photo_organizer.core.organize import apply_organize, plan_organize

TAKEN = datetime(2023, 4, 12, 10, 0, 0).timestamp()


def _make(db: Database, path: str, data: bytes = b"data", exif_dt=TAKEN) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    db.add_file(path, len(data), 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if exif_dt is not None:
        db.set_exif_dates([(exif_dt, fid)])
    db.conn.commit()
    return fid


def test_apply_moves_file_and_updates_db(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    src = str(tmp_path / "src" / "a.jpg")
    fid = _make(db, src)

    moved, skipped, failed = apply_organize(db, plan_organize(db, dest))

    expected = os.path.join(dest, "2023", "2023-04", "a.jpg")
    assert (moved, skipped, failed) == (1, 0, 0)
    assert os.path.exists(expected)
    assert not os.path.exists(src)
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == expected
    log = db.conn.execute("SELECT action, from_path, to_path FROM action_log").fetchone()
    assert log["action"] == "move"
    assert log["from_path"] == src
    assert log["to_path"] == expected
    db.close()


def test_plan_alone_does_not_touch_disk(tmp_path):
    db = Database(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    _make(db, src)

    plan_organize(db, str(tmp_path / "sorted"))

    assert os.path.exists(src)
    assert not os.path.exists(tmp_path / "sorted")
    db.close()


def test_name_collision_gets_suffix(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _make(db, str(tmp_path / "src1" / "a.jpg"), b"one")
    _make(db, str(tmp_path / "src2" / "a.jpg"), b"two")

    moved, _, failed = apply_organize(db, plan_organize(db, dest))

    month = os.path.join(dest, "2023", "2023-04")
    assert (moved, failed) == (2, 0)
    assert sorted(os.listdir(month)) == ["a (1).jpg", "a.jpg"]
    db.close()


def test_rerun_is_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _make(db, str(tmp_path / "src" / "a.jpg"))
    apply_organize(db, plan_organize(db, dest))

    moved, skipped, failed = apply_organize(db, plan_organize(db, dest))

    assert (moved, skipped, failed) == (0, 1, 0)
    month = os.path.join(dest, "2023", "2023-04")
    assert os.listdir(month) == ["a.jpg"]
    db.close()


def test_missing_source_counts_as_failed(tmp_path):
    # 계획을 세운 뒤 원본이 사라져도 나머지 파일 처리는 계속된다(NFR-03).
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    doomed = str(tmp_path / "src" / "gone.jpg")
    _make(db, doomed)
    _make(db, str(tmp_path / "src" / "ok.jpg"))
    plan = plan_organize(db, dest)
    os.remove(doomed)

    moved, _, failed = apply_organize(db, plan)

    assert (moved, failed) == (1, 1)
    assert os.path.exists(os.path.join(dest, "2023", "2023-04", "ok.jpg"))
    db.close()
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_apply.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_organize'`

- [ ] **Step 4: 구현 작성**

`src/photo_organizer/core/organize.py`의 import에 추가:

```python
import shutil

from .actions import unique_dest
from .platform_utils import normalize_long_path, to_nfc
```

파일 끝에 추가:

```python
def apply_organize(db: Database, plan: list[PlanItem]) -> tuple[int, int, int]:
    """계획대로 파일을 이동한다. (이동, 건너뜀, 실패) 반환.

    quarantine_files와 같은 패턴: 배치 번호 발급 → 파일 단위 예외 격리 →
    action_log('move') 기록 → files.path 갱신. 개별 파일 오류가 전체를 막지
    않는다(NFR-03).
    """
    targets = [item for item in plan if not item.skip]
    skipped = len(plan) - len(targets)
    if not targets:
        return 0, skipped, 0

    batch = db.next_action_batch()
    rows: list[tuple[int, str, str, str | None]] = []
    updates: list[tuple[str, int]] = []
    failed = 0
    for item in targets:
        try:
            os.makedirs(normalize_long_path(item.dest_dir), exist_ok=True)
            dest = unique_dest(item.dest_dir, os.path.basename(item.src))
            shutil.move(normalize_long_path(item.src), normalize_long_path(dest))
        except OSError:
            failed += 1
            continue
        dest = to_nfc(dest)
        rows.append((item.file_id, "move", item.src, dest))
        updates.append((dest, item.file_id))

    db.record_actions(batch, rows)
    conflicts = db.update_paths(updates)
    return len(updates) - conflicts, skipped, failed + conflicts
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_apply.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: 커밋**

```bash
git add src/photo_organizer/core/organize.py tests/test_organize_apply.py
git commit -m "feat(organize): 년/월 폴더로 안전 이동 실행"
git push origin main
```

---

### Task 5: `move` 되돌리기 지원

**Files:**
- Modify: `src/photo_organizer/core/database.py:307-313` (`last_undoable_batch`)
- Modify: `src/photo_organizer/core/actions.py:84-110` (`undo_last`)
- Test: `tests/test_organize_undo.py`

**Interfaces:**
- Consumes: `Database.update_paths`(Task 2), `organize.apply_organize`(Task 4)
- Produces: 동작 변경만 — `undo_last`가 `move` 배치도 복구하며, 복구한 파일 수(quarantine + move)를 반환한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_organize_undo.py`:

```python
"""날짜 정리 되돌리기 테스트 (spec §3.4)."""
import os
from datetime import datetime

from photo_organizer.core import actions
from photo_organizer.core.database import Database
from photo_organizer.core.organize import apply_organize, plan_organize

TAKEN = datetime(2023, 4, 12, 10, 0, 0).timestamp()


def _make(db: Database, path: str) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"data")
    db.add_file(path, 4, 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    db.set_exif_dates([(TAKEN, fid)])
    db.conn.commit()
    return fid


def test_undo_restores_file_and_db_path(tmp_path):
    db = Database(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    fid = _make(db, src)
    apply_organize(db, plan_organize(db, str(tmp_path / "sorted")))

    restored = actions.undo_last(db)

    assert restored == 1
    assert os.path.exists(src)
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == src
    assert db.conn.execute("SELECT undone FROM action_log").fetchone()["undone"] == 1
    db.close()


def test_undone_file_stays_visible(tmp_path):
    # move는 removed를 건드리지 않으므로 되돌린 뒤에도 뷰에서 보여야 한다.
    db = Database(tmp_path / "t.db")
    fid = _make(db, str(tmp_path / "src" / "a.jpg"))
    apply_organize(db, plan_organize(db, str(tmp_path / "sorted")))
    actions.undo_last(db)

    row = db.conn.execute("SELECT removed FROM files WHERE id=?", (fid,)).fetchone()
    assert row["removed"] == 0
    db.close()


def test_undo_is_noop_without_batches(tmp_path):
    db = Database(tmp_path / "t.db")
    assert actions.undo_last(db) == 0
    db.close()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_undo.py -q`
Expected: FAIL — `test_undo_restores_file_and_db_path`에서 `restored == 0` (현재 `last_undoable_batch`가 `quarantine`만 찾음)

- [ ] **Step 3: 구현 작성**

`database.py:307`의 `last_undoable_batch`를 교체:

```python
    def last_undoable_batch(self) -> int | None:
        """되돌릴 수 있는 가장 최근 배치(격리 이동·날짜 정리). 휴지통은 OS에서 복구."""
        row = self.conn.execute(
            "SELECT MAX(batch) AS b FROM action_log "
            "WHERE undone=0 AND action IN ('quarantine', 'move')"
        ).fetchone()
        return row["b"]
```

`actions.py`의 `undo_last`를 교체:

```python
def undo_last(db: Database) -> int:
    """되돌릴 수 있는 가장 최근 배치를 원위치로 복구한다. 복구 수 반환.

    - quarantine: 격리 폴더에서 원위치로 되돌리고 removed 플래그를 해제한다.
    - move: 날짜 정리로 옮긴 파일을 원위치로 되돌리고 DB 경로도 복원한다.
      (removed 를 건드린 적이 없으므로 플래그는 그대로 둔다.)
    휴지통 작업은 OS에서 복구해야 하므로 여기서 되돌리지 않는다.
    """
    batch = db.last_undoable_batch()
    if batch is None:
        return 0
    restored: list[int] = []
    moved_back: list[tuple[str, int]] = []
    for fid, action, from_path, to_path in db.actions_in_batch(batch):
        if action not in ("quarantine", "move") or not to_path:
            continue
        if not os.path.exists(normalize_long_path(to_path)):
            continue
        try:
            os.makedirs(os.path.dirname(from_path), exist_ok=True)
            shutil.move(normalize_long_path(to_path), normalize_long_path(from_path))
        except OSError:
            continue
        if action == "quarantine":
            restored.append(fid)
        else:
            moved_back.append((to_nfc(from_path), fid))
    db.mark_removed(restored, 0)
    db.update_paths(moved_back)
    db.mark_batch_undone(batch)
    return len(restored) + len(moved_back)
```

`actions.py`의 import를 `from .platform_utils import normalize_long_path, to_nfc` 로 확장한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_organize_undo.py tests/test_actions.py -q`
Expected: PASS (신규 3 + 기존 test_actions 전부)

- [ ] **Step 5: 커밋**

```bash
git add src/photo_organizer/core/actions.py src/photo_organizer/core/database.py tests/test_organize_undo.py
git commit -m "feat(actions): 날짜 정리(move) 되돌리기 지원"
git push origin main
```

---

### Task 6: CLI `organize` 서브커맨드

**Files:**
- Modify: `src/photo_organizer/cli.py` (모듈 독스트링 사용 예 추가, `_cmd_organize`·`_print_organize_preview` 추가, `build_parser`에 서브파서 등록)
- Test: `tests/test_cli_organize.py`

**Interfaces:**
- Consumes: `organize.plan_organize`/`apply_organize`/`DEFAULT_UNKNOWN_DIR`(Task 3·4)
- Produces: `photo-organizer-cli --db <db> organize --dest <경로> [--apply] [--unknown-dir <이름>]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli_organize.py`:

```python
"""CLI organize 테스트 — 기본 dry-run, --apply 시 실제 이동."""
import os
from datetime import datetime

from photo_organizer.cli import main
from photo_organizer.core.database import Database

TAKEN = datetime(2023, 4, 12, 10, 0, 0).timestamp()


def _seed(db_path, src) -> None:
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as fh:
        fh.write(b"data")
    with Database(db_path) as db:
        db.add_file(src, 4, 0.0, "jpg")
        fid = db.conn.execute("SELECT id FROM files WHERE path=?", (src,)).fetchone()["id"]
        db.set_exif_dates([(TAKEN, fid)])


def test_dry_run_reports_without_moving(tmp_path, capsys):
    db_path = str(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    dest = str(tmp_path / "sorted")
    _seed(db_path, src)

    rc = main(["--db", db_path, "organize", "--dest", dest])

    out = capsys.readouterr().out
    assert rc == 0
    assert os.path.exists(src)
    assert not os.path.exists(dest)
    assert "exif 1" in out
    assert "2023-04" in out
    assert "--apply" in out


def test_apply_moves_files(tmp_path, capsys):
    db_path = str(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    dest = str(tmp_path / "sorted")
    _seed(db_path, src)

    rc = main(["--db", db_path, "organize", "--dest", dest, "--apply"])

    assert rc == 0
    assert os.path.exists(os.path.join(dest, "2023", "2023-04", "a.jpg"))
    assert not os.path.exists(src)
    assert "이동 1" in capsys.readouterr().out


def test_unknown_dir_option(tmp_path):
    db_path = str(tmp_path / "t.db")
    src = str(tmp_path / "src" / "DSC_0001.jpg")
    dest = str(tmp_path / "sorted")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as fh:
        fh.write(b"data")
    with Database(db_path) as db:
        db.add_file(src, 4, 0.0, "jpg")

    main(["--db", db_path, "organize", "--dest", dest, "--unknown-dir", "_nodate", "--apply"])

    assert os.path.exists(os.path.join(dest, "_nodate", "DSC_0001.jpg"))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cli_organize.py -q`
Expected: FAIL — `argparse` 에러 `invalid choice: 'organize'` (SystemExit 2)

- [ ] **Step 3: 구현 작성**

`cli.py` 상단 import에 추가:

```python
from collections import Counter

from .core.organize import DEFAULT_UNKNOWN_DIR, apply_organize, plan_organize
```

모듈 독스트링의 사용 예에 한 줄 추가:

```
    photo-organizer-cli --db lib.db organize --dest "/Volumes/NAS/정리됨" --apply
```

`_cmd_report` 뒤(=`build_parser` 앞)에 추가:

```python
_PREVIEW_MONTHS = 12   # 미리보기에 나열할 년월 폴더 최대 개수
_PREVIEW_SAMPLES = 10  # 미리보기에 나열할 이동 예시 최대 개수


def _print_organize_preview(plan: list, dest: str) -> None:
    """dry-run 요약: 출처별 집계 · 년월별 건수 · 이동 예시."""
    targets = [p for p in plan if not p.skip]
    sources = Counter(p.source for p in plan)
    print(f"날짜 정리 미리보기 (dest: {dest})")
    print(
        f"  출처: exif {sources['exif']:,} · filename {sources['filename']:,} · "
        f"미상 {sources['unknown']:,}"
    )
    print(f"  이동 대상 {len(targets):,} · 이미 정리됨 {len(plan) - len(targets):,}")

    months = Counter(Path(p.dest_dir).name for p in targets)
    for name in sorted(months)[:_PREVIEW_MONTHS]:
        print(f"    {name}  {months[name]:,}")
    if len(months) > _PREVIEW_MONTHS:
        print(f"    … 외 {len(months) - _PREVIEW_MONTHS:,}개 폴더")

    if targets:
        print("  예시:")
        for item in targets[:_PREVIEW_SAMPLES]:
            rel = os.path.relpath(item.dest_dir, dest)
            print(f"    {Path(item.src).name} → {rel}")
    print("  실제로 이동하려면 --apply 를 붙이세요.")


def _cmd_organize(args: argparse.Namespace) -> int:
    dest = os.path.abspath(args.dest)
    with Database(args.db) as db:
        plan = plan_organize(db, dest, unknown_dir=args.unknown_dir)
        if not plan:
            print("정리할 파일이 없습니다. scan·analyze를 먼저 실행하세요.")
            return 0
        if not args.apply:
            _print_organize_preview(plan, dest)
            return 0
        moved, skipped, failed = apply_organize(db, plan)
    print(f"정리 완료: 이동 {moved:,} · 건너뜀 {skipped:,} · 실패 {failed:,}")
    return 0
```

`cli.py`에는 `import os`가 없으므로 상단 import 블록(`import json` 뒤)에 추가한다. `Path`와 `argparse`는 이미 있다.

`build_parser`의 `p_report` 등록 뒤, `return parser` 앞에 추가:

```python
    p_org = sub.add_parser(
        "organize", help="촬영 날짜 기준으로 <dest>/YYYY/YYYY-MM 폴더에 정리(이동)"
    )
    p_org.add_argument("--dest", required=True, help="정리 결과를 모을 대상 루트 경로")
    p_org.add_argument(
        "--apply", action="store_true",
        help="실제로 이동한다 (없으면 미리보기만)",
    )
    p_org.add_argument(
        "--unknown-dir", default=DEFAULT_UNKNOWN_DIR,
        help=f"날짜 추정 실패분을 모을 폴더 이름 (기본: {DEFAULT_UNKNOWN_DIR})",
    )
    p_org.set_defaults(func=_cmd_organize)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cli_organize.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/photo_organizer/cli.py tests/test_cli_organize.py
git commit -m "feat(cli): organize 서브커맨드 (dry-run 기본, --apply로 이동)"
git push origin main
```

---

### Task 7: 전체 검증 + 문서 갱신

**Files:**
- Modify: `docs/TODO.md` (Phase 5 섹션에 완료 항목 추가)
- Modify: `docs/HANDOFF.md` (§3 파이프라인 다이어그램과 실행 명령 목록)
- Modify: `CLAUDE.md` ("다음 할 일" 문단)

**Interfaces:**
- Consumes: Task 1~6의 산출물 전부
- Produces: 없음 (문서)

- [ ] **Step 1: 전체 테스트 실행**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 기존 68개 + 신규 41개(parametrize 전개 포함) 전부 PASS. 실패가 있으면 해당 태스크로 돌아가 고친다.

- [ ] **Step 2: 실제 CLI로 손 검증**

```bash
PYTHONPATH=src .venv/bin/python scripts/make_sample_photos.py /tmp/po-organize-check/photos
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db /tmp/po-organize-check/lib.db scan /tmp/po-organize-check/photos
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db /tmp/po-organize-check/lib.db analyze --thumb-dir /tmp/po-organize-check/thumbs
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db /tmp/po-organize-check/lib.db organize --dest /tmp/po-organize-check/sorted
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db /tmp/po-organize-check/lib.db organize --dest /tmp/po-organize-check/sorted --apply
find /tmp/po-organize-check/sorted -type f | head -20
```

미리보기 출력이 읽을 만한지, 한글 폴더명(`_날짜미상`)이 깨지지 않는지 확인한다. 확인 후 `/tmp/po-organize-check`를 지운다.

- [ ] **Step 3: 문서 갱신**

`docs/TODO.md` Phase 5 섹션에 추가:

```markdown
- [x] `core/date_source.py` — EXIF·파일명 기반 촬영 날짜 결정(오탐 방지 3규칙)
- [x] `core/organize.py` + CLI `organize` — 촬영 날짜 기준 `<dest>/YYYY/YYYY-MM` 이동
      (기본 dry-run, `--apply`로 실행, `_날짜미상` 폴더, move 되돌리기 지원)
```

`docs/HANDOFF.md` §3의 파이프라인 다이어그램 마지막 줄을 다음으로 교체:

```
 → bestshot(품질 가중합, 그룹별 ⭐) → GUI 검토 → 안전 정리(휴지통/격리)
 → organize(촬영 날짜 기준 YYYY/YYYY-MM 이동, 되돌리기 가능)
```

같은 문서 §2의 실행 명령 목록에 추가:

```bash
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db organize --dest <경로>          # 미리보기
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db organize --dest <경로> --apply  # 실제 이동
```

`CLAUDE.md`의 "다음 할 일" 문단에서 남은 작업 목록에 후속 항목을 반영한다:

```markdown
남은 작업(Phase 5 마감): 증분 재스캔(mtime) · PyInstaller 패키징(.exe/.app) ·
10만 장 성능/부하 테스트 · 날짜 정리 GUI 연결(CLI `organize`는 완료).
```

- [ ] **Step 4: 최종 테스트 재실행**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add docs/TODO.md docs/HANDOFF.md CLAUDE.md
git commit -m "docs: 날짜 기준 년/월 정리(organize) 반영"
git push origin main
```

---

## 범위 밖 (후속)

스펙 §6과 동일하다. 이 계획에서 **구현하지 않는다**:

- GUI 연결(버튼·미리보기 다이얼로그·진행률 워커)
- 동반 파일(`.AAE`/`.MOV`/`.XMP`) 동반 이동
- RAW의 EXIF 촬영시각 읽기 — RAW는 파일명에 날짜가 없으면 `_날짜미상`으로 간다
- `mtime` fallback 옵션
- 사용자 지정 폴더 패턴(`{YYYY}/{MM}`)
