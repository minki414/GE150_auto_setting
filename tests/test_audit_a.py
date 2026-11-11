"""A단계 감사(A01~A06) 재현 시험. 실제 Device 클래스에는 메모리 HID 대역을 꽂아 ACK·재시도·예외를 시험한다."""
import subprocess
import time
from collections import deque
from types import SimpleNamespace

import pytest

from conftest import MO_DIR
from ge150 import backup, codec, device, models, mofile, plan
from ge150 import protocol as P

# ---------------------------------------------------------------- 메모리 HID 대역
class MemHid:
    """hid.device 의 write/read/close 만 흉내 낸다. C3 를 받으면 mode 에 따라 ACK 를 만든다."""

    def __init__(self, preset_data=None, mode="ack", fail_write=False):
        self.queue = deque()
        self.mode = mode            # ack | none | wrong_slot | late (첫 시도 무응답, 재시도에 ACK)
        self.fail_write = fail_write
        self.parser = P.FrameParser()
        self.frames = []            # PC -> 장치로 나간 프레임 본문
        self.preset_data = preset_data or {}
        self.c3_count = 0
        self.closed = False

    def push_frame(self, body: bytes):
        for rep in P.frame_to_reports(P.build_frame(body)):
            self.queue.append(list(rep[1:]))   # 65B(보고서 ID 포함) -> 64B 보고서

    def write(self, rep: bytes):
        if self.fail_write:
            raise OSError("synthetic USB disconnect")
        self.parser.feed(P.report_payload(bytes(rep[1:])))
        for body, ok in self.parser.frames():
            assert ok
            self.frames.append(body)
            if body[0] == P.CMD_WRITE:
                self.c3_count += 1
                slot = body[1]
                if self.mode == "ack" or (self.mode == "late" and self.c3_count >= 2):
                    self.push_frame(bytes([P.RSP_WRITE_DONE, slot]))
                elif self.mode == "wrong_slot":
                    self.push_frame(bytes([P.RSP_WRITE_DONE, slot % 200 + 1]))
                    self.push_frame(b"\x32\x00")
            elif body == P.CMD_READ_ALL:
                for s in sorted(self.preset_data):   # 빠진 슬롯이 있으면 그 슬롯의 프레임은 오지 않는다
                    self.push_frame(bytes([P.RSP_PRESET, s]) + self.preset_data[s])
        return len(rep)

    def read(self, size, timeout_ms=0):
        if self.queue:
            return self.queue.popleft()
        time.sleep(min(timeout_ms, 5) / 1000)
        return []

    def close(self):
        self.closed = True


@pytest.fixture
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(device, "WRITE_ACK_TIMEOUT", 0.15)
    monkeypatch.setattr(device, "READ_TIMEOUT", 0.6)


def mem_device(hid):
    d = device.Device()
    d.h = hid
    d.profile = models.PRO   # 전체 읽기로 기종이 정해진 뒤의 상태(읽기 없이 쓰기는 거부되므로 시험에서 미리 정한다)
    return d


DATA = bytes(range(244))


# ---------------------------------------------------------------- A04
def test_a04_real_device_ack_ok(fast_timeouts):
    h = MemHid()
    assert mem_device(h).write_slot(200, DATA) is True
    assert h.c3_count == 1 and h.frames[0] == P.CMD_PRE_WRITE


def test_a04_stale_ack_in_hid_queue_is_not_success(fast_timeouts):
    """과거의 CRC 정상 ACK(43 C8)가 HID 대기열에 남아 있어도, 새 쓰기의 ACK 가 없으면 실패(재시도 후 False)."""
    h = MemHid(mode="none")
    h.push_frame(bytes([P.RSP_WRITE_DONE, 200]))
    assert mem_device(h).write_slot(200, DATA) is False
    assert h.c3_count == 2          # 1회 재시도


def test_a04_stale_ack_in_parser_buffer_is_not_success(fast_timeouts):
    h = MemHid(mode="none")
    d = mem_device(h)
    d.rx.feed(P.build_frame(bytes([P.RSP_WRITE_DONE, 200])) + b"\xaa\x55\x02")   # 완성 프레임 + 불완전 프레임
    assert d.write_slot(200, DATA) is False


def test_a04_ack_only_on_retry_succeeds(fast_timeouts):
    h = MemHid(mode="late")
    assert mem_device(h).write_slot(7, DATA) is True
    assert h.c3_count == 2


def test_a04_wrong_slot_or_other_frames_not_success(fast_timeouts):
    h = MemHid(mode="wrong_slot")
    assert mem_device(h).write_slot(7, DATA) is False


def test_a04_write_exception_propagates(fast_timeouts):
    with pytest.raises(OSError):
        mem_device(MemHid(fail_write=True)).write_slot(7, DATA)


def test_a04_read_all_with_real_device(fast_timeouts, dev_data):
    d = mem_device(MemHid(preset_data=dev_data))
    assert d.read_all() == dev_data
    partial = dict(dev_data)
    partial.pop(100)
    with pytest.raises(device.DeviceError):
        mem_device(MemHid(preset_data=partial)).read_all()


