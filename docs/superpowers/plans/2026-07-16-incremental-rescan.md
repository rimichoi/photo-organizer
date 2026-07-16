# 증분 재스캔 (Incremental Rescan) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 재스캔 시 신규 파일 추가 · 변경 파일 반영(파생 데이터 무효화) · 외부 삭제 감지를 비파괴·재개 가능 원칙을 지키며 수행한다.

**Architecture:** `scanner.add_file`의 `INSERT OR IGNORE`를 SQLite upsert(`ON CONFLICT(path) DO UPDATE`)로 교체해 변경 파일의 파생 데이터를 무효화한다. 외부 삭제는 새 `missing` 플래그로 표시하되(사용자 정리 `removed`와 분리), root 접두어 스코프 + 빈-walk 안전 가드로 네트워크 드라이브 오탐을 막는다. 가시성 필터에 `missing=0`을 병기해 파이프라인·뷰에서 자동 제외한다.

**Tech Stack:** Python 3.11+ (개발은 3.14 venv) · SQLite(WAL) · pytest · PySide6(GUI 배선만).

## Global Constraints

- 실행/테스트는 시스템 Python 금지. **`.venv`(zerobrew python@3.14)** 사용.
- 모든 실행에 `PYTHONPATH=src` 필요(editable 설치 안 함). GUI 테스트는 `QT_QPA_PLATFORM=offscreen`.
- 테스트 명령: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
- 비파괴성: 자동 완전삭제 없음. 삭제 감지는 `missing=1` 표시까지만.
- 코드는 `from __future__ import annotations` 유지(3.14 호환).
- Git 저장소 아님 → 커밋 스텝 없음. 각 태스크는 테스트 통과로 완료.
- "변경됨" 판정 = `size` 다름 OR `mtime` 다름 (exact 비교).
- 삭제 감지 스코프 = 재스캔 root 경로 접두어 하위로 제한.
- 🔒 안전 가드: root 하위 walk 결과가 0개면 삭제 감지 중단.

---

### Task 1: `missing` 컬럼 마이그레이션 + 가시성 필터

**Files:**
- Modify: `src/photo_organizer/core/database.py` (`SCHEMA`, `_migrate`, 조회 메서드들)
- Test: `tests/test_database_missing.py` (Create)

**Interfaces:**
- Consumes: 없음.
- Produces: `files.missing INTEGER DEFAULT 0` 컬럼. 다음 조회들이 `missing=0`을 필터에 포함: `iter_size_duplicates`, `count_files`, `iter_duplicate_groups`, `iter_files_needing_analysis`, `iter_files_by_category`, `category_counts`, `iter_phashes`, `iter_similar_groups`. 신규 메서드 `mark_missing(file_ids: list[int]) -> None`, `paths_under_root(root: str) -> list[tuple[int, str]]`.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_database_missing.py`:

```python
from __future__ import annotations

from photo_organizer.core.database import Database


def _add(db, path, size=100, mtime=1.0):
    db.add_file(path, size, mtime, "jpg")
    db.conn.commit()
    return db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def test_missing_column_exists_and_defaults_zero(tmp_path):
    db = Database(tmp_path / "lib.db")
    fid = _add(db, "/root/a.jpg")
    row = db.conn.execute("SELECT missing FROM files WHERE id=?", (fid,)).fetchone()
    assert row["missing"] == 0


