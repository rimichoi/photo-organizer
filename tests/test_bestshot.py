"""베스트샷 선정 테스트 (SPEC 4.4a, FR-07a)."""
import json

import numpy as np
from PIL import Image, ImageFilter

from photo_organizer.classify.analyze import run_analyze
from photo_organizer.classify.bestshot import _score_group, run_bestshot
from photo_organizer.classify.similar import cluster_similar
from photo_organizer.core.config import Config
from photo_organizer.core.database import Database
from photo_organizer.core.scanner import scan_directory


def _structured(seed=1, size=(400, 300)):
    rng = np.random.RandomState(seed)
    b = np.zeros((size[1], size[0], 3), np.uint8)
    for y in range(size[1]):
        b[y, :, 0] = int(255 * y / size[1])
    b[50:150, 60:200] = rng.randint(0, 256, (100, 140, 3))
    b[180:260, 250:360] = (200, 50, 50)
    return Image.fromarray(b, "RGB")


# ---- 순수 점수 로직 ----

def test_score_group_sharper_wins():
    """선명도만 다르면(정규화 후) 선명한 쪽 점수가 높다."""
    weights = {"sharpness": 1.0, "exposure": 0.0, "contrast": 0.0, "eyes_open": 0.0}
    metrics = [
        {"sharpness": 500.0, "exposure": 0.9, "contrast": 0.5, "eyes_open": None},
        {"sharpness": 50.0, "exposure": 0.9, "contrast": 0.5, "eyes_open": None},
    ]
    scores, reasons = _score_group(metrics, weights)
    assert scores[0] > scores[1]
    assert "선명도 1위" in reasons[0]


def test_score_group_eyes_reweighted_when_no_face():
    """eyes_open이 None이면 그 지표를 빼고 가중치를 재정규화한다(에러 없이)."""
    weights = {"sharpness": 0.3, "exposure": 0.2, "contrast": 0.1, "eyes_open": 0.4}
    metrics = [{"sharpness": 100.0, "exposure": 0.8, "contrast": 0.7, "eyes_open": None}]
    scores, _ = _score_group(metrics, weights)
    assert 0.0 <= scores[0] <= 1.0


# ---- 파이프라인 통합 ----

def _run_pipeline(tmp_path, images: dict, preset=None):
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for name, img in images.items():
        img.save(root / name)
    db = Database(tmp_path / "t.db")
    scan_directory(db, root)
    run_analyze(db, str(tmp_path / "thumbs"), workers=1)
    cluster_similar(db, cfg=Config())
    n = run_bestshot(db, cfg=Config(), preset=preset)
    return db, n


def test_sharp_beats_blurred_copy(tmp_path):
    """원본(선명)과 블러 처리한 유사본 중 원본이 베스트샷으로 뽑힌다."""
    original = _structured(seed=1)
    blurred = original.filter(ImageFilter.GaussianBlur(radius=4))
    db, n = _run_pipeline(tmp_path, {"sharp.png": original, "blur.png": blurred})
    assert n == 1

    rows = list(db.conn.execute(
        "SELECT f.path, sg.is_best_shot, sg.quality_score, sg.quality_detail "
        "FROM similar_groups sg JOIN files f ON f.id = sg.file_id"
    ))
    best = [r for r in rows if r["is_best_shot"]]
    assert len(best) == 1
    assert best[0]["path"].endswith("sharp.png")
    # 근거 JSON이 채워졌는지
    detail = json.loads(best[0]["quality_detail"])
    assert "score" in detail and "reason" in detail
    db.close()


def test_no_similar_groups_returns_zero(tmp_path):
    db = Database(tmp_path / "t.db")
    assert run_bestshot(db, cfg=Config()) == 0
    db.close()
