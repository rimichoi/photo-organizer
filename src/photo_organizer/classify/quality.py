"""베스트샷용 품질 지표 계산 (docs/SPEC.md 4.4a, FR-07a).

유사 그룹 내에서 "제일 잘 나온 사진"을 고르기 위한 지표들. 모두 규칙 기반
(빠름)이며 순수 함수다. 딥러닝 미적 점수(NIMA)는 이후 단계에서 이 점수에
가중 합산으로 얹을 수 있도록 자리를 비워둔다.

지표는 로컬에 캐시된 썸네일 위에서 계산한다(네트워크 재접근 없음 — SPEC 5.1).
그룹 내 사진들은 모두 같은 크기로 다운스케일되므로 상대 비교가 공정하다.
"""
from __future__ import annotations

import os

import cv2
import numpy as np
from PIL import Image

_face_cascade: cv2.CascadeClassifier | None = None
_eye_cascade: cv2.CascadeClassifier | None = None


def _load_cascades() -> tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]:
    """OpenCV 내장 Haar cascade(얼굴/눈)를 지연 로딩한다."""
    global _face_cascade, _eye_cascade
    if _face_cascade is None:
        base = cv2.data.haarcascades
        _face_cascade = cv2.CascadeClassifier(
            os.path.join(base, "haarcascade_frontalface_default.xml")
        )
        _eye_cascade = cv2.CascadeClassifier(
            os.path.join(base, "haarcascade_eye.xml")
        )
    return _face_cascade, _eye_cascade


def _gray(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)


def sharpness(gray: np.ndarray) -> float:
    """라플라시안 분산 = 선명도(초점/흔들림). 높을수록 선명. 상한 없음 → 그룹 내 정규화 필요."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def exposure_score(gray: np.ndarray) -> float:
    """노출 점수 [0,1]. 완전 검정/완전 흰색으로 날아간(clipping) 픽셀이 많을수록 감점."""
    total = gray.size
    clip_low = float(np.count_nonzero(gray < 5)) / total
    clip_high = float(np.count_nonzero(gray > 250)) / total
    return max(0.0, 1.0 - (clip_low + clip_high))


def contrast_score(gray: np.ndarray) -> float:
    """RMS 대비 점수 [0,1]. 표준편차 기반. 밋밋한 사진일수록 낮다."""
    return float(min(1.0, gray.std() / 64.0))


def eyes_open(gray: np.ndarray) -> tuple[bool, float | None]:
    """(얼굴 있음?, 눈뜬 얼굴 비율). 얼굴이 없으면 (False, None) — 인물 지표 미적용."""
    face_c, eye_c = _load_cascades()
    faces = face_c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    if len(faces) == 0:
        return False, None
    open_faces = 0
    for (x, y, w, h) in faces:
        roi = gray[y:y + h, x:x + w]
        eyes = eye_c.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=5)
        if len(eyes) >= 1:
            open_faces += 1
    return True, open_faces / len(faces)


def compute_metrics(img: Image.Image) -> dict:
    """한 이미지의 원시 품질 지표를 계산한다.

    eyes_open은 얼굴이 없으면 None(해당 지표 제외). sharpness는 상한이 없어
    그룹 내 정규화가 필요하므로 원시값을 그대로 담는다.
    """
    gray = _gray(img)
    has_face, eyes = eyes_open(gray)
    return {
        "sharpness": sharpness(gray),
        "exposure": exposure_score(gray),
        "contrast": contrast_score(gray),
        "eyes_open": eyes,      # None = 얼굴 없음
        "has_face": has_face,
    }
