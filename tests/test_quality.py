"""품질 지표 테스트 (SPEC 4.4a)."""
import numpy as np

from photo_organizer.classify import quality


def _noise(size=(200, 200), seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (size[1], size[0]), dtype=np.uint8)


def test_sharpness_noise_greater_than_flat():
    sharp = quality.sharpness(_noise())
    flat = quality.sharpness(np.full((200, 200), 128, np.uint8))
    assert sharp > flat
    assert flat < 1.0  # 단색은 라플라시안 분산 ≈ 0


def test_exposure_clipping_penalized():
    white = np.full((100, 100), 255, np.uint8)   # 완전 오버노출
    black = np.full((100, 100), 0, np.uint8)     # 완전 언더노출
    mid = np.full((100, 100), 128, np.uint8)     # 이상적
    assert quality.exposure_score(white) == 0.0
    assert quality.exposure_score(black) == 0.0
    assert quality.exposure_score(mid) == 1.0


def test_contrast_flat_is_low_varied_is_high():
    flat = np.full((100, 100), 128, np.uint8)
    varied = _noise()
    assert quality.contrast_score(flat) == 0.0
    assert quality.contrast_score(varied) > 0.5


def test_eyes_open_no_face_returns_none():
    """얼굴이 없는(노이즈) 이미지 → (False, None). 인물 지표 미적용."""
    has_face, score = quality.eyes_open(_noise(seed=3))
    assert has_face is False
    assert score is None


def test_compute_metrics_structure():
    from PIL import Image
    img = Image.fromarray(
        np.stack([_noise()] * 3, axis=-1).astype(np.uint8), "RGB"
    )
    m = quality.compute_metrics(img)
    assert set(m) == {"sharpness", "exposure", "contrast", "eyes_open", "has_face"}
    assert m["eyes_open"] is None  # 노이즈엔 얼굴 없음
