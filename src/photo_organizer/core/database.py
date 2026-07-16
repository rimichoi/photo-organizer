"""SQLite 저장소 — 스캔 상태, 해시, 분류 결과, 실행 로그를 영속화한다.

설계 원칙 (docs/SPEC.md 4.5 참조):
- 전체 목록을 메모리에 올리지 않는다. DB가 단일 진실 공급원(source of truth).
- 어느 단계에서 멈춰도 재개 가능하도록 scan_status로 각 파일의 진행 단계를 추적.
- WAL 모드 + 배치 커밋으로 10만 장 규모의 쓰기 성능 확보.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id                  INTEGER PRIMARY KEY,
    path                TEXT UNIQUE NOT NULL,
    size                INTEGER NOT NULL,
    mtime               REAL NOT NULL,
    format              TEXT,
    content_hash        TEXT,          -- 완전 중복용 (필요 시 계산)
    phash               TEXT,          -- 유사도용 perceptual hash
    dhash               TEXT,
    thumb_path          TEXT,          -- 썸네일 캐시 경로
    category            TEXT,          -- screenshot/document/blurry/normal/corrupt
    category_confidence REAL,
    scan_status         TEXT DEFAULT 'discovered',  -- discovered/hashed/classified/error
    error_msg           TEXT,
    missing             INTEGER DEFAULT 0  -- 외부 삭제(디스크에서 사라짐) 표식
);
CREATE INDEX IF NOT EXISTS idx_files_size   ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(scan_status);
CREATE INDEX IF NOT EXISTS idx_files_phash  ON files(phash);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    group_id          INTEGER NOT NULL,
    file_id           INTEGER NOT NULL REFERENCES files(id),
    is_representative  INTEGER DEFAULT 0,
    PRIMARY KEY (group_id, file_id)
);

CREATE TABLE IF NOT EXISTS similar_groups (
    group_id         INTEGER NOT NULL,
    file_id          INTEGER NOT NULL REFERENCES files(id),
    similarity_score REAL,
    quality_score    REAL,           -- 베스트샷 선정용 최종 점수
    is_best_shot     INTEGER DEFAULT 0,
    quality_detail   TEXT,           -- 지표별 근거 (JSON)
    PRIMARY KEY (group_id, file_id)
);

CREATE TABLE IF NOT EXISTS scan_sessions (
    id          INTEGER PRIMARY KEY,
    root_path   TEXT NOT NULL,
    started_at  REAL,
    finished_at REAL,
    total       INTEGER DEFAULT 0,
    processed   INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running'  -- running/paused/done/error
);

CREATE TABLE IF NOT EXISTS action_log (
    id        INTEGER PRIMARY KEY,
    file_id   INTEGER,
    action    TEXT,      -- trash/quarantine/move
    from_path TEXT,
    to_path   TEXT,
    timestamp REAL
);
"""


