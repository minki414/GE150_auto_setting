import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
FIXT = Path(__file__).resolve().parent / "fixtures"
(Path.cwd() / ".tmp").mkdir(exist_ok=True)   # pytest.ini 의 --basetemp(.tmp/pytest) 상위 폴더 — 저장소에는 없는 폴더라 새로 받으면 만들어 준다
# 공식 에디터가 만든 .mo 원본 폴더(저장소에는 없음). 있으면 그것과 대조하는 시험을 돌리고, 없으면 그 시험을 건너뛴다.
# 환경변수 GE150_OFFICIAL_MO_DIR 로 위치를 줄 수 있고, 없으면 저장소 옆의 GE150PRO/file 을 본다.
MO_DIR = Path(os.environ.get("GE150_OFFICIAL_MO_DIR") or ROOT.parent / "GE150PRO" / "file")


def _code_names(code):
    names = set(code.co_names)
    for c in code.co_consts:
        if hasattr(c, "co_names"):
            names |= _code_names(c)
    return names


def _uses_official_mo(func, seen=None):
    """시험 함수(와 그것이 부르는 시험 파일 안의 도우미 함수)가 MO_DIR·mo_files 를 쓰는지."""
    seen = seen if seen is not None else set()
    if func in seen or not hasattr(func, "__code__"):
        return False
    seen.add(func)
    names = _code_names(func.__code__)
    if "MO_DIR" in names:
        return True
    return any(_uses_official_mo(func.__globals__.get(n), seen) for n in names
               if callable(func.__globals__.get(n)) and getattr(func.__globals__.get(n), "__module__", "").startswith("test_"))


def pytest_collection_modifyitems(items):
    if MO_DIR.is_dir():
        return
    skip = pytest.mark.skip(reason="공식 .mo 폴더(GE150PRO/file)가 없어 건너뜁니다")
    for it in items:
        if "mo_files" in it.fixturenames or _uses_official_mo(it.function):
            it.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_active_model(monkeypatch):
    """기종 선택은 전역 상태라 시험마다 Pro 로 되돌린다. 창 시험이 기종 선택 창에서 멈추지 않게 기본 답도 Pro 로 둔다."""
    from ge150 import models
    models.set_active(models.PRO)
    try:
        import app as appmod
        fake_ask = lambda self: models.PRO  # noqa: E731
        fake_ask.real = appmod.MainWindow.ask_model   # 기종 선택 창 자체를 시험할 때 쓴다
        monkeypatch.setattr(appmod.MainWindow, "ask_model", fake_ask)
    except ImportError:   # PySide6 가 없는 환경
        pass
    yield
    models.set_active(models.PRO)


@pytest.fixture(scope="session")
def frames():
    return json.loads((FIXT / "capture-frames.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def dev_data():
    raw = (FIXT / "device-read-20261110.bin").read_bytes()
    return {s: raw[(s - 1) * 244:s * 244] for s in range(1, 201)}


@pytest.fixture(scope="session")
def mo_files():
    """슬롯 -> (경로, json). 공식 에디터가 만든 원본 .mo (읽기만 한다)."""
    from ge150.protocol import parse_slot
    out = {}
    for p in MO_DIR.glob("*.mo"):
        s = parse_slot(p.name[:3])
        if s:
            out[s] = (p, json.loads(p.read_text(encoding="utf-8")))
    return out


@pytest.fixture(autouse=True)
def _isolate_fallback_dir(tmp_path_factory, monkeypatch):
    """결과 대체 저장 위치(%LOCALAPPDATA%)에 시험이 실제 파일을 만들지 않게 임시 폴더로 바꾼다."""
    from ge150 import backup
    monkeypatch.setattr(backup, "fallback_dir", lambda: tmp_path_factory.mktemp("fallback"))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """D1: 쓰기 간격(0.3초)·다시 쓰기 간격(1.0초)을 시험에서 실제로 기다리지 않는다. 간격 자체는 test_write_gap.py 가 가짜 sleep 로 확인한다."""
    from ge150 import plan
    monkeypatch.setattr(plan, "_sleep", lambda s: None)


@pytest.fixture(scope="session", autouse=True)
def _qt_cleanup_at_exit():
    """시험 세션이 끝날 때 남은 Qt 창·스레드 객체를 정리한다.

    정리 없이 인터프리터가 끝나면 QApplication 이 먼저 사라지고 뒤늦게 파이썬 쪽 창/스레드 객체가 파괴되면서
    종료 코드 0xC0000409 로 간헐 종료되는 일이 있었다(시험은 전부 통과해도 종료 코드가 0 이 아님).
    """
    yield
    try:
        import gc
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                w.hide()
                w.deleteLater()
            app.processEvents()
        gc.collect()
        if app is not None:
            app.processEvents()
    except Exception:  # noqa: 정리 실패가 시험 결과를 바꾸지 않게 한다
        pass
