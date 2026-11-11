"""PyInstaller 로 dist/GE150PresetLoader.exe 1개를 만든다(build.cmd 가 호출).

경로에 한글이 있어 build.cmd(ASCII 전용)가 아니라 이 파이썬 파일에서 경로를 계산한다.
"""
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "device-read-20261110.bin"

# 카탈로그를 에디터 XML 에서 다시 만든다(XML 이 없으면 저장된 catalog*.json 을 그대로 쓴다)
subprocess.check_call([sys.executable, str(ROOT / "tools" / "build_catalog.py")])
PyInstaller.__main__.run([
    str(ROOT / "app.py"),
    "--name", "GE150PresetLoader",
    "--onefile", "--windowed", "--noconfirm", "--clean",
    "--paths", str(ROOT),
    "--add-data", "%s;ge150/data" % (ROOT / "ge150" / "data" / "catalog.json"),
    "--add-data", "%s;ge150/data" % (ROOT / "ge150" / "data" / "catalog-ge150.json"),
    "--add-data", "%s;fixtures" % FIXTURE,  # --fake 용 가짜 장비 자료
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / ".tmp" / "pyi-build"),
    "--specpath", str(ROOT / ".tmp"),
])