def test_mark_missing_hides_from_count_and_queries(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/root/a.jpg", size=100)
    b = _add(db, "/root/b.jpg", size=100)  # 같은 크기 → dedup 후보
    assert db.count_files() == 2
    db.mark_missing([a])
    assert db.count_files() == 1
    # dedup 후보에서 제외 → 크기중복 후보가 사라짐(b 혼자 남음)
    sizes = [r["path"] for r in db.iter_size_duplicates()]
    assert "/root/a.jpg" not in sizes
    # analyze 대상에서도 제외
    need = [r["path"] for r in db.iter_files_needing_analysis()]
    assert need == ["/root/b.jpg"]


def test_paths_under_root_scopes_by_prefix(tmp_path):
    db = Database(tmp_path / "lib.db")
    _add(db, "/rootA/x.jpg")
    _add(db, "/rootA/sub/y.jpg")
    _add(db, "/rootB/z.jpg")
    got = {p for _id, p in db.paths_under_root("/rootA")}
    assert got == {"/rootA/x.jpg", "/rootA/sub/y.jpg"}
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_database_missing.py -q`
Expected: FAIL (`mark_missing`/`paths_under_root` 없음; `missing` 컬럼 없음).

- [ ] **Step 3: 스키마 + 마이그레이션 + 필터 구현**

`database.py`의 `SCHEMA` 안 `files` 테이블 컬럼 목록 끝(예: `error_msg TEXT` 다음 줄)에 추가:

```python
    missing             INTEGER DEFAULT 0,  -- 외부 삭제(디스크에서 사라짐) 표식
```

`_migrate()`의 `removed` 추가 블록 아래에 추가:

```python
        if "missing" not in cols("files"):
            # 재스캔 시 디스크에서 사라진 파일 표식 (removed=사용자정리 와 분리)
            self.conn.execute("ALTER TABLE files ADD COLUMN missing INTEGER DEFAULT 0")
```

각 조회 메서드의 `removed=0`에 `AND missing=0`을 병기한다. 구체 수정:

`iter_size_duplicates` — 두 곳의 `removed=0`을 `removed=0 AND missing=0`으로:

```python
        return self.conn.execute(
            """SELECT id, path, size FROM files
               WHERE size > 0 AND removed=0 AND missing=0
                 AND size IN (SELECT size FROM files WHERE size > 0
                              AND removed=0 AND missing=0
                              GROUP BY size HAVING COUNT(*) > 1)
               ORDER BY size"""
        )
```

`count_files`:

```python
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE removed=0 AND missing=0"
        ).fetchone()
```

`iter_duplicate_groups` — `WHERE f.removed=0` → `WHERE f.removed=0 AND f.missing=0`.

`iter_files_needing_analysis`:

```python
        return self.conn.execute(
            "SELECT id, path FROM files "
            "WHERE phash IS NULL AND scan_status != 'error' "
            "AND removed=0 AND missing=0 ORDER BY id"
        )
```

`iter_files_by_category` — category 지정/미지정 두 쿼리 모두 `removed=0` → `removed=0 AND missing=0`.

`category_counts` — `WHERE category IS NOT NULL AND removed=0` → `... AND missing=0`.

`iter_phashes` — `WHERE phash IS NOT NULL AND removed=0` → `... AND missing=0`.

`iter_similar_groups` — `WHERE f.removed=0` → `WHERE f.removed=0 AND f.missing=0`.

`reset()`의 삭제 대상 테이블 목록은 그대로(파일 행 삭제로 missing도 함께 사라짐).

신규 메서드 2개를 추가(예: `mark_removed` 근처):

```python
    def mark_missing(self, file_ids: list[int], missing: int = 1) -> None:
        """외부 삭제 감지: 파일들을 missing 표시(재발견 시 upsert가 0으로 복원)."""
        if not file_ids:
            return
        marks = ",".join("?" * len(file_ids))
        with self.batch() as conn:
            conn.execute(
                f"UPDATE files SET missing=? WHERE id IN ({marks})",
                [missing, *file_ids],
            )

    def paths_under_root(self, root: str) -> list[tuple[int, str]]:
        """root 접두어 하위의 (id, path). 삭제 감지 대조용 (removed/missing 무관 전체)."""
        rows = self.conn.execute("SELECT id, path FROM files")
        import os
        prefix = root.rstrip(os.sep) + os.sep
        out: list[tuple[int, str]] = []
        for r in rows:
            p = r["path"]
            if p == root or p.startswith(prefix):
                out.append((r["id"], p))
        return out
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_database_missing.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: 회귀 확인**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 기존 68 + 신규 3 전부 PASS.

---

### Task 2: upsert 기반 변경 감지 + 파생 무효화

**Files:**
- Modify: `src/photo_organizer/core/database.py` (`add_file` 교체)
- Test: `tests/test_database_upsert.py` (Create)

