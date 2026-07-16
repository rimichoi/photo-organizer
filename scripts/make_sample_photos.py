"""테스트용 샘플 사진 생성기.

모든 기능을 시연할 수 있는 합성 사진 세트를 만든다:
- 완전 중복(바이트 동일), 유사(리사이즈/재압축), 연사(선명/흔들림 → 베스트샷)
- 카테고리: 일반 사진 / 문서 / 스크린샷 / 흔들린 사진
- 한글 폴더 + 재귀 구조

사용:
    PYTHONPATH=src .venv/bin/python scripts/make_sample_photos.py [출력폴더]
기본 출력폴더: ./sample_photos
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _canvas(size=(640, 480)) -> Image.Image:
    return Image.new("RGB", size, (0, 0, 0))


def _add_grain(img: Image.Image, amount: int = 8) -> Image.Image:
    """약한 노이즈를 더해 라플라시안 분산이 자연스럽게 잡히도록."""
    arr = np.asarray(img).astype(np.int16)
    noise = np.random.RandomState(1).randint(-amount, amount + 1, arr.shape)
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8), "RGB")


def _save_photo(img: Image.Image, path: Path, make="Canon", model="EOS 80D", quality=92):
    """카메라 EXIF를 넣어 저장(→ 일반 사진으로 분류되도록)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = model
    img.save(path, quality=quality, exif=exif)


