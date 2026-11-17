"""PyInstaller 로 실행 파일을 만든다.

- 윈도우(build.cmd 가 호출): dist/GE150PresetLoader.exe 1개
- 맥(build_mac.sh 가 호출): dist/GE150PresetLoader.app 과 배포용 dist/GE150PresetLoader-mac.zip

경로에 한글이 있어 build.cmd(ASCII 전용)가 아니라 이 파이썬 파일에서 경로를 계산한다.
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "device-read-20261110.bin"
NAME = "GE150PresetLoader"
MAC = sys.platform == "darwin"


def data(src, dest):
    return ["--add-data", "%s%s%s" % (src, os.pathsep, dest)]   # 구분자: 윈도우 ';', 맥 ':'


# 카탈로그를 에디터 XML 에서 다시 만든다(XML 이 없으면 저장된 catalog*.json 을 그대로 쓴다)
subprocess.check_call([sys.executable, str(ROOT / "tools" / "build_catalog.py")])
args = [
    str(ROOT / "app.py"),
    "--name", NAME,
    "--windowed", "--noconfirm", "--clean",
    "--paths", str(ROOT),
    *data(ROOT / "ge150" / "data" / "catalog.json", "ge150/data"),
    *data(ROOT / "ge150" / "data" / "catalog-ge150.json", "ge150/data"),
    *data(FIXTURE, "fixtures"),  # --fake 용 가짜 장비 자료
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / ".tmp" / "pyi-build"),
    "--specpath", str(ROOT / ".tmp"),
]
if MAC:
    # 맥은 .app 묶음(폴더 형태)으로 만든다. --onefile .app 은 실행할 때마다 압축을 풀어 느리고 PyInstaller 도 권하지 않는다.
    args += ["--onedir", "--osx-bundle-identifier", "io.github.minki414.ge150presetloader"]
else:
    args += ["--onefile"]
PyInstaller.__main__.run(args)

if MAC:
    app = ROOT / "dist" / (NAME + ".app")
    # 서명 인증서가 없으므로 임시(ad-hoc) 서명으로 묶음 전체를 맞춘다(Apple Silicon 은 서명 없는 실행 파일을 거부한다)
    subprocess.check_call(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    out = ROOT / "dist" / ("%s-mac-%s.zip" % (NAME, platform.machine()))   # arm64 = Apple Silicon, x86_64 = Intel
    if out.exists():
        out.unlink()
    # ditto 로 압축해야 .app 안의 심볼릭 링크·실행 권한·서명이 그대로 보존된다(일반 zip 은 깨질 수 있음)
    subprocess.check_call(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(out)])
    shutil.rmtree(ROOT / "dist" / NAME, ignore_errors=True)   # .app 을 만들며 남는 중간 폴더
    print("Built:", app)
    print("Zip  :", out)
