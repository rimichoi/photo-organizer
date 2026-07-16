"""베스트샷 선정 (docs/SPEC.md 4.4a, FR-07a/07b).

유사 그룹마다 품질 지표를 가중 합산해 최고점 사진을 ⭐베스트샷으로 추천하고,
선정 근거를 생성한다. 자동 선정은 어디까지나 추천이며 최종 결정은 사용자 몫
(GUI에서 교체 가능 — Phase 4).

- 지표: classify/quality.py (선명도·노출·대비·눈감음)
- 프리셋: 그룹에 얼굴이 있으면 person, 없으면 landscape 자동 선택(사용자 override 가능)
- 정규화: 선명도는 상한이 없어 그룹 내 최댓값으로 나눠 [0,1]로 맞춘다.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from PIL import Image

from ..core.config import Config
from ..core.database import Database
from . import quality


def _load_thumb(path: str | None) -> Optional[Image.Image]:
    if not path:
        return None
    try:
        return Image.open(path).convert("RGB")
    except (OSError, ValueError):
        return None


def _pick_preset(metrics_list: list[dict], override: str | None, cfg: Config) -> str:
    if override in cfg.quality_presets:
        return override
    # 그룹에 얼굴이 하나라도 있으면 인물 프리셋
    if any(m.get("has_face") for m in metrics_list):
        return "person"
    return "landscape"


def _score_group(metrics_list: list[dict], weights: dict) -> tuple[list[float], list[str]]:
    """그룹 구성원별 (최종점수, 근거문자열)을 계산한다.

    선명도는 그룹 내 최댓값으로 정규화. eyes_open이 None인 구성원은 그 지표를
    빼고 가중치를 재정규화해 공정하게 비교한다.
    """
    max_sharp = max((m["sharpness"] for m in metrics_list), default=0.0)

    scores: list[float] = []
    normalized: list[dict] = []
    for m in metrics_list:
        norm = {
            "sharpness": (m["sharpness"] / max_sharp) if max_sharp > 0 else 0.0,
            "exposure": m["exposure"],
            "contrast": m["contrast"],
        }
        if m["eyes_open"] is not None:
            norm["eyes_open"] = m["eyes_open"]
        # 활성 지표에 대해서만 가중치 재정규화
        active = {k: weights.get(k, 0.0) for k in norm}
        wsum = sum(active.values())
        if wsum <= 0:
            score = sum(norm.values()) / len(norm)  # 가중치가 모두 0이면 균등 평균
        else:
            score = sum(norm[k] * active[k] for k in norm) / wsum
        scores.append(round(score, 4))
        normalized.append(norm)

    # 근거 문자열: 그룹 내 순위/임계 기반
    best_idx = max(range(len(scores)), key=lambda i: scores[i]) if scores else -1
    reasons: list[str] = []
    for i, (m, norm) in enumerate(zip(metrics_list, normalized)):
        parts: list[str] = []
        if i == best_idx:
            parts.append("종합 1위")
        if norm["sharpness"] >= 0.999 and max_sharp > 0:
            parts.append("선명도 1위")
        if m["eyes_open"] is not None:
            parts.append("눈 뜸" if m["eyes_open"] >= 0.5 else "눈 감음 주의")
        if m["exposure"] >= 0.85:
            parts.append("노출 양호")
        if m["contrast"] >= 0.6:
            parts.append("대비 좋음")
        reasons.append(" · ".join(parts) if parts else "—")
    return scores, reasons


def run_bestshot(
    db: Database,
    cfg: Optional[Config] = None,
    preset: str | None = None,
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """모든 유사 그룹에 대해 베스트샷을 선정해 DB에 기록한다. 처리한 그룹 수 반환."""
    cfg = cfg or Config()

    # 그룹별 구성원(썸네일 경로 포함) 수집
    groups: dict[int, list[tuple[int, str]]] = {}
    for r in db.iter_similar_members_with_thumbs():
        groups.setdefault(r["group_id"], []).append((r["file_id"], r["thumb_path"]))

    if not groups:
        if progress is not None:
            progress("유사 그룹이 없습니다. 먼저 similar 를 실행하세요.")
        return 0

    updates: list[tuple] = []  # (quality_score, is_best_shot, quality_detail, group_id, file_id)
    for gid, members in groups.items():
        metrics_list: list[dict] = []
        for _fid, thumb in members:
            img = _load_thumb(thumb)
            metrics_list.append(
                quality.compute_metrics(img) if img is not None
                else {"sharpness": 0.0, "exposure": 0.0, "contrast": 0.0,
                      "eyes_open": None, "has_face": False}
            )

        preset_name = _pick_preset(metrics_list, preset, cfg)
        weights = cfg.quality_presets[preset_name]
        scores, reasons = _score_group(metrics_list, weights)
        best_idx = max(range(len(scores)), key=lambda i: scores[i])

        for i, (fid, _thumb) in enumerate(members):
            detail = {
                "preset": preset_name,
                "sharpness": round(metrics_list[i]["sharpness"], 2),
                "exposure": round(metrics_list[i]["exposure"], 3),
                "contrast": round(metrics_list[i]["contrast"], 3),
                "eyes_open": metrics_list[i]["eyes_open"],
                "score": scores[i],
                "reason": reasons[i],
            }
            updates.append((
                scores[i], 1 if i == best_idx else 0,
                json.dumps(detail, ensure_ascii=False), gid, fid,
            ))

    db.set_bestshot_results(updates)
    if progress is not None:
        progress(f"베스트샷 선정 완료: {len(groups)}개 그룹")
    return len(groups)