def test_a04_apply_plan_stops_on_missing_ack(fast_timeouts, tmp_path, dev_data):
    """실제 Device 로 apply_plan: ACK 가 없으면 그 슬롯을 '실패'로 두고 뒤 슬롯은 미시도."""
    h = MemHid(preset_data=dev_data, mode="none")
    dev = mem_device(h)
    rows, _ = plan.build_plan(dev_data, MO_DIR)
    sel = [r.slot for r in rows if r.status == plan.ST_CHANGE][:3]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    assert res.failed == sel[:1] and res.skipped == sel[1:] and not res.success
    assert (res.backup_dir / "결과.txt").exists()


# ---------------------------------------------------------------- A01
def _backup_and_scramble(tmp_path, dev_data):
    dev = device.FakeDevice()
    folder = backup.make_backup(dev.read_all(), tmp_path / "bk")
    for s in range(1, 201):                       # 모든 슬롯을 1번 프리셋으로 덮어 '변경'
        dev.slots[s] = dev_data[1]
    return dev, folder


def test_a01_all_200_restore_byte_exact(tmp_path, dev_data):
    dev, folder = _backup_and_scramble(tmp_path, dev_data)
    rows, un = plan.build_plan(dev.read_all(), folder)
    assert un == [] and not any(r.status == plan.ST_ERROR for r in rows)
    sel = [r.slot for r in rows if r.status == plan.ST_RESTORE]
    assert len(sel) == 199                        # 1번 슬롯은 이미 같아서 '동일'
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk2")
    assert res.success, res.text()
    assert dev.read_all() == dev_data             # 바이트 전체 일치


def test_a01_09d_and_48d_are_restorable(tmp_path, dev_data):
    """실측 범위 밖 값(09D OCTAVE DRY=200, 48D DYNAMIC THRES=1040)도 백업에서 복원된다."""
    assert mofile.validate_mo(codec.decode(dev_data[36])) and mofile.validate_mo(codec.decode(dev_data[192]))
    dev, folder = _backup_and_scramble(tmp_path, dev_data)
    rows, _ = plan.build_plan(dev.read_all(), folder)
    assert rows[35].status == plan.ST_RESTORE and rows[191].status == plan.ST_RESTORE
    assert plan.apply_plan(dev, rows, [36, 192], backup_root=tmp_path / "b2").success
    r = dev.read_all()
    assert r[36] == dev_data[36] and r[192] == dev_data[192]


def test_a01_mo_range_check_is_not_widened(tmp_path, dev_data):
    """사용자 .mo 입력은 지금처럼 범위 검사: device.bin 이 없는 폴더에서는 09D 가 오류."""
    mofile.write_mo(tmp_path, 36, codec.decode(dev_data[36]))
    rows, _ = plan.build_plan(dev_data, tmp_path)
    assert rows[35].status == plan.ST_ERROR


def test_a01_device_bin_alone_is_not_a_backup(tmp_path, dev_data):
    """B01로 정책 변경: backup.json 이 없는 폴더의 device.bin 은 무시하고 .mo 로 처리한다(상세는 test_audit_b.py)."""
    (tmp_path / "device.bin").write_bytes(b"\x00" * 100)
    rows, _ = plan.build_plan(dev_data, tmp_path)
    assert not any(r.exact for r in rows)


def test_a01_intentional_mismatch_is_detected(tmp_path, dev_data):
    """쓰기 뒤 대조: 선택 슬롯이 다르게 들어가거나 비선택 슬롯이 바뀌면 대조 불일치."""
    class Corrupt(device.FakeDevice):
        def write_slot(self, slot, data):
            super().write_slot(slot, data)
            self.slots[slot] = data[:-2] + b"\x00\x00" if slot == 36 else data   # 선택 슬롯 손상
            if slot == 36:
                self.slots[5] = b"\x01" * 244                                      # 비선택 슬롯 손상
            return True
    dev, folder = _backup_and_scramble(tmp_path, dev_data)
    good = Corrupt()
    good.slots = dict(dev.slots)
    rows, _ = plan.build_plan(good.read_all(), folder)
    res = plan.apply_plan(good, rows, [36, 192], backup_root=tmp_path / "b3")
    assert res.mismatch == [5] and res.not_saved == [36] and res.rewritten == {36: 2} and not res.success   # D1: 선택 슬롯은 2회 다시 써도 손상되면 저장 안 됨, 비선택 슬롯 손상은 대조 불일치
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert "예상 밖 변경(선택하지 않은 슬롯이 원본과 달라짐) 1개: 02A" in txt
    assert "저장 안 됨(응답은 왔으나 장비에 저장되지 않음) 1개: 09D" in txt


