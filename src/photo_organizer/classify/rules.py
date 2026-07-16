"""규칙 기반 1차 분류 (docs/SPEC.md 4.4).

스크린샷 / 문서 / 흔들린(블러) / 일반 을 빠른 규칙으로 판정한다. 각 판정은
(category, confidence)를 돌려주며, confidence가 낮은 항목은 Phase 3에서 딥러닝
2차 확인 대상이 된다. 모든 함수는 순수(이미지·메타 → 값)라 단위 테스트가 쉽다.

이미지는 정규화된 PIL RGB를 받아 OpenCV(numpy) 배열로 변환해 분석한다.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from ..core.config import Config


def _to_gray(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)


def laplacian_variance(gray: np.ndarray) -> float:
    """라플라시안 분산 = 선명도 지표. 낮을수록 흐림/흔들림(SPEC 부록 B)."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def white_ratio(gray: np.ndarray, threshold: int = 200) -> float:
    """밝은(문서 배경) 픽셀 비율."""
    return float(np.mean(gray > threshold))


def edge_density(gray: np.ndarray) -> float:
    """Canny 엣지 픽셀 비율 = 직선 테두리·텍스트 밀도의 근사."""
    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges > 0))


def has_camera_info(meta: dict) -> bool:
    return bool(meta.get("make") or meta.get("model"))


def is_screen_resolution(size: tuple[int, int], cfg: Config) -> bool:
    """이미지 크기가 알려진 화면 해상도(가로/세로 양방향)와 정확히 일치하는지."""
    w, h = size
    for sw, sh in cfg.screenshot_resolutions:
        if (w, h) == (sw, sh) or (w, h) == (sh, sw):
            return True
    return False


def classify(img: Image.Image, filename: str, meta: dict, cfg: Config) -> tuple[str, float]:
    """이미지를 (category, confidence)로 분류한다.

    우선순위: 스크린샷 → 문서 → 블러 → 일반. 먼저 매칭된 규칙을 채택한다.
    """
    gray = _to_gray(img)
    name = filename.lower()

    # 1) 스크린샷:
    #    (a) 파일명 패턴 = 가장 강한 신호(정밀도 높음)
    #    (b) 카메라 EXIF 없음 + 정확한 화면 해상도 일치 = 결합 신호
    #    "카메라 없음" 단독은 EXIF 없는 일반 사진 오탐이 커서 쓰지 않는다.
    name_hit = any(pat in name for pat in cfg.screenshot_name_patterns)
    if name_hit:
        conf = 0.95 if not has_camera_info(meta) else 0.75
        return "screenshot", conf
    if not has_camera_info(meta) and is_screen_resolution(img.size, cfg):
        return "screenshot", 0.7

    # 2) 문서/영수증: 밝은 배경 + 엣지(테두리/텍스트) 밀도.
    wr = white_ratio(gray)
    if wr >= cfg.document_white_ratio and edge_density(gray) >= cfg.document_min_edge_density:
        conf = 0.8 if wr > 0.7 else 0.6
        return "document", conf

    # 3) 블러: 라플라시안 분산이 낮음. 단, 어두운 사진과 구분(SPEC 4.4).
    lap = laplacian_variance(gray)
    if lap < cfg.blur_laplacian_threshold:
        brightness = float(gray.mean())
        if brightness < cfg.blur_min_brightness:
            # 어두워서 흐릿해 보일 수 있음 → 신뢰도 낮춰 DL 2차로 넘김
            return "blurry", 0.4
        return "blurry", 0.85

    # 4) 어디에도 강하게 걸리지 않으면 일반 사진.
    #    카메라 EXIF가 없으면 확신을 낮춰 Phase 3 DL 2차 확인 대상으로 남긴다.
    return "normal", 0.9 if has_camera_info(meta) else 0.6
