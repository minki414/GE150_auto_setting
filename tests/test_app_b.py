"""창 시험(A03 창 닫기, A05 에디터 검사, A06 재연결) — offscreen, 가짜 장비."""
import os
import shutil
import threading
import time
from collections import deque

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import MO_DIR  # noqa: E402
from ge150 import device  # noqa: E402

OrigFake = device.FakeDevice


def wait(app, cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_window(tmp_path, fake=True):
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=fake, backup_root=tmp_path / "bk")
    win.msgs = []
    win.answer = True
    win.ask_yes_no = lambda t: (win.msgs.append(t), win.answer)[1]
    win.info = lambda t: win.msgs.append(t)
    win.show()
    return app, win


def tone_folder(tmp_path, n=3):
    tone = tmp_path / "tone"
    tone.mkdir()
    for p in sorted(MO_DIR.glob("5*.mo"))[:40]:
        shutil.copy(p, tone)
    return tone


# ---------------------------------------------------------------- A03
class Blocking(OrigFake):
    """첫 쓰기에서 멈춰 서 있다가 gate 가 열리면 마친다(작업 중 창 닫기 재현)."""

    def __init__(self):
        super().__init__()
        self.entered, self.gate, self.closed = threading.Event(), threading.Event(), False

    def write_slot(self, slot, data):
        self.entered.set()
        assert self.gate.wait(10)
        return super().write_slot(slot, data)

    def close(self):
        self.closed = True


def test_a03_close_during_apply_finishes_slot_then_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(device, "FakeDevice", Blocking)
    app, win = make_window(tmp_path)
    win.connect_and_read()
    assert wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    dev = win.dev
    win.set_folder(tone_folder(tmp_path))
    n_sel = len(win._checked_slots())
    assert n_sel >= 3
    win.apply()
    assert wait(app, lambda: dev.entered.is_set())

    win.close()                                          # 작업 중 X
    assert win.isVisible() and win._closing and win.stop_flag   # 바로 닫히지 않고 중단 요청
    assert "적용 중입니다. 지금 슬롯까지 마치고 멈춘 뒤 닫습니다." in win.msgs[-1]
    dev.gate.set()                                       # 현재 슬롯이 끝나면
    assert wait(app, lambda: not win.isVisible())
    assert dev.closed and win.dev is None                # 스레드 종료 후 장비 닫기 -> 창 닫기
    assert len(dev.written) == 1                         # 지금 슬롯까지만 씀
    txt = next((tmp_path / "bk").glob("*/결과.txt")).read_text(encoding="utf-8")
    assert "결과: 쓰기 중단" in txt and "완료 1개" in txt and "미시도 %d개" % (n_sel - 1) in txt


def test_a03_close_declined_keeps_running(tmp_path, monkeypatch):
    monkeypatch.setattr(device, "FakeDevice", Blocking)
    app, win = make_window(tmp_path)
    win.connect_and_read()
    assert wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    dev = win.dev
    win.set_folder(tone_folder(tmp_path))
    n_sel = len(win._checked_slots())
    win.apply()
    assert wait(app, lambda: dev.entered.is_set())
    win.answer = False
    win.close()
    assert win.isVisible() and not win._closing and not win.stop_flag
    dev.gate.set()
    assert wait(app, lambda: not win.busy)
    assert len(dev.written) == n_sel                    # 닫기를 거절하면 끝까지 계속한다
    win.answer = True
    win.close()


# ---------------------------------------------------------------- A05
def test_a05_window_blocks_when_editor_reason(tmp_path, monkeypatch):
    app, win = make_window(tmp_path, fake=False)
    monkeypatch.setattr(device, "editor_blocked_reason", lambda: "공식 에디터를 닫아 주십시오.")
    win.connect_and_read()
    assert win.dev is None and win.msgs[-1] == "공식 에디터를 닫아 주십시오." and not win.busy
    assert "연결 안 함" in win.lbl_dev.text()


def test_a05_apply_rechecks_editor(tmp_path):
    app, win = make_window(tmp_path)
    win.connect_and_read()
    assert wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    win.set_folder(tone_folder(tmp_path))
    win.editor_reason = lambda: "에디터가 켜졌습니다."      # 연결 뒤에 에디터가 시작된 경우
    win.apply()
    assert win.msgs[-1] == "에디터가 켜졌습니다." and not win.busy
    assert win.dev.written == []


# ---------------------------------------------------------------- A06
class Flaky(OrigFake):
    script = deque(["fail_connect", "ok", "ok"])
    instances = []

    def __init__(self):
        super().__init__()
        self.mode = Flaky.script.popleft() if Flaky.script else "ok"
        self.fail_next_read = False
        self.closed = False
        Flaky.instances.append(self)

    def connect(self):
        if self.mode == "fail_connect":
            raise device.DeviceError("장비를 찾을 수 없습니다.")
        super().connect()

    def read_all(self):
        if self.fail_next_read:
            raise OSError("USB 끊김")
        return super().read_all()

    def close(self):
        self.closed = True


def test_a06_reconnect_after_failure_and_clear_stale_table(tmp_path, monkeypatch):
    Flaky.script, Flaky.instances = deque(["fail_connect", "ok", "ok"]), []
    monkeypatch.setattr(device, "Device", Flaky)
    monkeypatch.setattr(device, "editor_blocked_reason", lambda: None)
    app, win = make_window(tmp_path, fake=False)

    win.btn_reread.click()                               # 1) 첫 연결 실패
    assert wait(app, lambda: not win.busy and win.msgs)
    assert win.dev is None and win.dev_data == {} and win.table.rowCount() == 0
    assert "연결 안 함" in win.lbl_dev.text() and "실패" in win.msgs[-1]

    win.btn_reread.click()                               # 2) 다시 읽기 = 새로 연결
    assert wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    assert len(Flaky.instances) == 2 and win.table.rowCount() == 200 and "연결됨" in win.lbl_dev.text()

    win.dev.fail_next_read = True                        # 3) 끊김: 이전 자료는 표에서 지운다
    first = win.dev
    win.btn_reread.click()
    assert wait(app, lambda: not win.busy and win.dev is None)
    assert first.closed and win.dev_data == {} and win.table.rowCount() == 0

    win.btn_reread.click()                               # 4) 또 새로 연결
    assert wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    assert len(Flaky.instances) == 3
    win.close()
