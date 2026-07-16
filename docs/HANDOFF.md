# 인수인계 (HANDOFF) — 다음 세션용

> 이 문서를 **가장 먼저** 읽으세요. 현재까지의 진행 상황·개발 환경·아키텍처
> 결정·남은 작업을 한 곳에 정리했습니다. (작성 기준: 2026-07-15)

## 1. 한눈에 보는 진행 상황

| Phase | 상태 | 산출물 |
|-------|------|--------|
| Phase 1 — 코어 엔진 | ✅ 완료 | scan · dedup · image_loader · CLI |
| Phase 2 — 유사도 & 분류 | ✅ 완료 | phash · BK-tree 유사군 · 규칙 분류 · analyze(read-once) |
| Phase 3 — 베스트샷 | ✅ 규칙 기반 완료 (AI 보류) | quality · bestshot(프리셋·근거) |
| Phase 4 — GUI (PySide6) | ✅ 완료 | 메인윈도우 · 워커 · 그룹 그리드 · 상세 패널 · UX |
| Phase 5 — 안전 작업 | 🟡 핵심 완료 | 휴지통/격리 이동 · 되돌리기 · CSV/JSON |

**테스트: 68개 전부 통과.** (`docs/TODO.md`에 항목별 체크 상세)

## 2. 개발 환경 (중요 — 추측 금지)

- 시스템 macOS Python은 **3.9** 뿐이고 의존성도 없음. 절대 시스템 Python으로 실행하지 말 것.
- **zerobrew(`zb`)의 `python@3.14`** 로 만든 프로젝트 `.venv` 사용:
  - 인터프리터: `/opt/zerobrew/opt/python@3.14/bin/python3.14`
  - venv: `<repo>/.venv` (이미 생성됨, 의존성 설치 완료)
- 설치된 의존성: Pillow, pillow-heif, rawpy, imagehash, **opencv-python-headless 4.13**, numpy, send2trash, pytest, **PySide6 6.11**.
- `pyproject.toml`의 `requires-python`은 3.11(배포 타깃 Windows). 코드는 `from __future__ import annotations`로 3.14에서도 동작.

### 실행/검증 명령 (전부 `PYTHONPATH=src` 필요 — editable 설치 안 함)
```bash
# 테스트 (GUI 포함, 헤드리스)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
# CLI 파이프라인
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db scan <경로>
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db dedup --workers 4
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db analyze --workers 4 --thumb-dir thumbs
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db similar
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db bestshot
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db report            # 콘솔
PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db report --kind similar --json out.json
# GUI (사용자 Mac 화면에 창)
PYTHONPATH=src .venv/bin/python -m photo_organizer.gui.app
# 샘플 사진 재생성 (테스트용, 한글 폴더 17장)
PYTHONPATH=src .venv/bin/python scripts/make_sample_photos.py sample_photos
```

### GUI 헤드리스 검증 (화면 없는 세션에서 스크린샷)
`QT_QPA_PLATFORM=offscreen` 로 QApplication 생성 후 `window.grab().save("x.png")` →
그 PNG를 Read로 확인. 다크모드는 앱 팔레트를 다크로 세팅해 캡처.

## 3. 아키텍처 / 파이프라인

```
scan(디스커버리) → dedup(완전중복: 크기→빠른해시→SHA-256)
 → analyze(read-once: pHash+썸네일+규칙분류 동시) → similar(BK-tree+union-find)
 → bestshot(품질 가중합, 그룹별 ⭐) → GUI 검토 → 안전 정리(휴지통/격리)
```
- 저장소: SQLite(WAL). 모든 진행 상태 DB 기록 → 재개 가능.
- 코어(`src/photo_organizer/core/`): database, scanner, hasher, image_loader,
  config, platform_utils, actions
- 분류(`classify/`): phash, rules, analyze, similar, quality, bestshot
- GUI(`gui/`): app, main_window, workers(QThread), thumbnail_grid(+GroupedGrid), detail_panel

## 4. 굳어진 결정 사항 (되돌리지 말 것 — 이유 있음)

