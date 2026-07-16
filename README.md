# Photo Organizer

Windows / macOS에서 네트워크 드라이브의 대용량 사진(10만 장 이상)을 스캔해
**완전 중복 · 유사 사진 · 종류별 분류(스크린샷/문서/흔들린 사진) · 유사 그룹 내
베스트샷 선정**을 수행하는 GUI 프로그램.

## 핵심 원칙

- **비파괴성** — 자동 완전삭제 없음. 삭제는 항상 휴지통(기본) 또는 격리 폴더 이동이며,
  사용자 확인 후에만 실행. 격리는 앱 '되돌리기'로 복구.
- **재개 가능성** — 모든 진행 상태를 SQLite(WAL)에 기록. 중단 후 재시작 시 이어서 진행.
- **응답성** — 무거운 작업은 백그라운드(멀티프로세싱/QThread). UI 프리징 없음.
- **한글 완전 지원** — 파일명·경로·EXIF UTF-8.

## 파이프라인

```
scan(디스커버리) → dedup(완전중복: 크기→빠른해시→SHA-256)
 → analyze(read-once: pHash+썸네일+규칙분류 동시) → similar(BK-tree+union-find)
 → bestshot(품질 가중합, 그룹별 ⭐) → GUI 검토 → 안전 정리(휴지통/격리)
```

## 기능 현황

| Phase | 상태 | 산출물 |
|-------|------|--------|
| Phase 1 — 코어 엔진 | ✅ | scan · dedup · image_loader · CLI |
| Phase 2 — 유사도 & 분류 | ✅ | pHash · BK-tree 유사군 · 규칙 분류 |
| Phase 3 — 베스트샷 | ✅ (규칙 기반) | 품질 가중합 · 그룹별 베스트샷 선정 |
| Phase 4 — GUI (PySide6) | ✅ | 메인윈도우 · 워커 · 그룹 그리드 · 상세 패널 |
| Phase 5 — 안전 작업 | 🟡 진행 중 | 휴지통/격리 이동 · 되돌리기 · CSV/JSON · 증분 재스캔 |

## 기술 스택

- Python 3.11+ / GUI: PySide6
- 이미지: Pillow, pillow-heif, rawpy, opencv-python(4.x)
- 유사도: imagehash / (선택) 딥러닝: onnxruntime
- 저장: SQLite(WAL) / 병렬: multiprocessing
- 삭제 안전: send2trash / 패키징: pyinstaller

## 설치 & 실행

```bash
# 1) 가상환경
python -m venv .venv
source .venv/bin/activate         # macOS/Linux
# .venv\Scripts\activate          # Windows

# 2) 의존성 (코어만 / GUI 포함 선택)
pip install -e .                  # 코어
pip install -e ".[gui]"           # GUI 포함

# 3) 테스트
PYTHONPATH=src pytest             # (GUI 테스트는 QT_QPA_PLATFORM=offscreen)
```

### CLI

```bash
PYTHONPATH=src python -m photo_organizer.cli --db lib.db scan <경로>
PYTHONPATH=src python -m photo_organizer.cli --db lib.db scan <경로> --detect-deletions
PYTHONPATH=src python -m photo_organizer.cli --db lib.db dedup --workers 4
PYTHONPATH=src python -m photo_organizer.cli --db lib.db analyze --workers 4 --thumb-dir thumbs
PYTHONPATH=src python -m photo_organizer.cli --db lib.db similar
PYTHONPATH=src python -m photo_organizer.cli --db lib.db bestshot
PYTHONPATH=src python -m photo_organizer.cli --db lib.db report
```

### GUI

```bash
PYTHONPATH=src python -m photo_organizer.gui.app
```

## 문서

- `docs/SPEC.md` — 전체 스펙 (요구사항/설계/구현/테스트)
- `docs/HANDOFF.md` — 인수인계: 현재 상태·개발환경·아키텍처 결정·남은 작업
- `docs/TODO.md` — 단계별 구현 체크리스트
- `CLAUDE.md` — Claude Code용 프로젝트 가이드

## 라이선스

[MIT](LICENSE)
