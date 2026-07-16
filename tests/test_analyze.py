"""analyze + similar 통합 테스트 — read-once 분석과 유사 그룹핑."""
import numpy as np
from PIL import Image

from photo_organizer.classify.analyze import run_analyze
from photo_organizer.classify.similar import cluster_similar
from photo_organizer.core.config import Config
from photo_organizer.core.database import Database
from photo_organizer.core.scanner import scan_directory


def _structured_photo(size=(400, 300), seed=1):
    """구조가 있는(유사도 해시가 의미 있는) 결정적 이미지.

    세로 그라디언트(저주파 구조) + 블록. pHash는 저주파 구조를 잡으므로 이
    이미지를 리사이즈/재압축해도 해시가 거의 같다.
    """
    rng = np.random.RandomState(seed)
    base = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    for y in range(size[1]):
        base[y, :, 0] = int(255 * y / size[1])
    base[50:150, 60:200] = rng.randint(0, 256, (100, 140, 3))
    base[180:260, 250:360] = (200, 50, 50)
    return Image.fromarray(base, "RGB")


def _distinct_photo(size=(400, 300)):
    """저주파 구조가 확연히 다른 이미지 (가로 그라디언트 + 다른 블록 배치).

    _structured_photo 와 pHash 거리가 임계값보다 훨씬 크다(측정상 ~32).
    """
    base = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    for x in range(size[0]):
        base[:, x, 2] = int(255 * x / size[0])
    base[20:120, 220:360] = (50, 200, 50)
    base[200:280, 40:160] = (250, 250, 80)
    return Image.fromarray(base, "RGB")


def _setup(tmp_path, images: dict):
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for name, img in images.items():
        img.save(root / name)
    db = Database(tmp_path / "t.db")
    scan_directory(db, root)
    return db


def test_analyze_populates_hashes_and_thumbnails(tmp_path):
    db = _setup(tmp_path, {"a.png": _structured_photo()})
    thumb_dir = tmp_path / "thumbs"
    ok, err = run_analyze(db, str(thumb_dir), workers=1)
    assert (ok, err) == (1, 0)

    row = db.conn.execute(
        "SELECT phash, dhash, thumb_path, category, scan_status FROM files"
    ).fetchone()
    assert row["phash"] and row["dhash"]
    assert row["scan_status"] == "analyzed"
    assert (thumb_dir / "1.jpg").exists()
    db.close()


def test_resized_recompressed_detected_as_similar(tmp_path):
    """원본과 리사이즈·JPEG 재압축본은 유사 그룹으로 묶이고,
    전혀 다른 이미지는 묶이지 않는다."""
    original = _structured_photo(seed=1)
    resized = original.resize((200, 150))          # 리사이즈
    different = _distinct_photo()                    # 구조가 다른 사진

    root = tmp_path / "photos"
    root.mkdir(parents=True)
    original.save(root / "orig.png")
    resized.save(root / "resized.jpg", quality=70)  # 재압축
    different.save(root / "other.png")

    db = Database(tmp_path / "t.db")
    scan_directory(db, root)
    run_analyze(db, str(tmp_path / "thumbs"), workers=1)
    groups = cluster_similar(db, cfg=Config())

    # 원본+리사이즈본이 같은 그룹에 있어야 한다.
    path_by_id = {r["id"]: r["path"] for r in db.conn.execute("SELECT id, path FROM files")}
    grouped_names = [
        sorted(path_by_id[fid].split("/")[-1] for fid, _score in g) for g in groups
    ]
    assert any("orig.png" in names and "resized.jpg" in names for names in grouped_names)
    # 다른 사진은 원본과 함께 묶이지 않아야 한다.
    assert not any("other.png" in names and "orig.png" in names for names in grouped_names)
    db.close()


def test_analyze_is_resumable(tmp_path):
    """이미 분석된 파일은 재실행 시 건너뛴다(phash IS NULL 기준)."""
    db = _setup(tmp_path, {"a.png": _structured_photo(seed=2)})
    thumb_dir = str(tmp_path / "thumbs")
    ok1, _ = run_analyze(db, thumb_dir, workers=1)
    ok2, _ = run_analyze(db, thumb_dir, workers=1)
    assert ok1 == 1
    assert ok2 == 0  # 두 번째엔 처리할 게 없음
    db.close()
