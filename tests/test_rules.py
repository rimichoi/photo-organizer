"""규칙 기반 분류 테스트 (SPEC 6.1: 블러 정오 판정, 스크린샷 규칙)."""
import numpy as np
from PIL import Image

from photo_organizer.classify import rules
from photo_organizer.core.config import Config

CFG = Config()


def _noise_image(size=(200, 200), seed=0):
    """고주파(선명) 노이즈 이미지 — 라플라시안 분산이 높다."""
    rng = np.random.RandomState(seed)
    arr = rng.randint(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def _flat_image(size=(200, 200), color=(120, 120, 120)):
    """단색 이미지 — 라플라시안 분산이 0에 가깝다(흐림처럼)."""
    return Image.new("RGB", size, color)


def test_laplacian_sharp_vs_blur():
    sharp = rules.laplacian_variance(rules._to_gray(_noise_image()))
    flat = rules.laplacian_variance(rules._to_gray(_flat_image()))
    assert sharp > CFG.blur_laplacian_threshold
    assert flat < CFG.blur_laplacian_threshold


def test_blurry_flat_bright_image():
    """밝은 단색(흐림, 어둡지 않음) → blurry 높은 신뢰도."""
    cat, conf = rules.classify(_flat_image(color=(180, 180, 180)), "IMG_1.jpg",
                               {"make": "Canon"}, CFG)
    assert cat == "blurry"
    assert conf >= 0.8


def test_dark_flat_image_low_confidence():
    """어두운 단색 → blurry지만 신뢰도 낮게(어두운 사진과 혼동 가능)."""
    cat, conf = rules.classify(_flat_image(color=(10, 10, 10)), "IMG_2.jpg",
                               {"make": "Canon"}, CFG)
    assert cat == "blurry"
    assert conf < 0.5


def test_sharp_photo_is_normal():
    cat, conf = rules.classify(_noise_image(), "IMG_3.jpg", {"make": "Nikon"}, CFG)
    assert cat == "normal"


def test_screenshot_by_filename():
    """파일명 패턴이면 카메라 정보와 무관하게 스크린샷."""
    cat, conf = rules.classify(_noise_image(), "Screenshot_2024.png", {}, CFG)
    assert cat == "screenshot"
    assert conf >= 0.9


def test_screenshot_korean_filename():
    cat, _ = rules.classify(_noise_image(), "화면 캡처 2024-01-01.png", {}, CFG)
    assert cat == "screenshot"


def test_no_camera_sharp_normal_size_is_normal():
    """카메라 정보 없고 선명하지만 화면 해상도가 아니면 normal(낮은 신뢰도).

    "카메라 없음" 단독으로 스크린샷 오탐하지 않는 것이 핵심(정밀도 보호).
    """
    cat, conf = rules.classify(_noise_image(size=(640, 480)), "download.png", {}, CFG)
    assert cat == "normal"
    assert conf < 0.9  # EXIF 없어 확신 낮춤 → DL 2차 후보


def test_no_camera_exact_screen_resolution_is_screenshot():
    """카메라 없음 + 정확한 화면 해상도(1920x1080) → 스크린샷."""
    cat, conf = rules.classify(_noise_image(size=(1920, 1080)), "img.png", {}, CFG)
    assert cat == "screenshot"
    assert conf >= 0.6


def test_camera_photo_with_screen_resolution_not_screenshot():
    """카메라 EXIF가 있으면 우연히 화면 해상도여도 스크린샷 아님."""
    cat, _ = rules.classify(_noise_image(size=(1920, 1080)), "IMG_9.jpg",
                            {"make": "Sony"}, CFG)
    assert cat == "normal"


def test_document_white_background_with_edges():
    """흰 배경 + 검은 테두리/텍스트 → 문서."""
    arr = np.full((200, 200, 3), 255, dtype=np.uint8)
    arr[20:180, 20:22] = 0   # 세로 선
    arr[20:22, 20:180] = 0   # 가로 선
    arr[178:180, 20:180] = 0
    arr[20:180, 178:180] = 0
    for y in range(40, 160, 12):  # 텍스트 유사 가로줄
        arr[y:y + 2, 40:160] = 0
    cat, conf = rules.classify(Image.fromarray(arr, "RGB"), "scan001.jpg",
                               {"make": "Canon"}, CFG)
    assert cat == "document"