**Interfaces:**
- Consumes: Task 1의 `files` 스키마.
- Produces: `add_file(path, size, mtime, fmt=None) -> str` — 반환값이 `"new"` | `"updated"` | `"unchanged"` 중 하나. 변경 시 `content_hash·phash·dhash·thumb_path·category·category_confidence·error_msg=NULL`, `scan_status='discovered'`, `missing=0`으로 리셋. 무변경 시 `missing=0`만 보정.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_database_upsert.py`:

```python
from __future__ import annotations

from photo_organizer.core.database import Database


def test_add_file_returns_new_then_unchanged(tmp_path):
    db = Database(tmp_path / "lib.db")
    assert db.add_file("/r/a.jpg", 100, 1.0, "jpg") == "new"
    db.conn.commit()
    assert db.add_file("/r/a.jpg", 100, 1.0, "jpg") == "unchanged"


def test_add_file_size_change_invalidates_derived(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", ("/r/a.jpg",)).fetchone()["id"]
    db.set_analysis_results([("ph", "dh", "/t/1.jpg", "normal", 0.9, fid)])
    db.conn.commit()
    # size 변경 → updated + 파생 NULL
    assert db.add_file("/r/a.jpg", 200, 1.0, "jpg") == "updated"
    db.conn.commit()
    row = db.conn.execute(
        "SELECT size, phash, dhash, thumb_path, category, category_confidence, "
        "scan_status, missing FROM files WHERE id=?", (fid,)
    ).fetchone()
    assert row["size"] == 200
    assert row["phash"] is None and row["dhash"] is None
    assert row["thumb_path"] is None and row["category"] is None
    assert row["category_confidence"] is None
    assert row["scan_status"] == "discovered"


def test_add_file_mtime_change_invalidates(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    db.conn.commit()
    assert db.add_file("/r/a.jpg", 100, 2.0, "jpg") == "updated"


def test_add_file_refind_clears_missing(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", ("/r/a.jpg",)).fetchone()["id"]
    db.mark_missing([fid])
    # 무변경 재발견 → unchanged 이지만 missing 은 0으로 복원
    assert db.add_file("/r/a.jpg", 100, 1.0, "jpg") == "unchanged"
    db.conn.commit()
    row = db.conn.execute("SELECT missing FROM files WHERE id=?", (fid,)).fetchone()
    assert row["missing"] == 0
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_database_upsert.py -q`
Expected: FAIL (`add_file`이 None 반환, upsert 미구현).

- [ ] **Step 3: `add_file`를 upsert로 교체**

`database.py`의 기존 `add_file`를 아래로 교체:

```python
    def add_file(self, path: str, size: int, mtime: float, fmt: str | None = None) -> str:
        """디스커버리/재스캔 단계: 파일 정보를 upsert 한다.

        반환값: 'new'(신규), 'updated'(size/mtime 변경 → 파생 무효화),
        'unchanged'(변경 없음, missing 만 0 복원). 변경 시 content_hash·phash·
        dhash·thumb_path·category·category_confidence·error_msg 를 NULL 로 리셋하고
        scan_status 를 'discovered' 로 되돌려 파이프라인이 재처리하게 한다.
        """
        prev = self.conn.execute(
            "SELECT size, mtime FROM files WHERE path=?", (path,)
        ).fetchone()
        if prev is None:
            self.conn.execute(
                "INSERT INTO files(path, size, mtime, format) VALUES (?,?,?,?)",
                (path, size, mtime, fmt),
            )
            return "new"
        if prev["size"] == size and prev["mtime"] == mtime:
            # 무변경: 재발견 시 missing 만 복원.
            self.conn.execute(
                "UPDATE files SET missing=0 WHERE path=?", (path,)
            )
            return "unchanged"
        # 변경됨: 메타 갱신 + 파생 데이터 무효화.
        self.conn.execute(
            "UPDATE files SET size=?, mtime=?, format=?, "
            "content_hash=NULL, phash=NULL, dhash=NULL, thumb_path=NULL, "
            "category=NULL, category_confidence=NULL, error_msg=NULL, "
            "scan_status='discovered', missing=0 WHERE path=?",
            (size, mtime, fmt, path),
        )
        return "updated"
```

주: 파일당 SELECT 1회가 추가되나 path UNIQUE 인덱스로 빠르고, 배치 커밋은 유지된다.

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_database_upsert.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: 회귀 확인**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 전부 PASS.

---

### Task 3: 스캐너 요약 반환 + 삭제 감지 + 안전 가드

**Files:**
- Modify: `src/photo_organizer/core/scanner.py` (`scan_directory`)
- Test: `tests/test_scanner_incremental.py` (Create)

**Interfaces:**
- Consumes: `Database.add_file`(반환 문자열), `Database.paths_under_root`, `Database.mark_missing`.
- Produces: `scan_directory(db, root, batch_size=500, progress=None, detect_deletions=False) -> dict` — `{"new": int, "updated": int, "unchanged": int, "deleted": int | None}`. `deleted`는 감지 미수행 시 `None`, 안전 가드 발동(빈 walk) 시에도 `None`. `progress`는 계속 누적 발견 개수(int)를 받는다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_scanner_incremental.py`:

```python
from __future__ import annotations

import os

from photo_organizer.core.database import Database
from photo_organizer.core.scanner import scan_directory


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_scan_summary_new_and_unchanged(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    _write(str(root / "b.jpg"))
    db = Database(tmp_path / "lib.db")
    s1 = scan_directory(db, str(root))
    assert s1["new"] == 2 and s1["updated"] == 0
    s2 = scan_directory(db, str(root))
    assert s2["new"] == 0 and s2["unchanged"] == 2


def test_scan_detects_modification(tmp_path):
    root = tmp_path / "photos"
    f = str(root / "a.jpg")
    _write(f, b"x")
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    _write(f, b"xxxxxx")  # size 변경
    s = scan_directory(db, str(root))
    assert s["updated"] == 1


def test_detect_deletions_marks_missing_scoped(tmp_path):
    root = tmp_path / "photos"
    other = tmp_path / "other"
    _write(str(root / "a.jpg"))
    _write(str(root / "b.jpg"))
    _write(str(other / "c.jpg"))
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    scan_directory(db, str(other))
    assert db.count_files() == 3
    # root 에서 b 삭제 후 삭제 감지 재스캔
    os.remove(str(root / "b.jpg"))
    s = scan_directory(db, str(root), detect_deletions=True)
    assert s["deleted"] == 1
    # b 만 missing, other/c 는 무영향
    assert db.count_files() == 2
    miss = db.conn.execute(
        "SELECT path FROM files WHERE missing=1"
    ).fetchall()
    assert [r["path"] for r in miss] == [str(root / "b.jpg")]


def test_deleted_file_refound_restores(tmp_path):
    root = tmp_path / "photos"
    f = str(root / "a.jpg")
    _write(f)
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    os.remove(f)
    scan_directory(db, str(root), detect_deletions=True)
    assert db.count_files() == 0
    _write(f)  # 되살아남
    scan_directory(db, str(root), detect_deletions=True)
    assert db.count_files() == 1
    row = db.conn.execute("SELECT missing FROM files WHERE path=?", (f,)).fetchone()
    assert row["missing"] == 0


def test_safety_guard_empty_walk_skips_deletion(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    # root 를 통째로 접근 불가로: 존재하지 않는 경로로 재스캔(빈 walk)
    gone = tmp_path / "photos_gone"
    # DB 경로는 여전히 root 하위지만 walk 대상 root 를 바꿔 빈 결과를 유도할 수는 없으므로
    # 실제 root 내용을 모두 지워 빈 walk 를 만든다.
    os.remove(str(root / "a.jpg"))
    os.rmdir(str(root))
    os.makedirs(str(root))  # 빈 디렉토리 → walk 0개
    s = scan_directory(db, str(root), detect_deletions=True)
    assert s["deleted"] is None  # 가드 발동 → 삭제 보류
    assert db.count_files() == 1  # a 는 여전히 살아있음(missing 처리 안 됨)
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scanner_incremental.py -q`
Expected: FAIL (`scan_directory`가 int 반환, `detect_deletions` 인자 없음).

- [ ] **Step 3: `scan_directory` 재작성**

`scanner.py`의 `scan_directory`를 아래로 교체:

```python
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

    변경 파일은 add_file 이 파생 데이터를 무효화하므로 이후 dedup/analyze/
    similar/bestshot 재실행이 자연히 재처리한다.
    """
    root = str(root)
    session_id = db.start_session(root)
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    seen: set[str] = set()
    processed = 0
    try:
        with db.batch() as conn:
            for raw_path, size, mtime, ext in _iter_image_files(root):
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
                # 🔒 안전 가드: 빈 walk(언마운트/접근불가) → 삭제 보류.
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
```

주: `paths_under_root`는 removed/missing 무관 전체를 반환하나, 이미 missing 인
파일이 다시 gone 이어도 `mark_missing`이 멱등이라 무해하다. `seen`에 포함되면
add_file 의 upsert 경로에서 이미 missing=0 으로 복원된다.

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scanner_incremental.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: 회귀 확인**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 전부 PASS.

---

### Task 4: CLI `scan --detect-deletions` + 요약 출력

**Files:**
- Modify: `src/photo_organizer/cli.py` (`_cmd_scan`, argparse `scan` 서브파서)
- Test: `tests/test_cli_scan.py` (Create 또는 기존 CLI 테스트에 추가)

**Interfaces:**
- Consumes: `scan_directory(... detect_deletions=...) -> dict`.
- Produces: CLI 동작. `_cmd_scan`이 요약 dict 를 사람이 읽는 문자열로 출력. `--detect-deletions` 플래그 인식.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_cli_scan.py`:

```python
from __future__ import annotations

import os

from photo_organizer.cli import build_parser, _cmd_scan


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_scan_flag_parses_detect_deletions():
    parser = build_parser()
    args = parser.parse_args(["--db", "x.db", "scan", "/p", "--detect-deletions"])
    assert args.detect_deletions is True


def test_cmd_scan_reports_summary(tmp_path, capsys):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    db_path = str(tmp_path / "lib.db")

    class NS:
        db = db_path
        path = str(root)
        detect_deletions = False

    rc = _cmd_scan(NS())
    assert rc == 0
    out = capsys.readouterr().out
    assert "신규" in out
```

주: `build_parser`가 아직 없으면 Step 3에서 argparse 구성을 `build_parser()`로
추출한다(현재 `main()` 안에 인라인일 수 있음 — 구현 시 확인).

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli_scan.py -q`
Expected: FAIL (`build_parser` 없음 또는 `detect_deletions` 미인식).

- [ ] **Step 3: CLI 수정**

먼저 `cli.py` 하단의 `main()`에서 argparse 구성을 확인한다. `scan` 서브파서에
플래그를 추가:

```python
    p_scan.add_argument(
        "--detect-deletions", action="store_true",
        help="디스크에서 사라진 파일을 감지해 missing 표시(root 하위만)",
    )
```

argparse 구성이 `main()` 안에 인라인이면, 파서 생성부를 `build_parser()` 함수로
추출하고 `main()`은 `build_parser().parse_args()`를 호출하도록 리팩터링한다
(테스트에서 파서만 단독 검증하기 위함). 추출 시 기존 서브커맨드/인자는 그대로 이동.

`_cmd_scan`을 요약 출력으로 교체:

```python
def _cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.exists():
        print(f"경로를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    def progress(n: int) -> None:
        print(f"\r  발견: {n:,}개", end="", flush=True)

    with Database(args.db) as db:
        summary = scan_directory(
            db, root, progress=progress,
            detect_deletions=getattr(args, "detect_deletions", False),
        )
    print(
        f"\n스캔 완료: 신규 {summary['new']:,} · 변경 {summary['updated']:,} · "
        f"무변경 {summary['unchanged']:,}"
    )
    if summary["deleted"] is None and getattr(args, "detect_deletions", False):
        print("  ⚠ 삭제 감지 보류: root 하위에서 파일을 찾지 못함(드라이브 연결 확인).")
    elif summary["deleted"] is not None:
        print(f"  삭제(missing) 표시: {summary['deleted']:,}개")
    return 0
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli_scan.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: 회귀 확인**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 전부 PASS.

---

### Task 5: GUI 파이프라인 배선 (detect_deletions)

**Files:**
- Modify: `src/photo_organizer/gui/workers.py` (`PipelineWorker`)
- Test: `tests/test_worker_incremental.py` (Create)

**Interfaces:**
- Consumes: `scan_directory(... detect_deletions=...) -> dict`.
- Produces: `PipelineWorker(..., detect_deletions: bool = True)`. `run()`의 요약 dict 에 `scanned_new`/`scanned_updated`/`scanned_deleted` 포함.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_worker_incremental.py`:

```python
from __future__ import annotations

import os

from photo_organizer.gui.workers import PipelineWorker


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_worker_accepts_detect_deletions(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    w = PipelineWorker(
        db_path=str(tmp_path / "lib.db"),
        root=str(root),
        thumb_dir=str(tmp_path / "thumbs"),
        detect_deletions=True,
    )
    results = {}
    w.finished.connect(lambda s: results.update(s))
    w.failed.connect(lambda m: results.setdefault("error", m))
    w.run()
    assert "error" not in results
    assert "scanned_new" in results
```

주: `w.run()`을 직접 호출해 스레드 없이 동기 실행(테스트 단순화). 실제 앱은
QThread 로 구동.

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_worker_incremental.py -q`
Expected: FAIL (`detect_deletions` 인자 없음 / `scanned_new` 없음).

- [ ] **Step 3: `PipelineWorker` 수정**

`__init__` 시그니처에 파라미터 추가(`cfg` 다음):

```python
        cfg: Config | None = None,
        detect_deletions: bool = True,
    ):
        ...
        self._cfg = cfg or Config()
        self._detect_deletions = detect_deletions
```

`run()`의 scan 호출과 summary 를 수정:

```python
                self.progress.emit("① 스캔 시작…")
                scan_summary = scan_directory(
                    db, self._root,
                    progress=lambda c: self.progress.emit(f"① 스캔 중… {c:,}개 발견"),
                    detect_deletions=self._detect_deletions,
                )
```

`summary` dict 구성에 추가:

```python
                summary = {
                    "scanned_new": scan_summary["new"],
                    "scanned_updated": scan_summary["updated"],
                    "scanned_deleted": scan_summary["deleted"],
                    "analyzed_ok": ok,
                    "analyzed_err": err,
                    "duplicate_groups": len(dups),
                    "similar_groups": len(sims),
                    "bestshot_groups": best_groups,
                }
```

주: 기존 `"scanned"` 키를 참조하는 곳이 있으면(예: main_window 요약 표시)
`scanned_new`로 갱신하거나 `"scanned": scan_summary["new"] + scan_summary["updated"]`
호환 키를 추가한다. 구현 시 `grep -rn '"scanned"\|\[.scanned.\]' src/photo_organizer/gui`
로 확인 후 배선.

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_worker_incremental.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: 전체 회귀 + 문서 갱신**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: 전부 PASS.

`docs/HANDOFF.md` §5의 "증분 재스캔" 체크박스를 `[x]`로, `docs/TODO.md`의 해당
항목도 완료로 갱신한다.

---

## Self-Review

- **스펙 커버리지**: D1(size+mtime)=Task2/3 · D2(missing)=Task1/3 · D3(scan+flag)=Task3/4 · 가시성 필터=Task1 · upsert 무효화=Task2 · 삭제 감지+안전가드=Task3 · CLI=Task4 · GUI=Task5 · 테스트=각 태스크. 스펙 §4 테스트 항목 전부 태스크에 매핑됨.
- **플레이스홀더**: 없음(모든 코드/명령/기대출력 명시). "구현 시 확인" 2곳(CLI 파서 위치, GUI `"scanned"` 참조부)은 코드베이스 실제 구조에 의존하는 배선으로, grep 명령까지 제시.
- **타입 일관성**: `add_file -> str`("new"/"updated"/"unchanged")가 Task2 정의·Task3 소비에서 일치. `scan_directory -> dict`(new/updated/unchanged/deleted) Task3 정의·Task4/5 소비 일치. `mark_missing`/`paths_under_root` Task1 정의·Task3 소비 일치.