# ---------------------------------------------------------------- A02
class ThrowingDevice(device.FakeDevice):
    def __init__(self, throw_on, after_write=True, **kw):
        super().__init__(**kw)
        self.throw_on, self.after_write = throw_on, after_write

    def write_slot(self, slot, data):
        if slot == self.throw_on:
            if self.after_write:
                self.slots[slot] = bytes(data)   # 장비에는 도달했으나 ACK 전에 끊김
            raise OSError("synthetic USB disconnect after write")
        return super().write_slot(slot, data)


def test_a02_exception_returns_result_with_unattempted_and_error(tmp_path):
    dev = ThrowingDevice(throw_on=0)
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    sel = [r.slot for r in rows if r.status == plan.ST_CHANGE][:4]
    dev.throw_on = sel[1]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)           # 예외가 밖으로 나오지 않는다
    # E단계: 예외 뒤 추가 쓰기 없이 읽기를 한 번 시도하고, 그 결과(마지막 성공 재읽기)로 상태를 정한다.
    # 예외 시점 슬롯은 장비에 들어가 있었으므로 재읽기에서 목표와 일치 -> 완료, 다만 오류가 있으니 전체는 실패.
    assert res.ok == sel[:2] and res.unknown == [] and res.skipped == sel[2:]
    assert res.backup_dir and not res.success and not res.write_ok and "synthetic" in res.error
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert "미시도 2개" in txt and "쓰는 중 오류" in txt and "결과: 쓰기 실패" in txt


def test_a02_reread_failure_is_not_success(tmp_path):
    class NoReread(device.FakeDevice):
        n = 0

        def read_all(self):
            self.n += 1
            if self.n >= 2:
                raise OSError("read failed")
            return super().read_all()
    dev = NoReread()
    rows, _ = plan.build_plan(device.FakeDevice().read_all(), MO_DIR)
    sel = [next(r.slot for r in rows if r.status == plan.ST_CHANGE)]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    # E단계: ACK 만 받고 재읽기를 못 한 슬롯은 '완료'가 아니라 '미확인'(확인 시점: 확인 못 함)
    assert res.ok == [] and res.unknown == sel and res.unknown_info == {sel[0]: 0}
    assert not res.success and "다시 읽기 실패" in res.error
    assert "마지막 확인 시점: 확인 못 함" in res.text()
    assert (res.backup_dir / "결과.txt").exists()


def test_a02_result_file_failure_is_reported(tmp_path, monkeypatch):
    def boom(folder, text):
        raise OSError("disk full")
    monkeypatch.setattr(backup, "write_result", boom)
    dev = device.FakeDevice()
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    res = plan.apply_plan(dev, rows, [next(r.slot for r in rows if r.status == plan.ST_CHANGE)], backup_root=tmp_path)
    assert res.backup_dir and "disk full" in res.result_file_error and "결과.txt 저장 실패" in res.text()


# ---------------------------------------------------------------- A05
class _Run:
    def __init__(self, stdout="", rc=0):
        self.stdout, self.returncode = stdout, rc


def test_a05_editor_state(monkeypatch):
    def fake(out):
        monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: out() if callable(out) else out)
    fake(_Run('"GE150PRO.exe","1234","Console","1","50,000 K"\n'))
    assert device.editor_state() == device.EDITOR_RUNNING and "닫아" in device.editor_blocked_reason()
    fake(_Run("INFO: No tasks are running which match the specified criteria.\n"))
    assert device.editor_state() == device.EDITOR_NONE and device.editor_blocked_reason() is None
    fake(_Run('"GE150PROUpdate.exe","9","Console","1","1 K"\n'))
    assert device.editor_state() == device.EDITOR_NONE
    fake(_Run("", rc=1))
    assert device.editor_state() == device.EDITOR_UNKNOWN and "확인할 수 없어" in device.editor_blocked_reason()

    def raise_timeout():
        raise subprocess.TimeoutExpired("tasklist", 10)
    fake(raise_timeout)
    assert device.editor_state() == device.EDITOR_UNKNOWN
    fake(lambda: (_ for _ in ()).throw(OSError("no tasklist")))
    assert device.editor_state() == device.EDITOR_UNKNOWN


def test_a05_guard_blocks_apply_before_backup(tmp_path):
    dev = device.FakeDevice()
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    sel = [next(r.slot for r in rows if r.status == plan.ST_CHANGE)]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk", guard=lambda: "에디터 실행 중")
    assert res.error == "에디터 실행 중" and res.backup_dir is None and dev.written == []
    assert not (tmp_path / "bk").exists()


def test_a05_cli_write_rechecks_editor(tmp_path, monkeypatch):
    import cli
    monkeypatch.setattr(cli, "editor_guard", lambda fake: "에디터 실행 중")
    dev = device.FakeDevice()
    a = SimpleNamespace(allow_write=True, slots=["50D"], folder=str(MO_DIR), backup_root=str(tmp_path), fake=False)
    with pytest.raises(SystemExit) as ex:
        cli.cmd_write(dev, a)
    assert ex.value.code == 1 and dev.written == []
