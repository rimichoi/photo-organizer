"""스캐너 테스트 — 확장자 필터, 재귀, 시스템 폴더 스킵, 세션 기록."""
from PIL import Image

from photo_organizer.core.database import Database
from photo_organizer.core.scanner import scan_directory


def _img(path, size=(4, 4)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (1, 2, 3)).save(path)


def test_recursive_scan_and_extension_filter(tmp_path):
    root = tmp_path / "lib"
    _img(root / "a.jpg")
    _img(root / "sub" / "b.png")
    _img(root / "sub" / "deep" / "c.webp")
    (root / "notes.txt").write_text("not an image")
    (root / "sub" / "data.json").write_text("{}")

    db = Database(tmp_path / "s.db")
    summary = scan_directory(db, root)
    assert summary["new"] == 3
    assert db.count_files() == 3

    paths = {r["path"] for r in db.conn.execute("SELECT path FROM files")}
    assert any(p.endswith("a.jpg") for p in paths)
    assert any(p.endswith("c.webp") for p in paths)
    assert not any(p.endswith(".txt") for p in paths)
    db.close()


def test_rescan_is_idempotent(tmp_path):
    """같은 디렉토리를 두 번 스캔해도 파일 수가 늘지 않는다(INSERT OR IGNORE)."""
    root = tmp_path / "lib"
    _img(root / "a.jpg")
    _img(root / "b.jpg")

    db = Database(tmp_path / "s.db")
    scan_directory(db, root)
    scan_directory(db, root)
    assert db.count_files() == 2
    db.close()


def test_size_and_mtime_recorded(tmp_path):
    root = tmp_path / "lib"
    _img(root / "a.jpg", size=(8, 8))
    db = Database(tmp_path / "s.db")
    scan_directory(db, root)
    row = db.conn.execute("SELECT size, mtime, format FROM files").fetchone()
    assert row["size"] > 0
    assert row["mtime"] > 0
    assert row["format"] == "jpg"
    db.close()


def test_session_recorded(tmp_path):
    root = tmp_path / "lib"
    _img(root / "a.jpg")
    db = Database(tmp_path / "s.db")
    scan_directory(db, root)
    sess = db.conn.execute(
        "SELECT root_path, total, status FROM scan_sessions"
    ).fetchone()
    assert sess["status"] == "done"
    assert sess["total"] == 1
    db.close()


def test_hidden_system_dir_skipped(tmp_path):
    """macOS 시스템 폴더(.Spotlight-V100)는 스캔에서 제외된다."""
    root = tmp_path / "lib"
    _img(root / "real.jpg")
    _img(root / ".Spotlight-V100" / "hidden.jpg")

    db = Database(tmp_path / "s.db")
    summary = scan_directory(db, root)
    # 플랫폼 무관하게: macOS면 스킵되어 1, 그 외 OS에선 스킵 규칙 미적용.
    from photo_organizer.core.platform_utils import IS_MACOS
    if IS_MACOS:
        assert summary["new"] == 1
    else:
        assert summary["new"] == 2
    db.close()
