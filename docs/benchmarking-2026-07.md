# 경쟁 제품 벤치마킹 & 기능 추천 (2026-07)

> 4개 조사팀(상용 유료 · 무료/오픈소스 · AI 컬링/베스트샷 · UX/기능격차)의 웹 조사 결과를
> photo-organizer 실제 코드와 대조해 정리한 문서. 가격/수치는 조사 시점(2026-07) 기준이며 변동 가능.

## 1. 경쟁 지형 요약

| 카테고리 | 대표 제품 | 강점 | 우리 대비 시사점 |
|---|---|---|---|
| 상용 유료 | Duplicate Cleaner Pro, PhotoSweeper, ACDSee, Lightroom, Mylio | 완성된 UX, 라이브러리 연동, (일부)AI 컬링 | **완전중복만 잘하고 perceptual 유사는 알고리즘 비공개/약함**이 다수 → 우리 pHash+BK-tree는 이미 경쟁력 |
| 무료/오픈소스 | Czkawka, digiKam, dupeGuru, fclones, rmlint | 고성능(Rust), 알고리즘 공개, 자동선택 프리셋 | rmlint의 "삭제 안 하고 실행계획만 생성" 철학이 우리 비파괴 원칙과 동일 노선 |
| AI 컬링 | Aftershoot, Narrative Select, FilterPixel, Excire, Optyx | focus/eye/표정/미적 점수 자동 랭킹 | 진짜 기능 격차 지점. 단 Aftershoot/Narrative/Excire 모두 **완전 로컬** = 우리 철학 유지하며 도입 가능 |
| 수동 초고속 | Photo Mechanic Plus | 키보드 컬링 워크플로우, 즉시 로딩 | 키보드 중심 리뷰 UX 벤치마크 |

**핵심 발견 3가지**
1. 상용 중복찾기(ACDSee/Lightroom/Mylio)조차 대부분 **완전 일치 중복만** 확실히 처리 → 우리 유사 검출은 우위.
2. **크래시 후 SQLite 재개**는 조사한 어떤 경쟁 도구에서도 확인되지 않은 우리만의 차별점(Apple/Excire조차 대용량에서 인덱싱이 수일~수주 걸린다는 사용자 보고 다수).
3. ACDSee는 **네트워크 드라이브 삭제 시 휴지통 우회 = 영구삭제**라는 치명적 약점 → NAS 타깃인 우리가 확실히 우위를 점할 지점.

## 2. photo-organizer 현재 위치 (코드 대조 확인)

**이미 강점 (유지/홍보 가치)**
- 비파괴: 휴지통(기본)/격리 이동 + action_log + 되돌리기, 자동 완전삭제 없음.
- 재개 가능: SQLite(WAL) 진행상태 기록 → 경쟁 우위.
- 유사 검출: pHash + BK-tree + union-find.
- 증분 재스캔(방금 추가): size+mtime 변경 감지 + 삭제 감지(안전 가드 포함).
- **베스트샷 근거 이미 표시**: `detail_panel`의 "베스트샷 근거" + 그리드 툴팁(`reason`). → 이건 격차 아님.
- 유사도 임계값 `hamming_threshold` config/CLI 조절 가능.

**확인된 격차**
- 한글 **NFC/NFD 정규화 미처리** (`unicodedata` 미사용). ⚠️ 정확도 직결.
- GUI에 유사도 임계값 **슬라이더 없음**(config/CLI로만).
- **나란히 비교 뷰 없음**.
- **ETA/처리량(files/sec) 표시 없음**(진행 개수만).
- 사용자 조정형 **유지(keep) 규칙 프리셋 없음**(현재 keep = 계산된 대표/베스트샷 고정).
- 키보드 컬링 워크플로우, "다음 미검토 그룹" 네비 없음.
- 버스트/시리즈(촬영시각 기반) 그룹핑 별도 축 없음.

## 3. 우선순위별 기능 추천 (코드 근거 반영)

철학 기준: **비파괴 · 규칙기반/로컬 우선 · 무구독**. AI는 온디바이스로만.

### Tier 1 — Quick Win (난이도 하, 가치 큼, 철학 부합)

