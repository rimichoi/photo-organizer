"""Perceptual hash 계산 + 썸네일 생성 (docs/SPEC.md 4.3 [3]).

순수 함수만 둔다(이미지 → 결과). DB 접근·병렬화는 analyze.py가 담당한다.
- pHash/dHash: imagehash로 계산해 16진 문자열로 저장(유사도용).
- 썸네일: 유사 그룹 검토(Phase 4)와 재접근 최소화(SPEC 5.1)를 위해 캐시.
"""
from __future__ import annotations

import os

import imagehash
from PIL import Image


def compute_hashes(img: Image.Image) -> tuple[str, str]:
    """정규화된 이미지의 (pHash, dHash) 16진 문자열을 반환한다.

    pHash는 리사이즈·재압축에 강하고, dHash는 그라디언트 기반으로 상호 보완적.
    두 값 모두 저장해 Phase 2 유사 판정의 정밀도를 높인다.
    """
    return str(imagehash.phash(img)), str(imagehash.dhash(img))


def save_thumbnail(img: Image.Image, thumb_dir: str, file_id: int, size: int) -> str:
    """썸네일을 thumb_dir/{file_id}.jpg 로 저장하고 경로를 반환한다."""
    thumb = img.copy()
    thumb.thumbnail((size, size))
    path = os.path.join(thumb_dir, f"{file_id}.jpg")
    thumb.save(path, "JPEG", quality=85)
    return path
