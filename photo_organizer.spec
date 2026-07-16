# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Photo Organizer GUI (Windows .exe / macOS .app 공통).

빌드:
    PYTHONPATH=src .venv/bin/pyinstaller photo_organizer.spec --noconfirm
산출물: dist/PhotoOrganizer/ (onedir), macOS는 dist/PhotoOrganizer.app.

onefile 대신 onedir 사용: 시작이 빠르고 대형 Qt/네이티브 의존성에서 AV 오탐이
적다(docs/PACKAGING.md 참조). onnxruntime은 3.14 wheel이 없어 제외(AI 보류).
"""
import os
import sys

hiddenimports = [
    # 훅으로 대개 잡히나, 플러그인/네이티브 로더는 명시해 누락을 방지.
    "rawpy",
    "pillow_heif",
    "imagehash",
    "send2trash",
]

a = Analysis(
    [os.path.join("packaging", "pyinstaller_entry.py")],
    pathex=[os.path.join(SPECPATH, "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["onnxruntime", "pytest", "tkinter", "matplotlib"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoOrganizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX는 AV 오탐/서명 문제 유발 → 사용 안 함
    console=False,           # GUI 앱: 콘솔 창 없음
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,        # macOS: 현재 아키텍처(universal2는 별도 빌드 필요)
    codesign_identity=None,  # 서명은 빌드 후 별도 단계(docs/PACKAGING.md)
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PhotoOrganizer",
)

# .app 번들은 macOS 전용. Windows/Linux는 위 COLLECT(dist/PhotoOrganizer/)가 산출물.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PhotoOrganizer.app",
        icon=None,
        bundle_identifier="com.rimichoi.photoorganizer",
        info_plist={
            "CFBundleName": "Photo Organizer",
            "CFBundleDisplayName": "Photo Organizer",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
        },
    )
