"""분류·유사도 임계값 설정 (docs/SPEC.md 4.4, FR-12).

기본값은 코드에 두고, 선택적으로 TOML 파일로 덮어쓴다. 임계값은 이미지마다
편차가 크므로 사용자가 조정 가능해야 한다(SPEC 3.4). multiprocessing 워커로
넘길 수 있도록 ``to_dict``/``from_dict``로 평범한 dict 직렬화를 지원한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # --- 유사도 (classify/similar.py) ---
    hamming_threshold: int = 5          # pHash 해밍 거리 이내면 유사로 간주
    phash_bits: int = 64                # pHash 비트 수 (imagehash 기본 8x8)

    # --- 썸네일 (classify/phash.py) ---
    thumb_size: int = 256               # 썸네일 최대 변(px)

    # --- 블러 판정 (classify/rules.py) ---
    blur_laplacian_threshold: float = 100.0   # 라플라시안 분산 < 이 값이면 흐림 후보
    blur_min_brightness: float = 40.0         # 이보다 어두우면 "어두운 사진"일 수 있어 신뢰도 down

    # --- 문서 판정 ---
    document_white_ratio: float = 0.55        # 밝은 배경 비율 하한
    document_min_edge_density: float = 0.02   # 엣지 밀도 하한 (문서 테두리/텍스트)

    # --- 스크린샷 판정 ---
    # 파일명 패턴(강한 신호)이 최우선. 그 외에는 "카메라 EXIF 없음 + 정확한
    # 화면 해상도 일치"라는 결합 신호만 스크린샷으로 본다("카메라 없음" 단독은
    # EXIF 없는 일반 사진을 오탐하므로 사용하지 않는다 — SPEC 3.4/6.3 정밀도).
    screenshot_name_patterns: tuple[str, ...] = (
        "screenshot", "screen shot", "화면 캡처", "화면캡처", "스크린샷", "scr_",
    )
    # 알려진 화면 해상도(가로 기준). 세로 방향은 자동으로 함께 검사한다.
    screenshot_resolutions: tuple[tuple[int, int], ...] = (
        (1280, 720), (1366, 768), (1440, 900), (1536, 864), (1600, 900),
        (1920, 1080), (1920, 1200), (2560, 1440), (2560, 1600), (3840, 2160),
        (750, 1334), (828, 1792), (1080, 1920), (1080, 2340), (1125, 2436),
        (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1440, 3200),
    )

    # --- 베스트샷 품질 가중치 (classify/bestshot.py, FR-07a/07b) ---
    # 프리셋별 지표 가중치. 인물은 눈감음·선명도, 풍경은 대비·선명도 비중이 높다.
    # 그룹에 얼굴이 있으면 person, 없으면 landscape를 자동 선택한다.
    quality_presets: dict = field(default_factory=lambda: {
        "person":    {"sharpness": 0.30, "exposure": 0.20, "contrast": 0.10, "eyes_open": 0.40},
        "landscape": {"sharpness": 0.40, "exposure": 0.30, "contrast": 0.30, "eyes_open": 0.00},
    })

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for k, v in data.items():
            if k not in known:
                continue
            # TOML/직렬화에서 list로 온 값을 tuple로 정규화
            if k == "screenshot_name_patterns" and isinstance(v, list):
                v = tuple(v)
            if k == "screenshot_resolutions" and isinstance(v, list):
                v = tuple(tuple(pair) for pair in v)
            kwargs[k] = v
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """TOML 설정을 읽어 Config를 만든다. 경로가 없거나 파일이 없으면 기본값."""
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        import tomllib

        with open(p, "rb") as f:
            data = tomllib.load(f)
        # [thresholds] 섹션이 있으면 그 안을, 없으면 최상위를 사용
        section = data.get("thresholds", data)
        return cls.from_dict(section)
