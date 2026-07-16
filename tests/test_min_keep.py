from __future__ import annotations

from photo_organizer.core.database import Database
from photo_organizer.core import actions


def _add(db, path, size=100):
    db.add_file(path, size, 1.0, "jpg")
    return db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def _dup_group(db, gid, members):
    # members: [(file_id, is_representative)]
    db.conn.executemany(
        "INSERT INTO duplicate_groups(group_id, file_id, is_representative) VALUES (?,?,?)",
        [(gid, fid, rep) for fid, rep in members],
    )
    db.conn.commit()


def _sim_group(db, gid, members):
    # members: [(file_id, is_best_shot)]
    db.conn.executemany(
        "INSERT INTO similar_groups(group_id, file_id, is_best_shot) VALUES (?,?,?)",
        [(gid, fid, best) for fid, best in members],
    )
    db.conn.commit()


def test_protected_survivors_keeps_dup_representative(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg"); b = _add(db, "/r/b.jpg")
    _dup_group(db, 1, [(a, 1), (b, 0)])  # a=대표
    # 그룹 전체 제거 요청 → 대표 a 보호
    assert db.protected_survivors([a, b]) == {a}


def test_protected_survivors_keeps_similar_bestshot(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg"); b = _add(db, "/r/b.jpg")
    _sim_group(db, 1, [(a, 0), (b, 1)])  # b=베스트샷
    assert db.protected_survivors([a, b]) == {b}


def test_protected_survivors_empty_when_one_kept(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg"); b = _add(db, "/r/b.jpg")
    _dup_group(db, 1, [(a, 1), (b, 0)])
    # 대표 a는 요청에 없음(정상 extras 경로) → 보호 불필요
    assert db.protected_survivors([b]) == set()


def test_trash_skips_protected_and_reports(tmp_path, monkeypatch):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg"); b = _add(db, "/r/b.jpg")
    _dup_group(db, 1, [(a, 1), (b, 0)])
    monkeypatch.setattr(actions, "send2trash", lambda p: None)
    ok, failed, protected = actions.trash_files(db, [a, b])
    assert (ok, failed, protected) == (1, 0, 1)  # b만 제거, a 보호
    # 대표 a는 여전히 활성
    assert db.conn.execute("SELECT removed FROM files WHERE id=?", (a,)).fetchone()["removed"] == 0
    assert db.conn.execute("SELECT removed FROM files WHERE id=?", (b,)).fetchone()["removed"] == 1