1. **한글 NFC 경로 정규화** ⭐ (난이도: 하 / 정확도 필수)
   - 문제: macOS(APFS)는 NFD(자모 분리), Windows/대부분 NAS는 NFC(음절 결합). 정규화 없이 경로를 DB에 저장하면 (a)같은 파일이 두 경로로 중복 기록, (b)증분 재스캔이 변경/신규를 오판, (c)한글 파일명 mojibake.
   - 조치: `scanner`가 경로를 DB에 기록하기 전 `unicodedata.normalize("NFC", path)` 적용(조회 시에도 일관). NAS·한글이 핵심 타깃이라 최우선.
   - 근거: haah.kr, bandisoft(APFS NFD), 우리 코드에 정규화 부재 확인.

2. **그룹당 "최소 1장 보존" 안전장치** (난이도: 하 / 규칙기반)
   - 유사 그룹 전원이 흐림/눈감음이라도 상대 1등은 항상 keep 후보로 남겨 조용한 전량 격리 방지. Aftershoot 핵심 설계. 비파괴 철학과 완벽 정합.

3. **유사 그룹핑에 EXIF 촬영시각 결합(버스트 인식)** (난이도: 하 / 규칙기반)
   - pHash 거리 + `DateTimeOriginal` 근접(초 단위)으로 "같은 순간 다른 포즈" 버스트를 정확히 묶기. EXIF는 이미 읽으므로 거의 공짜. Google Photo Stacks·Optyx Autogroup 방식.

4. **GUI 유사도 임계값 슬라이더** (난이도: 하~중 / 규칙기반)
   - `hamming_threshold`를 실시간 슬라이더로 노출 + Czkawka식 프리셋(매우높음/높음/보통…). BK-tree가 이미 거리 기반이라 파라미터+UI 바인딩만.

5. **삭제/격리 감사 로그 내보내기(CSV/JSON)** (난이도: 하)
   - action_log를 "무엇을 언제 어디로" 리스트로 export. 이미 CSV/JSON 내보내기 인프라 있음 → 확장. 비파괴·감사 신뢰성 강화.

6. **네트워크 드라이브 삭제 안전성 검증 + 격리 fallback** (난이도: 하)
   - send2trash가 UNC/네트워크 경로에서 실제 휴지통 이동을 하는지 검증하고, 불가 시 격리 폴더로 fallback 보장(회귀 테스트 추가). ACDSee 약점을 우리 강점으로.

### Tier 2 — 중간 투자 (난이도 중, UX 경쟁력)

7. **나란히/오버레이 비교 뷰** (난이도: 중 / 규칙기반 UI)
   - PhotoSweeper Face-to-Face·Lightroom Survey/Compare. 유사 그룹 최종 판단 핵심 UX. PySide6 QGraphicsView/그리드, 로컬 이미지 로더 이미 보유.

8. **키보드 컬링 워크플로우 + "다음 미검토 그룹" 네비 + ETA** (난이도: 중)
   - P=유지/X=제거/←→/자동 다음. 10만 장 리뷰 속도 좌우. ETA·files/sec 표시로 대기 신뢰(VisiPics급). Photo Mechanic·Narrative 벤치마크.

9. **사용자 조정형 베스트샷 가중치 + 장르 프리셋** (난이도: 중 / 규칙기반)
   - 현재 고정 가중합을 슬라이더로 조절(선명도/눈감음/노출), 인물/이벤트/풍경 프리셋. Optyx 가중치 조절·Excire Smart Selection 방식. 규칙기반이라 설명가능성 우위.

10. **blur 2단계 판정 + 얼굴/눈 영역 선명도 분리** (난이도: 중 / 규칙기반)
    - 1차 Laplacian 분산(전량) → borderline만 FFT 재검(저텍스처 벽 오판 완화). 얼굴 bbox 내 선명도를 별도 스코어(인물에서 배경만 선명한 실패샷 감점). DPReview가 지적한 상용 blur 오판 문제를 정면 완화.

### Tier 3 — 큰 투자 / 온디바이스 AI (Phase 3 ONNX 자리 활용)

11. **Haar cascade → MediaPipe Face Landmarker 교체** ⭐ (난이도: 중, 비용대비 최고)
    - blendshapes로 `eyeBlink` + 미소 계수를 CPU 한 패스로. Haar의 정면/안경 취약점 해결 + 표정 신호 무료 획득. 성숙한 로컬 라이브러리.