class Database:
    """스캔 결과 저장소. `with Database(path) as db:` 형태로 사용."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # 대량 쓰기 성능 및 동시성 확보
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """스키마 점진 확장 (기존 DB도 안전하게 컬럼 추가)."""
        def cols(table: str) -> set[str]:
            return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

        if "removed" not in cols("files"):
            # 휴지통/격리로 정리된 파일 표식 (뷰에서 숨김, 되돌리기로 복원)
            self.conn.execute("ALTER TABLE files ADD COLUMN removed INTEGER DEFAULT 0")
        if "missing" not in cols("files"):
            # 재스캔 시 디스크에서 사라진 파일 표식 (removed=사용자정리 와 분리)
            self.conn.execute("ALTER TABLE files ADD COLUMN missing INTEGER DEFAULT 0")
        alog = cols("action_log")
        if "batch" not in alog:
            self.conn.execute("ALTER TABLE action_log ADD COLUMN batch INTEGER")
        if "undone" not in alog:
            self.conn.execute("ALTER TABLE action_log ADD COLUMN undone INTEGER DEFAULT 0")

        # 경로 NFC 정규화 (user_version 1). macOS NFD로 저장된 기존 경로를 1회 보정.
        ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if ver < 1:
            import unicodedata
            rows = self.conn.execute("SELECT id, path FROM files").fetchall()
            for r in rows:
                nfc = unicodedata.normalize("NFC", r["path"])
                if nfc != r["path"]:
                    try:
                        self.conn.execute(
                            "UPDATE files SET path=? WHERE id=?", (nfc, r["id"])
                        )
                    except sqlite3.IntegrityError:
                        # NFC 형태의 행이 이미 존재(과거 중복 기록) → 중복 NFD 행과
                        # 그 의존 행(그룹/로그)을 함께 제거해 고아 참조를 막는다.
                        rid = r["id"]
                        self.conn.execute("DELETE FROM duplicate_groups WHERE file_id=?", (rid,))
                        self.conn.execute("DELETE FROM similar_groups WHERE file_id=?", (rid,))
                        self.conn.execute("DELETE FROM action_log WHERE file_id=?", (rid,))
                        self.conn.execute("DELETE FROM files WHERE id=?", (rid,))
            self.conn.execute("PRAGMA user_version=1")

    def add_file(self, path: str, size: int, mtime: float, fmt: str | None = None) -> str:
        """디스커버리/재스캔 단계: 파일 정보를 upsert 한다.

        반환값: 'new'(신규), 'updated'(size/mtime 변경 → 파생 무효화),
        'unchanged'(변경 없음, missing 만 0 복원). 변경 시 content_hash·phash·
        dhash·thumb_path·category·category_confidence·error_msg 를 NULL 로 리셋하고
        scan_status 를 'discovered' 로 되돌려 파이프라인이 재처리하게 한다.
        """
        prev = self.conn.execute(
            "SELECT size, mtime, missing FROM files WHERE path=?", (path,)
        ).fetchone()
        if prev is None:
            self.conn.execute(
                "INSERT INTO files(path, size, mtime, format) VALUES (?,?,?,?)",
                (path, size, mtime, fmt),
            )
            return "new"
        if prev["size"] == size and prev["mtime"] == mtime:
            # 무변경: 재발견 시 missing 만 복원. 이미 missing=0 이면 쓰기 생략
            # (10만 장 규모 무변경 재스캔에서 불필요한 WAL 쓰기를 막는다).
            if prev["missing"]:
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

    def iter_size_duplicates(self):
        """완전 중복 후보: 동일 크기가 2개 이상인 파일들만 반환 (해시 대상 최소화).

        크기 0 파일은 (빈 파일끼리 모두 "중복"으로 잡혀 노이즈가 되므로) 제외한다.
        """
        return self.conn.execute(
            """SELECT id, path, size FROM files
               WHERE size > 0 AND removed=0 AND missing=0
                 AND size IN (SELECT size FROM files WHERE size > 0
                              AND removed=0 AND missing=0
                              GROUP BY size HAVING COUNT(*) > 1)
               ORDER BY size"""
        )

    # ---- 해시/중복 그룹 (Phase 1: hasher가 사용) ----

    def set_content_hashes(self, pairs: list[tuple[str, int]]) -> None:
        """(content_hash, file_id) 목록을 일괄 갱신하고 scan_status를 hashed로 표시."""
        if not pairs:
            return
        with self.batch() as conn:
            conn.executemany(
                "UPDATE files SET content_hash=?, scan_status='hashed' WHERE id=?",
                pairs,
            )

    def save_duplicate_groups(self, groups: dict[str, list[tuple[int, str]]]) -> None:
        """완전 중복 그룹을 duplicate_groups에 재작성한다.

        groups: {content_hash: [(file_id, path), ...]} (각 그룹 원소 2개 이상).
        대표 원본(is_representative)은 경로가 가장 짧은(→ 상위 폴더에 가까운) 것을
        결정적으로 선택한다. 완전 중복은 바이트가 동일하므로 해상도/품질 차이가
        없어 경로 우선순위만으로 충분하다(최종 결정은 사용자 몫 — SPEC 3.2).
        """
        with self.batch() as conn:
            conn.execute("DELETE FROM duplicate_groups")
            for gid, (_ch, items) in enumerate(groups.items(), start=1):
                rep_id = min(items, key=lambda t: (len(t[1]), t[1]))[0]
                conn.executemany(
                    "INSERT OR REPLACE INTO duplicate_groups"
                    "(group_id, file_id, is_representative) VALUES (?,?,?)",
                    [(gid, fid, 1 if fid == rep_id else 0) for fid, _p in items],
                )

    def iter_duplicate_groups(self):
        """중복 그룹을 리포트용으로 조회. 그룹 → 대표 우선 → 경로 순 정렬."""
        return self.conn.execute(
            """SELECT dg.group_id, f.id AS file_id, f.path, f.size,
                      dg.is_representative, f.content_hash, f.thumb_path,
                      f.category, f.category_confidence
               FROM duplicate_groups dg
               JOIN files f ON f.id = dg.file_id
               WHERE f.removed=0 AND f.missing=0
               ORDER BY dg.group_id, dg.is_representative DESC, f.path"""
        )

    def count_files(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE removed=0 AND missing=0"
        ).fetchone()
        return row["c"]

    def reset(self) -> None:
        """모든 결과를 비운다(새로 시작). 파일 메타·그룹·세션·로그 전부 삭제."""
        with self.batch() as conn:
            for table in (
                "action_log", "similar_groups", "duplicate_groups",
                "scan_sessions", "files",
            ):
                conn.execute(f"DELETE FROM {table}")

    # ---- 안전 작업: 휴지통/격리 이동 + 되돌리기 (Phase 5) ----

    def paths_for_ids(self, file_ids: list[int]) -> list[tuple[int, str]]:
        """file_id 목록의 (id, path). 순서는 보장하지 않는다."""
        if not file_ids:
            return []
        marks = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"SELECT id, path FROM files WHERE id IN ({marks})", file_ids
        )
        return [(r["id"], r["path"]) for r in rows]

    def next_action_batch(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(batch), 0) AS m FROM action_log"
        ).fetchone()
        return int(row["m"]) + 1

    def record_actions(self, batch: int, rows: list[tuple[int, str, str, str | None]]) -> None:
        """rows: (file_id, action, from_path, to_path). timestamp/batch/undone 자동 기록."""
        if not rows:
            return
        with self.batch() as conn:
            conn.executemany(
                "INSERT INTO action_log(file_id, action, from_path, to_path, "
                "timestamp, batch, undone) VALUES (?,?,?,?,?,?,0)",
                [(fid, act, frm, to, time.time(), batch) for fid, act, frm, to in rows],
            )

    def mark_removed(self, file_ids: list[int], removed: int = 1) -> None:
        if not file_ids:
            return
        marks = ",".join("?" * len(file_ids))
        with self.batch() as conn:
            conn.execute(
                f"UPDATE files SET removed=? WHERE id IN ({marks})",
                [removed, *file_ids],
            )

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
        """root 접두어 하위의 (id, path). 삭제 감지 대조용 — 이미 격리/휴지통으로

        정리된(removed=1) 파일과 이미 missing 처리된 파일은 대조 대상에서
        제외한다(removed=0 AND missing=0). 그렇지 않으면 (a) 사용자가 격리한
        파일이 재스캔 시 missing=1 로 오염되어 되돌리기 후에도 뷰에서 계속
        숨겨지고, (b) 이미 missing 인 파일이 재스캔마다 매번 deleted 로 다시
        카운트되는 유령 삭제 카운트가 발생한다.
        """
        rows = self.conn.execute(
            "SELECT id, path FROM files WHERE removed=0 AND missing=0"
        )
        import os
        prefix = root.rstrip(os.sep) + os.sep
        out: list[tuple[int, str]] = []
        for r in rows:
            p = r["path"]
            if p == root or p.startswith(prefix):
                out.append((r["id"], p))
        return out

    def last_undoable_batch(self) -> int | None:
        """되돌릴 수 있는 가장 최근 배치(격리 이동). 휴지통은 OS에서 복구."""
        row = self.conn.execute(
            "SELECT MAX(batch) AS b FROM action_log "
            "WHERE undone=0 AND action='quarantine'"
        ).fetchone()
        return row["b"]

    def actions_in_batch(self, batch: int) -> list[tuple[int, str, str, str | None]]:
        rows = self.conn.execute(
            "SELECT file_id, action, from_path, to_path FROM action_log "
            "WHERE batch=? AND undone=0",
            (batch,),
        )
        return [(r["file_id"], r["action"], r["from_path"], r["to_path"]) for r in rows]

    def mark_batch_undone(self, batch: int) -> None:
        with self.batch() as conn:
            conn.execute("UPDATE action_log SET undone=1 WHERE batch=?", (batch,))

    def iter_action_log(self):
        """정리 내역 전체를 시간순(batch, id)으로 조회 (감사 로그 리포트용)."""
        return self.conn.execute(
            "SELECT id, file_id, action, from_path, to_path, timestamp, batch, undone "
            "FROM action_log ORDER BY batch, id"
        )

    # ---- 분석: phash/dhash/썸네일/분류 (Phase 2: analyze가 사용) ----

    def iter_files_needing_analysis(self):
        """아직 pHash가 없고 오류 표시되지 않은 파일 (재개 지원)."""
        return self.conn.execute(
            "SELECT id, path FROM files "
            "WHERE phash IS NULL AND scan_status != 'error' "
            "AND removed=0 AND missing=0 ORDER BY id"
        )

    def set_analysis_results(self, rows: list[tuple]) -> None:
        """(phash, dhash, thumb_path, category, confidence, file_id) 목록 일괄 갱신."""
        if not rows:
            return
        with self.batch() as conn:
            conn.executemany(
                "UPDATE files SET phash=?, dhash=?, thumb_path=?, "
                "category=?, category_confidence=?, scan_status='analyzed' "
                "WHERE id=?",
                rows,
            )

    def mark_errors(self, pairs: list[tuple[int, str]]) -> None:
        """(file_id, error_msg) 목록을 오류 상태로 표시한다."""
        if not pairs:
            return
        with self.batch() as conn:
            conn.executemany(
                "UPDATE files SET scan_status='error', error_msg=? WHERE id=?",
                [(msg, fid) for fid, msg in pairs],
            )

    def iter_files_by_category(self, category: str | None = None):
        """카테고리별 파일 조회 (GUI 분류 탭). category=None이면 분류된 전체."""
        if category:
            return self.conn.execute(
                "SELECT id AS file_id, path, category, thumb_path, size, "
                "category_confidence FROM files "
                "WHERE category=? AND removed=0 AND missing=0 ORDER BY id",
                (category,),
            )
        return self.conn.execute(
            "SELECT id AS file_id, path, category, thumb_path, size, "
            "category_confidence FROM files "
            "WHERE category IS NOT NULL AND removed=0 AND missing=0 ORDER BY id"
        )

    def category_counts(self) -> dict[str, int]:
        """분류 카테고리별 파일 수."""
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS c FROM files "
            "WHERE category IS NOT NULL AND removed=0 AND missing=0 "
            "GROUP BY category ORDER BY c DESC"
        )
        return {r["category"]: r["c"] for r in rows}

    # ---- 유사 그룹 (Phase 2: similar가 사용) ----

    def iter_phashes(self):
        """pHash가 계산된 모든 파일의 (id, phash)."""
        return self.conn.execute(
            "SELECT id, phash FROM files WHERE phash IS NOT NULL "
            "AND removed=0 AND missing=0"
        )

    def save_similar_groups(self, groups: list[list[tuple[int, float]]]) -> None:
        """유사 그룹을 similar_groups에 재작성한다.

        groups: [[(file_id, similarity_score), ...], ...] (각 그룹 2개 이상).
        베스트샷(is_best_shot)은 Phase 3에서 품질 점수로 채운다.
        """
        with self.batch() as conn:
            conn.execute("DELETE FROM similar_groups")
            for gid, members in enumerate(groups, start=1):
                conn.executemany(
                    "INSERT OR REPLACE INTO similar_groups"
                    "(group_id, file_id, similarity_score) VALUES (?,?,?)",
                    [(gid, fid, score) for fid, score in members],
                )

    def nonrepresentative_duplicate_ids(self) -> set[int]:
        """완전 중복 그룹에서 '대표가 아닌' 파일 id 집합.

        유사 탭에서 이 파일들을 접어(collapse) 완전 중복 중복 노출을 막는다.
        """
        rows = self.conn.execute(
            "SELECT file_id FROM duplicate_groups WHERE is_representative=0"
        )
        return {r["file_id"] for r in rows}

    def protected_survivors(self, file_ids: list[int]) -> set[int]:
        """제거 요청 file_ids 중, 그룹의 마지막 남는 활성 멤버라 보호해야 할 id.

        중복/유사 그룹의 활성(removed=0 AND missing=0) 멤버가 전부 요청에 포함되면
        그룹이 통째로 비므로, 대표(is_representative)/베스트샷(is_best_shot)을 1장
        보호한다. 해당 플래그가 없으면 가장 작은 file_id를 보호한다(결정적).
        """
        req = set(file_ids)
        if not req:
            return set()
        protected: set[int] = set()

        def _guard(rows) -> None:
            groups: dict[int, list[tuple[int, int]]] = {}
            for r in rows:
                groups.setdefault(r["group_id"], []).append((r["file_id"], r["flag"]))
            for members in groups.values():
                active = [fid for fid, _ in members]
                if active and set(active) <= req:  # 그룹 전체가 제거 대상
                    reps = [fid for fid, flag in members if flag]
                    protected.add(min(reps) if reps else min(active))

        _guard(self.conn.execute(
            "SELECT dg.group_id AS group_id, dg.file_id AS file_id, "
            "dg.is_representative AS flag FROM duplicate_groups dg "
            "JOIN files f ON f.id = dg.file_id WHERE f.removed=0 AND f.missing=0"
        ).fetchall())
        _guard(self.conn.execute(
            "SELECT sg.group_id AS group_id, sg.file_id AS file_id, "
            "sg.is_best_shot AS flag FROM similar_groups sg "
            "JOIN files f ON f.id = sg.file_id WHERE f.removed=0 AND f.missing=0"
        ).fetchall())
        return protected

    def iter_similar_members_with_thumbs(self):
        """유사 그룹 구성원과 썸네일 경로 (베스트샷 계산용)."""
        return self.conn.execute(
            """SELECT sg.group_id, sg.file_id, f.thumb_path
               FROM similar_groups sg
               JOIN files f ON f.id = sg.file_id
               ORDER BY sg.group_id"""
        )

    def set_bestshot_results(self, rows: list[tuple]) -> None:
        """(quality_score, is_best_shot, quality_detail, group_id, file_id) 일괄 갱신."""
        if not rows:
            return
        with self.batch() as conn:
            conn.executemany(
                "UPDATE similar_groups SET quality_score=?, is_best_shot=?, "
                "quality_detail=? WHERE group_id=? AND file_id=?",
                rows,
            )

    def iter_similar_groups(self):
        """유사 그룹 리포트용 조회. 그룹 → 베스트샷 우선 → 품질점수 높은 순."""
        return self.conn.execute(
            """SELECT sg.group_id, f.id AS file_id, f.path, f.category,
                      sg.similarity_score, sg.quality_score,
                      sg.is_best_shot, sg.quality_detail, f.thumb_path,
                      f.size, f.category_confidence
               FROM similar_groups sg
               JOIN files f ON f.id = sg.file_id
               WHERE f.removed=0 AND f.missing=0
               ORDER BY sg.group_id, sg.is_best_shot DESC,
                        sg.quality_score DESC, f.path"""
        )

    # ---- 스캔 세션 (재개 토대 — SPEC 재개 가능성) ----

    def start_session(self, root_path: str) -> int:
        """스캔 세션을 시작하고 세션 id를 반환한다."""
        cur = self.conn.execute(
            "INSERT INTO scan_sessions(root_path, started_at, status) VALUES (?,?,?)",
            (root_path, time.time(), "running"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_session(self, session_id: int, total: int, status: str = "done") -> None:
        """세션을 종료 상태로 갱신한다."""
        self.conn.execute(
            "UPDATE scan_sessions SET finished_at=?, total=?, processed=?, status=? "
            "WHERE id=?",
            (time.time(), total, total, status, session_id),
        )
        self.conn.commit()

    @contextmanager
    def batch(self):
        """배치 커밋 컨텍스트. 블록이 끝나면 한 번에 커밋."""
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
