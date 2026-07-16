"""GUI 스모크 테스트 (헤드리스/offscreen).

실제 화면 없이 위젯 구성과 모델 로직을 검증한다. PySide6가 없으면 skip.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# 프로세스당 QApplication은 하나만.
_app = QApplication.instance() or QApplication([])

from photo_organizer.gui.main_window import MainWindow  # noqa: E402
from photo_organizer.gui.thumbnail_grid import ThumbnailModel  # noqa: E402


def test_thumbnail_model_rowcount():
    m = ThumbnailModel([
        {"label": "a", "thumb_path": None},
        {"label": "b", "thumb_path": None},
    ])
    assert m.rowCount() == 2


def test_thumbnail_model_roles():
    m = ThumbnailModel([{"label": "x", "thumb_path": None, "tooltip": "tip"}])
    idx = m.index(0, 0)
    assert m.data(idx, Qt.DisplayRole) == "x"
    assert m.data(idx, Qt.ToolTipRole) == "tip"


def test_thumbnail_model_missing_thumb_is_null_pixmap():
    m = ThumbnailModel([{"label": "x", "thumb_path": None}])
    pm = m.data(m.index(0, 0), Qt.DecorationRole)
    assert pm.isNull()  # 경로 없으면 빈 픽스맵(크래시 없이)


def test_mainwindow_constructs_with_three_tabs(tmp_path):
    w = MainWindow(db_path=str(tmp_path / "none.db"), thumb_dir=str(tmp_path / "t"))
    assert w._tabs.count() == 3
    # DB가 없어도 예외 없이 빈 상태로 구성된다.
    assert w._dup_grid.count() == 0
    assert w._sim_grid.count() == 0


def test_grid_path_at():
    from photo_organizer.gui.thumbnail_grid import ThumbnailModel
    m = ThumbnailModel([{"label": "a", "thumb_path": None, "path": "/x/a.jpg"}])
    assert m.path_at(0) == "/x/a.jpg"
    assert m.path_at(99) is None  # 범위 밖은 None


def test_double_click_opens_existing_file(tmp_path, monkeypatch):
    """더블클릭 → 존재하는 원본을 OS 뷰어(openUrl)로 연다."""
    from PIL import Image

    from photo_organizer.gui import thumbnail_grid as tg

    f = tmp_path / "x.png"
    Image.new("RGB", (10, 10), (1, 2, 3)).save(f)

    grid = tg.ThumbnailGrid()
    grid.set_items([{"label": "x", "thumb_path": None, "path": str(f)}])

    opened = {}
    monkeypatch.setattr(tg.QDesktopServices, "openUrl",
                        lambda url: opened.setdefault("path", url.toLocalFile()))
    grid._open_item(grid._model.index(0, 0))
    assert opened["path"] == str(f)


def test_double_click_missing_file_warns_and_skips_open(tmp_path, monkeypatch):
    """원본이 없으면 경고만 하고 뷰어를 열지 않는다(크래시 없이)."""
    from photo_organizer.gui import thumbnail_grid as tg

    grid = tg.ThumbnailGrid()
    grid.set_items([{"label": "x", "thumb_path": None, "path": str(tmp_path / "nope.png")}])

    warned, opened = {}, {}
    monkeypatch.setattr(tg.QMessageBox, "warning", lambda *a, **k: warned.setdefault("w", True))
    monkeypatch.setattr(tg.QDesktopServices, "openUrl", lambda url: opened.setdefault("o", True))
    grid._open_item(grid._model.index(0, 0))
    assert warned.get("w") is True
    assert "o" not in opened


def test_mainwindow_defaults_to_user_data_dir(tmp_path, monkeypatch):
    """인자 없이 생성하면 cwd가 아니라 OS 사용자 데이터 폴더 하위 경로를 쓴다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)

    from photo_organizer.core.paths import app_data_dir

    w = MainWindow()
    base = str(app_data_dir())
    assert w._db_path.startswith(base)
    assert w._thumb_dir.startswith(base)
    assert w._quarantine_dir.startswith(base)


def test_mainwindow_loads_from_db(tmp_path):
    """엔진으로 만든 DB를 GUI가 읽어 그리드를 채운다."""
    import numpy as np
    from PIL import Image

    from photo_organizer.classify.analyze import run_analyze
    from photo_organizer.classify.bestshot import run_bestshot
    from photo_organizer.classify.similar import cluster_similar
    from photo_organizer.core.database import Database
    from photo_organizer.core.hasher import find_exact_duplicates
    from photo_organizer.core.scanner import scan_directory

    root = tmp_path / "photos"
    root.mkdir()
    img = Image.fromarray(
        np.random.RandomState(0).randint(0, 256, (120, 120, 3), np.uint8), "RGB"
    )
    img.save(root / "a.png")
    (root / "b.png").write_bytes((root / "a.png").read_bytes())  # 완전 중복

    db_path = tmp_path / "lib.db"
    with Database(db_path) as db:
        scan_directory(db, root)
        find_exact_duplicates(db)
        run_analyze(db, str(tmp_path / "thumbs"), workers=1)
        cluster_similar(db)
        run_bestshot(db)

    w = MainWindow(db_path=str(db_path), thumb_dir=str(tmp_path / "thumbs"))
    assert w._dup_grid.count() == 2   # 완전 중복 쌍은 중복 탭에 표시
    # 순수 완전 중복(바이트 동일)은 유사 탭에서 대표로 접혀 사라진다.
    assert w._sim_grid.count() == 0


def test_quick_cull_quarantines_without_modal(tmp_path, monkeypatch):
    """_quick_cull: 확인 다이얼로그 없이 즉시 격리(되돌리기 가능) + 상태 텍스트 갱신."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)

    from photo_organizer.core.database import Database
    from photo_organizer.core.scanner import scan_directory
    from photo_organizer.gui.main_window import MainWindow

    root = tmp_path / "photos"
    root.mkdir()
    f = root / "a.jpg"
    f.write_bytes(b"data")

    db_path = tmp_path / "lib.db"
    with Database(db_path) as db:
        scan_directory(db, str(root))
        fid = db.conn.execute("SELECT id FROM files WHERE path=?", (str(f),)).fetchone()["id"]

    w = MainWindow(db_path=str(db_path), thumb_dir=str(tmp_path / "thumbs"))

    # 모달 다이얼로그가 뜨면 즉시 실패하도록(True=호출됨) 감시
    called = {"modal": False}
    monkeypatch.setattr(
        "photo_organizer.gui.main_window.QMessageBox.question",
        lambda *a, **k: called.__setitem__("modal", True),
    )

    w._quick_cull([fid])

    assert called["modal"] is False   # 모달 다이얼로그 미호출
    assert not f.exists()             # 원본 위치에서 격리됨
    assert list(os.scandir(w._quarantine_dir))  # 격리 폴더로 실제 이동
    with Database(db_path) as db:
        row = db.conn.execute("SELECT removed FROM files WHERE id=?", (fid,)).fetchone()
        assert row["removed"] == 1
    assert "격리됨" in w._status.text()