def _save_plain(img: Image.Image, path: Path, **kw):
    """EXIF 없이 저장(스크린샷/문서용)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, **kw)


# ---------- 장면(scene) 그리기 ----------

def beach() -> Image.Image:
    img = _canvas()
    a = np.asarray(img).copy()
    for y in range(480):
        if y < 200:      # 하늘 (주황→하늘색 노을)
            t = y / 200
            a[y, :] = (int(255 - 40 * t), int(140 + 60 * t), int(60 + 140 * t))
        elif y < 320:    # 바다
            a[y, :] = (30, 90, 160)
        else:            # 모래
            a[y, :] = (210, 190, 150)
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    d.ellipse([500, 60, 580, 140], fill=(255, 240, 180))  # 해
    return _add_grain(img)


def mountain() -> Image.Image:
    img = _canvas()
    a = np.asarray(img).copy()
    for y in range(480):
        t = y / 480
        a[y, :] = (int(120 + 100 * t), int(170 + 60 * t), 235)  # 하늘
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    d.polygon([(0, 480), (200, 180), (380, 480)], fill=(90, 110, 80))
    d.polygon([(260, 480), (460, 220), (640, 480)], fill=(70, 95, 70))
    return _add_grain(img)


def park() -> Image.Image:
    img = _canvas()
    a = np.asarray(img).copy()
    a[:280, :] = (150, 200, 245)   # 하늘
    a[280:, :] = (80, 160, 70)     # 잔디
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    d.rectangle([300, 150, 340, 320], fill=(110, 70, 40))  # 나무 기둥
    d.ellipse([250, 90, 400, 220], fill=(40, 130, 50))     # 나무 잎
    return _add_grain(img)


def city() -> Image.Image:
    img = _canvas()
    a = np.asarray(img).copy()
    a[:, :] = (60, 70, 100)
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    rng = np.random.RandomState(5)
    x = 20
    while x < 620:
        w = rng.randint(40, 80); h = rng.randint(120, 340)
        d.rectangle([x, 480 - h, x + w, 480], fill=tuple(rng.randint(40, 120, 3).tolist()))
        for wy in range(480 - h + 12, 470, 24):     # 창문 불빛
            for wx in range(x + 8, x + w - 8, 18):
                if rng.random() > 0.4:
                    d.rectangle([wx, wy, wx + 8, wy + 12], fill=(250, 230, 150))
        x += w + 12
    return _add_grain(img)


def flower() -> Image.Image:
    img = _canvas()
    a = np.asarray(img).copy()
    a[:, :] = (40, 90, 40)
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    for cx, cy, c in [(200, 200, (230, 80, 120)), (420, 260, (240, 200, 60)),
                      (300, 340, (200, 100, 220))]:
        for ang in range(0, 360, 45):
            import math
            dx, dy = 42 * math.cos(math.radians(ang)), 42 * math.sin(math.radians(ang))
            d.ellipse([cx + dx - 26, cy + dy - 26, cx + dx + 26, cy + dy + 26], fill=c)
        d.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=(250, 230, 90))
    return _add_grain(img)


def night_scene() -> Image.Image:
    img = _canvas()
    a = np.asarray(img).copy()
    a[:, :] = (15, 15, 40)
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    rng = np.random.RandomState(9)
    for _ in range(60):
        x, y = rng.randint(0, 640), rng.randint(0, 300)
        d.ellipse([x, y, x + 3, y + 3], fill=(255, 250, 200))
    d.ellipse([80, 60, 140, 120], fill=(240, 240, 210))  # 달
    return _add_grain(img)


def document(title_lines=12) -> Image.Image:
    """흰 배경 + 테두리 + 텍스트 줄 (→ 문서로 분류)."""
    img = Image.new("RGB", (500, 680), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, 476, 656], outline=(40, 40, 40), width=2)
    y = 70
    rng = np.random.RandomState(3)
    for _ in range(title_lines):
        w = rng.randint(200, 420)
        d.rectangle([60, y, 60 + w, y + 8], fill=(30, 30, 30))
        y += 34
    return img


def receipt() -> Image.Image:
    img = Image.new("RGB", (360, 620), (252, 252, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 350, 610], outline=(80, 80, 80), width=1)
    d.rectangle([60, 30, 300, 58], fill=(20, 20, 20))  # 상호
    y = 90
    for _ in range(16):
        d.rectangle([30, y, 200, y + 6], fill=(40, 40, 40))
        d.rectangle([250, y, 330, y + 6], fill=(40, 40, 40))
        y += 28
    return img


def ui_screenshot(size=(1180, 820)) -> Image.Image:
    """앱 화면 같은 스크린샷."""
    img = Image.new("RGB", size, (245, 246, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0], 56], fill=(40, 44, 52))          # 상단 바
    d.rectangle([0, 56, 240, size[1]], fill=(28, 30, 36))        # 사이드바
    for i in range(8):
        d.rectangle([20, 90 + i * 46, 220, 122 + i * 46], fill=(60, 64, 72))
    for i in range(10):
        d.rectangle([270, 90 + i * 62, size[0] - 40, 130 + i * 62], fill=(255, 255, 255))
        d.rectangle([286, 100 + i * 62, 700, 112 + i * 62], fill=(120, 120, 120))
    return img


def blurry(base: Image.Image, radius=6) -> Image.Image:
    return base.filter(ImageFilter.GaussianBlur(radius))


def flat_dim() -> Image.Image:
    """어두컴컴하고 밋밋한(흔들린 실내) 사진."""
    return _add_grain(Image.new("RGB", (640, 480), (70, 66, 72)), amount=3)


# ---------- 세트 구성 ----------

def build(out: Path) -> int:
    np.random.seed(0)
    count = 0

    def photo(img, rel, **kw):
        nonlocal count
        _save_photo(img, out / rel, **kw)
        count += 1

    def plain(img, rel, **kw):
        nonlocal count
        _save_plain(img, out / rel, **kw)
        count += 1

    # 1) 일반 사진 (카테고리: normal)
    b = beach()
    photo(b, "여행/해변_노을.jpg")
    photo(mountain(), "여행/산.jpg")
    photo(park(), "일상/공원.jpg")
    photo(city(), "일상/야경_도시.jpg", make="Apple", model="iPhone 15")
    photo(flower(), "일상/꽃.jpg")

    # 2) 완전 중복 (바이트 동일 복사본)
    src = out / "여행/해변_노을.jpg"
    dst = out / "여행/사본/해변_노을 (1).jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    count += 1

    # 3) 유사 (리사이즈 + 재압축)
    photo(b.resize((320, 240)), "여행/해변_노을_작게.jpg", quality=70)

    # 4) 연사 (같은 장면 3컷: 선명 → 베스트샷, 나머지 흔들림)
    burst = night_scene()
    photo(burst, "여행/불꽃_연사_1.jpg", make="Apple", model="iPhone 15")
    photo(blurry(burst, 2), "여행/불꽃_연사_2.jpg", make="Apple", model="iPhone 15")
    photo(blurry(burst, 5), "여행/불꽃_연사_3.jpg", make="Apple", model="iPhone 15")

    # 5) 흔들린 사진 (카테고리: blurry)
    photo(blurry(park(), 7), "흔들린사진/실외_흔들림.jpg")
    photo(flat_dim(), "흔들린사진/실내_흔들림.jpg")

    # 6) 문서 (카테고리: document)
    plain(document(), "문서/계약서_스캔.jpg", quality=90)
    plain(receipt(), "문서/영수증.jpg", quality=90)

    # 7) 스크린샷 (파일명 / 해상도)
    plain(ui_screenshot(), "스크린샷/Screenshot_2024-01-15.png")
    plain(ui_screenshot((900, 600)), "스크린샷/화면캡처_설정.png")
    plain(ui_screenshot((1920, 1080)), "스크린샷/캡처_이름없음.png")  # 해상도로 판정

    return count


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_photos")
    n = build(out)
    print(f"샘플 사진 {n}장 생성 완료 → {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