1. **크로스플랫폼 Windows/macOS 동등 지원**. 플랫폼 분기는 `core/platform_utils.py`에만
   격리(긴 경로 `\\?\`/UNC, 시스템 폴더 스킵).
2. **OpenCV는 반드시 4.x (`<5`)**. 5.0은 얼굴/눈 Haar cascade를 기본 포함에서 제거 →
   베스트샷 '눈 감음' 검출이 다운로드 없이 되려면 4.x 필요.
3. **PySide6/onnxruntime는 optional extras**(`gui`/`dl`). 코어(Phase1~2)만으로
   두 OS에서 바로 설치되게. onnxruntime는 3.14 wheel 없음(AI 단계에서 3.12 필요).
4. **스캔 = 누적(라이브러리) 모델**. 여러 폴더를 한 DB에 쌓음. '새로 시작' 버튼으로 초기화.
   앱은 DB 없으면 빈 상태로 시작.
5. **유사 탭에서 완전 중복은 대표로 접음(collapse)**. 한 문제는 한 탭에서만.
6. **UI 색 하드코딩 금지** — 테마 팔레트 사용(라이트/다크 자동 대응).
7. **그룹 표시 = [헤더 + 그룹 사진 한 줄]**(GroupedGrid). 배경 틴트 방식은 폐기(줄바꿈 시 경계 불명확).
8. **비파괴성**: 자동 완전삭제 없음. 휴지통(기본)/격리 이동 + action_log + 확인 다이얼로그.
   격리는 앱 '되돌리기' 복구, 휴지통은 OS에서 복구. `files.removed` 플래그로 뷰에서 숨김.
9. **AI(ONNX 분류·NIMA 미적점수) 보류** — 사용자 결정으로 규칙 기반 우선. 자리는 마련됨.

## 5. 남은 작업 (Phase 5 마감)

- [x] **증분 재스캔**: size+mtime 변경 감지(upsert+파생 무효화), 삭제 감지(`missing` 플래그, root 스코프 + 빈-walk 안전 가드), CLI `--detect-deletions`, GUI 배선.
- [x] **한글 NFC 경로 정규화**: macOS(NFD)↔Windows/NAS(NFC) 중복 오탐/재스캔 오판 방지. scanner walk+root NFC, DB 1회성 마이그레이션.
- [x] **그룹당 최소 1장 보존**: 제거 시 그룹이 비면 대표/베스트샷 보호(`Database.protected_survivors`, actions 3-tuple 반환).
- [x] **감사 로그 export**: CLI `report --kind actions` (CSV/JSON/콘솔).
- [x] **EXIF 버스트 그룹핑**: 촬영시각 근접 + 완화 pHash 임계값으로 연사 묶기(`exif_dt` 컬럼, config `burst_seconds`/`burst_hamming_threshold`).
- [x] **PyInstaller 패키징**: `photo_organizer.spec` + `packaging/pyinstaller_entry.py`. macOS `.app` 빌드·기동·
      `--selftest`(지연 import 포함) 검증 완료(PyInstaller 6.21, PySide6 6.11, py3.14 OK). onnxruntime은 3.14 wheel
      없어 제외(AI 보류). Windows `.exe`는 동일 spec을 Windows에서 빌드(크로스컴파일 불가). 서명/공증·SmartScreen
      대응은 `docs/PACKAGING.md` 참조. 남은 개선: DB/썸네일 경로를 실행 위치 → 사용자 쓰기폴더로.
- [x] **10만 장 성능/부하 테스트**: `scripts/benchmark.py`(하이브리드 — 알고리즘은 합성 DB 100k, scan은 빈 파일 100k).
      결과(macOS py3.14): scan 0.84s · 재스캔/삭제감지 ~0.7s · protected_survivors 0.09s · report 0.09s · DB 19MB · 피크 RSS 123MB — 모두 우수.
      **병목이던 유사 클러스터링을 BK-tree→멀티인덱스 해싱으로 교체해 524s→1.6s(약 330배, 결과 불변).**
      I/O 단계는 `--real-n`/`--real-size`로 실이미지 실측+외삽(single-process, 로컬 SSD, macOS py3.14):
      · 128px(~0MP): dedup 0.07ms·analyze 0.70ms/파일
      · **3000px(~9MP, 폰 사진급): dedup 0.61ms(100k~61s) · analyze 75.5ms/파일 → 100k ~126분 single-process,
        workers=8이면 ~16분** · bestshot 무시 수준. (24~50MP는 이에 비례해 더 큼.)
      결론: **초선형(O(N²)) 병목 없음** — 100k의 비용은 analyze의 이미지 디코드가 지배하며 멀티프로세싱으로
      코어 수만큼 병렬화됨(순수 CPU/드라이브 throughput 문제). 클러스터링 자체는 1.6s로 무시 수준.
      버스트 O(N²) 엣지는 `burst_max_window`(기본 200) 앵커당 상한으로 가드됨(union 전이성으로 정확성 보존).
- [ ] 추가 개선 백로그: 유사도 슬라이더·나란히 비교 뷰·키보드 컬링·ETA·`Haar→MediaPipe` 등은
      `docs/benchmarking-2026-07.md`(경쟁 벤치마킹, 미커밋 working note) 참조.

## 6. 알아둘 점 / 함정

- 테스트에서 휴지통은 `monkeypatch`로 `send2trash`를 대체(실제 OS 휴지통 오염 방지).
- 격리/되돌리기 테스트는 원본을 실제 이동하므로 tmp 파일에서만. `sample_photos` 직접 쓰지 말 것
  (지우고 싶으면 `scripts/make_sample_photos.py`로 재생성).
- 합성 이미지는 저주파 구조가 비슷하면 pHash가 가까워 한 유사군으로 묶임(정상 동작).
  "다른 사진" 데모가 필요하면 구조(그라디언트 방향·블록 배치)를 확 다르게.
- 산출물(`*.db`, `thumbnails/`, `sample_photos/`, `격리보관함/`)은 `.gitignore` 처리됨.
