"""D1: 쓰기 간격·저장 확인·다시 쓰기 시험. 가짜 시계와 가짜 sleep 로 시간을 주입하므로 시험은 느려지지 않는다.

실장비 시험(2026-11-10): C3 를 간격 없이 99개 보내자 모두 43 응답은 왔지만 앞 22개만 저장됐다.
"""
import pytest

from conftest import MO_DIR
from ge150 import device, plan
from ge150 import protocol as P

OrigFake = device.FakeDevice


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(plan, "_sleep", c.sleep)
    return c


def changed_rows(dev):
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    return rows, [r.slot for r in rows if r.status == plan.ST_CHANGE]


def test_constants_and_estimate():
    assert plan.WRITE_GAP_S == 0.3 and plan.RETRY_GAP_S == 1.0 and plan.MAX_REWRITE == 2
    assert plan.estimate_seconds(200) == 80           # 확인 창의 예상 시간(200개 약 80초)


def test_gap_is_taken_after_each_ack_before_next_slot(clock, tmp_path):
    dev = OrigFake(clock=clock)
    events = []
    orig = dev.write_slot
    dev.write_slot = lambda s, d: (events.append(("write", s)), orig(s, d))[1]
    sleep = clock.sleep
    clock.sleep = lambda s: (events.append(("sleep", s)), sleep(s))[1]
    plan._sleep = clock.sleep
    rows, sel = changed_rows(dev)
    plan.apply_plan(dev, rows, sel[:3], backup_root=tmp_path)
    assert events[:6] == [("write", sel[0]), ("sleep", 0.3), ("write", sel[1]), ("sleep", 0.3),
                          ("write", sel[2]), ("sleep", 0.3)]


def test_with_gap_all_99_saved_without_rewrites(clock, tmp_path):
    dev = OrigFake(clock=clock, drop_after=22)        # 실장비처럼 빠른 연속 쓰기는 22개 뒤 저장 안 함
    rows, sel = changed_rows(dev)
    assert len(sel) == 99
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    assert res.success, res.text()
    assert len(res.ok) == 99 and res.rewritten == {} and res.not_saved == []
    assert clock.sleeps.count(0.3) == 99 and 1.0 not in clock.sleeps
    assert len(dev.written) == 99


def test_without_gap_unsaved_slots_are_rewritten_automatically(clock, tmp_path, monkeypatch):
    """간격을 없애면 실장비처럼 22개만 저장 -> 대조에서 어긋난 77개를 자동으로 다시 써서(1.0초 간격) 전부 저장."""
    monkeypatch.setattr(plan, "WRITE_GAP_S", 0.0)
    dev = OrigFake(clock=clock, drop_after=22)
    rows, sel = changed_rows(dev)
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    assert res.success, res.text()
    assert len(res.ok) == 99 and len(res.rewritten) == 77 and set(res.rewritten.values()) == {1}
    assert 1.0 in clock.sleeps and res.not_saved == []
    assert len(dev.write_calls) == 99 + 77
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert "다시 쓴 슬롯 77개" in txt and "(1회·저장 확인)" in txt and "저장 안 됨(응답은 왔으나 장비에 저장되지 않음) 0개" in txt
    assert "결과: 쓰기 완료" in txt


def test_never_saved_slot_is_reported_as_not_saved(clock, tmp_path):
    dev = OrigFake(clock=clock, never_save={199})
    rows, sel = changed_rows(dev)
    assert 199 in sel
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    assert res.not_saved == [199] and res.rewritten == {199: 2} and 199 not in res.ok
    assert dev.write_calls.count(199) == 3            # 처음 1번 + 다시 쓰기 2번
    assert not res.write_ok and not res.success
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert "결과: 쓰기 실패" in txt and "저장 안 됨(응답은 왔으나 장비에 저장되지 않음) 1개: 50C" in txt
    assert "50C(2회·저장 안 됨)" in txt
    assert len(res.ok) == len(sel) - 1


def test_progress_messages_for_verify_and_rewrite(clock, tmp_path):
    dev = OrigFake(clock=clock, never_save={200})
    rows, sel = changed_rows(dev)
    msgs = []
    plan.apply_plan(dev, rows, sel[-3:], backup_root=tmp_path, progress=lambda i, n, lab, msg: msgs.append(msg))
    assert "쓰는 중" in msgs and "저장 확인 중" in msgs
    assert "다시 쓰는 중(1/2)" in msgs and "다시 쓰는 중(2/2)" in msgs
    assert msgs.index("쓰는 중") < msgs.index("저장 확인 중") < msgs.index("다시 쓰는 중(1/2)") < msgs.index("다시 쓰는 중(2/2)")


def test_stop_request_is_honored_during_rewrite(tmp_path, monkeypatch):
    c, state = Clock(), {"stop": False}

    def sleep(s):
        c.sleep(s)
        if s == 1.0:
            state["stop"] = True                       # 다시 쓰기 단계에 들어서면 중단 요청
    monkeypatch.setattr(plan, "_sleep", sleep)
    monkeypatch.setattr(plan, "WRITE_GAP_S", 0.0)
    dev = OrigFake(clock=c, drop_after=22)
    rows, sel = changed_rows(dev)
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path, should_stop=lambda: state["stop"])
    assert res.stopped and not res.write_ok
    assert len(res.ok) == 22 and len(res.not_saved) == 77 and res.rewritten == {}
    assert "다시 쓰기 중단" in (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert len(dev.write_calls) == 99                  # 중단 뒤에는 더 쓰지 않음


def test_usb_error_during_rewrite_is_unknown(clock, tmp_path, monkeypatch):
    monkeypatch.setattr(plan, "WRITE_GAP_S", 0.0)

    class Boom(OrigFake):
        def write_slot(self, slot, data):
            if len(self.write_calls) >= 99:            # 다시 쓰기 단계의 첫 쓰기에서 USB 오류
                raise OSError("synthetic USB disconnect")
            return super().write_slot(slot, data)
    dev = Boom(clock=clock, drop_after=22)
    rows, sel = changed_rows(dev)
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    # E단계: 예외 뒤 추가 쓰기 없이 읽기를 한 번 하고 그 결과로 판정(쓰기가 읽기보다 먼저였으므로 저장 안 됨). 예외 쓰기도 시도로 센다.
    assert res.unknown == [] and "다시 쓰는 중 오류" in res.error and not res.write_ok
    assert len(res.ok) == 22 and len(res.not_saved) == 77
    assert sum(res.rewritten.values()) == 1 and sum(res.rewrite_errors.values()) == 1


def test_app_confirm_text_has_estimate(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import app as appmod
    import time
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    asked = []
    win.ask_yes_no = lambda t: (asked.append(t), False)[1]
    win.info = lambda t: None
    win.connect_and_read()
    t0 = time.time()
    while time.time() - t0 < 20 and not (len(win.dev_data) == 200 and not win.busy):
        app.processEvents()
        time.sleep(0.01)
    win.set_folder(MO_DIR)
    n = len(win._checked_slots())
    win.apply()
    assert n == 99 and "예상 시간: 약 %d초" % plan.estimate_seconds(99) in asked[0]
    win.close()