12. **NIMA/CLIP 미적 점수** (난이도: 상 / 보류된 ONNX 자리)
    - MobileNet-NIMA를 ONNX로. Excire 100점 미적점수 등 상용 차별점. 10만 장 배치엔 MobileNet 계열이 현실적.

13. **소형 임베딩(DINOv2 ViT-S) 버스트 그룹핑 보강** (난이도: 상)
    - pHash 1차 버킷 → 버킷 내 임베딩 코사인 클러스터링. pHash가 놓치는 포즈 변화 버스트 포착(보조).

### 배제/보류 (철학 상충)

- **얼굴 인식(인물별 그룹)**: 업계 표준이나 우리 AI보류·프라이버시 철학과 충돌. 로컬이면 일부 타협 가능하나 보류 지속.
- **Best-Take식 얼굴 합성**: 픽셀 재작성 = 비파괴 원칙 정면 충돌. **의도적으로 안 하는 것이 차별점**.
- **클라우드 장르 딥컬(FilterPixel식)**: 로컬 철학상 배제.
- **gaze/시선 검출**: 미성숙·고난도, stretch goal.

## 4. 배포(Phase 5 PyInstaller) 주의

- PyInstaller 부트로더가 **AV 오탐**을 자주 유발 → 코드 서명으로 완화.
- macOS는 **공증(notarization)** 사실상 필수(Gatekeeper). `base_library.zip` 경로 함정 주의.
- Windows **SmartScreen**은 EV 인증서도 즉시 평판을 주지 않음(다운로드 누적 필요) / MSIX·Store 우회.
- 자동 업데이트: macOS Sparkle, Windows Squirrel, Python용 PyUpdater.

## 5. 종합 제언

1. **가장 시급(정확도)**: 한글 NFC 정규화(#1). NAS·크로스플랫폼·한글 타깃에서 미처리 시 중복 오탐 직결.
2. **철학을 무기로**: 근거 표시(이미 있음)·감사 로그(#5)·"AI-assisted 제안(강제삭제 아님)"은 규칙기반/비파괴라 오히려 AI 경쟁사보다 설명가능성에서 유리 — 신뢰 스토리로 홍보.
3. **저비용 고효과 UX**: 비교 뷰(#7)·키보드 컬링(#8)은 Phase 4 GUI에서 상대적 저비용으로 큰 이득.
4. **AI 도입은 온디바이스 한정**: #11(MediaPipe) → #12(NIMA) 순. 경쟁사도 완전 로컬로 하므로 철학 훼손 없음.
5. **차별점 방어**: SQLite 재개·NAS 삭제 안전성은 경쟁 공백 → 명시적으로 지키고 홍보.

## 6. 주요 출처

- 상용: digitalvolcano.co.uk, overmacs.com(PhotoSweeper), excire.com, mylio.com, acdsee.com, helpx.adobe.com/lightroom-classic
- 오픈소스: github.com/qarmin/czkawka, docs.digikam.org, dupeguru.voltaicideas.net, github.com/pkolaczk/fclones, rmlint.readthedocs.io, github.com/idealo/imagededup
- AI 컬링: aftershoot.com, narrative.so, filterpixel.com, optyx.app, excire.com, home.camerabits.com, blog.google(Best Take), support.apple.com(Duplicates)
- 기술 레퍼런스: developers.google.com/edge/mediapipe(Face Landmarker), github.com/idealo/image-quality-assessment(NIMA), pyimagesearch.com(FFT blur/EAR), burstpick.app/models
- i18n: haah.kr, bandisoft(APFS NFD/NFC), exiftool.org(IPTC/XMP UTF-8)
- 배포: pythonguis.com(PyInstaller AV), learn.microsoft.com(SmartScreen), pyupdater.org

> 신뢰도 한계: 경쟁사 내부 모델 아키텍처는 대부분 비공개, 가격/속도 벤치마크는 벤더 주장과 실측이 상충하는 경우 많음. 어떤 도구도 10만+ 장 공개 검증 벤치마크가 없어 우리가 자체 벤치마크로 공백을 채울 여지가 있음.
