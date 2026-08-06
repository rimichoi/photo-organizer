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
