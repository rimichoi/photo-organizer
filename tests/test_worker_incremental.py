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
