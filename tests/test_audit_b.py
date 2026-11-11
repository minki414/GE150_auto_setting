"""B단계 감사(B01~B04) 재현 시험 — 장비 없이(가짜 장비·메모리 대역·offscreen 창)."""
import json
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import MO_DIR
from ge150 import backup, codec, device, excel, mofile, plan, tonepack
from ge150 import protocol as P

OrigFake = device.FakeDevice


def corrupt_type(raw: bytes, module=0) -> bytes:
    """모듈의 TYPE(u16)을 65535 로 — decode 가 거부하는 해석 불가 슬롯."""
    b = bytearray(raw)
    b[16 + module * 24 + 2:16 + module * 24 + 4] = b"\xff\xff"
    return bytes(b)


def good_backup(tmp_path, name="bk"):
    dev = OrigFake()
    return dev, backup.make_backup(dev.read_all(), tmp_path / name)


# ---------------------------------------------------------------- B01
def test_b01_backup_has_proof_file_and_restores_all_200_incl_09d_48d(tmp_path, dev_data):
    dev, folder = good_backup(tmp_path)
    proof = json.loads((folder / "backup.json").read_text(encoding="utf-8"))
    assert proof["format"] == "GE150PRO-BACKUP" and proof["version"] == 1 and len(proof["slots"]) == 200
    assert proof["device_bin_sha256"] and proof["created"]
    for s in range(1, 201):
        dev.slots[s] = dev_data[1]
    rows, _ = plan.build_plan(dev.read_all(), folder)
    assert all(r.exact for r in rows) and rows[35].status == plan.ST_RESTORE and rows[191].status == plan.ST_RESTORE
    res = plan.apply_plan(dev, rows, [r.slot for r in rows if r.status == plan.ST_RESTORE], backup_root=tmp_path / "b2")
    assert res.success, res.text()
    assert dev.read_all() == dev_data


def test_b01_corrupt_device_bin_one_byte_rejects_whole_folder(tmp_path, dev_data):
    dev, folder = good_backup(tmp_path)
    raw = bytearray((folder / "device.bin").read_bytes())
    raw[199 * 244 + 18] ^= 0xFF                      # 50D 한 바이트 손상(크기·.mo 는 그대로)
    (folder / "device.bin").write_bytes(bytes(raw))
    with pytest.raises(backup.BackupError) as ex:
        plan.build_plan(dev.read_all(), folder)
    assert "백업 손상" in str(ex.value)
    assert dev.written == []                         # 쓰기 0


@pytest.mark.parametrize("mutate", ["format", "version", "slot_hash", "bin_hash", "broken_json", "no_bin", "short_slots"])
def test_b01_bad_proof_rejects_folder(tmp_path, mutate):
    dev, folder = good_backup(tmp_path)
    pf = folder / "backup.json"
    proof = json.loads(pf.read_text(encoding="utf-8"))
    if mutate == "format":
        proof["format"] = "OTHER"
    elif mutate == "version":
        proof["version"] = 2
    elif mutate == "slot_hash":
        proof["slots"]["37"] = "0" * 64
    elif mutate == "bin_hash":
        proof["device_bin_sha256"] = "0" * 64
    elif mutate == "short_slots":
        del proof["slots"]["200"]
    if mutate == "broken_json":
        pf.write_text("{nope", encoding="utf-8")
    else:
        pf.write_text(json.dumps(proof), encoding="utf-8")
    if mutate == "no_bin":
        (folder / "device.bin").unlink()
    with pytest.raises(backup.BackupError):
        plan.build_plan(dev.read_all(), folder)


def test_b01_without_proof_file_is_not_a_backup(tmp_path, dev_data):
    """backup.json 이 없으면 device.bin 이 있어도 백업이 아님 -> .mo 로 처리(원본 바이트 복원 없음, 09D 는 범위 오류)."""
    dev, folder = good_backup(tmp_path)
    (folder / "backup.json").unlink()
    rows, _ = plan.build_plan(dev.read_all(), folder)
    assert not any(r.exact for r in rows)
    assert rows[35].status == plan.ST_ERROR          # 09D OCTAVE DRY=200 은 .mo 범위 검사에 걸림


def test_b01_plain_tone_folder_with_stray_device_bin(tmp_path, dev_data):
    tone = tmp_path / "tone"
    tone.mkdir()
    (tone / "device.bin").write_bytes(b"\x00" * 48800)   # 무관한 같은 이름·같은 크기의 파일
    shutil.copy(next(MO_DIR.glob("50D*.mo")), tone)
    rows, un = plan.build_plan(dev_data, tone)
    assert not any(r.exact for r in rows) and rows[199].entry is not None and rows[199].status == plan.ST_CHANGE
    assert sum(1 for r in rows if r.status != plan.ST_NONE) == 1


def test_b01_unreadable_current_slot_does_not_block_backup_and_restore_works(tmp_path, dev_data):
    dev, good = good_backup(tmp_path, "good")
    dev.slots[200] = corrupt_type(dev_data[200])        # 현재 장비 50D TYPE=65535(해석 불가)
    with pytest.raises(codec.CodecError):
        codec.decode(dev.slots[200])
    rows, _ = plan.build_plan(dev.read_all(), good)
    assert rows[199].status == plan.ST_RESTORE
    res = plan.apply_plan(dev, rows, [200], backup_root=tmp_path / "bad")   # 백업 단계가 해석 실패에 막히지 않음
    assert res.success, res.text()
    bk = res.backup_dir
    assert (bk / "device.bin").read_bytes()[199 * 244:] == corrupt_type(dev_data[200])   # 해석 불가 원본 보존
    assert backup.verify_backup(bk)[200] == corrupt_type(dev_data[200])
    assert len(list(bk.glob("*.mo"))) == 199
    txt = (bk / "결과.txt").read_text(encoding="utf-8")
    assert "해석 불가" in txt and "50D" in txt
    assert dev.read_all()[200] == dev_data[200]                              # 정상 백업으로 50D 복원 성공


def test_b01_cli_plan_rejects_corrupt_backup(tmp_path):
    import cli
    dev, folder = good_backup(tmp_path)
    (folder / "device.bin").write_bytes(b"\x00" * 48800)
    with pytest.raises(SystemExit) as ex:
        cli._plan(dev, folder)
    assert "백업 손상" in str(ex.value.code)


def test_b01_cli_backup_reports_unreadable_slot(tmp_path, capsys):
    import cli
    dev = OrigFake()
    dev.slots[5] = corrupt_type(dev.slots[5], 3)
    cli.cmd_backup(dev, SimpleNamespace(folder=str(tmp_path / "b")))
    out = capsys.readouterr().out
    assert "해석 불가" in out and "02A" in out


# ---------------------------------------------------------------- B02
class _Fail:
    @staticmethod
    def write_result(folder, text):
        raise OSError("synthetic disk full")


def _changed_slot(dev):
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    return rows, next(r.slot for r in rows if r.status == plan.ST_CHANGE)


def test_b02_record_failure_alone_is_not_full_success(tmp_path, monkeypatch):
    fb = tmp_path / "fb"
    monkeypatch.setattr(backup, "fallback_dir", lambda: fb)
    monkeypatch.setattr(backup, "write_result", _Fail.write_result)
    dev = OrigFake()
    rows, slot = _changed_slot(dev)
    res = plan.apply_plan(dev, rows, [slot], backup_root=tmp_path / "bk")
    assert res.write_ok and not res.record_ok and not res.success
    first = res.text().splitlines()[0]
    assert "쓰기 완료" in first and "기록 저장 실패" in first
    assert res.fallback_path and Path(res.fallback_path).parent == fb and Path(res.fallback_path).exists()
    assert res.fallback_path in res.text()
    assert "백업 폴더" in Path(res.fallback_path).read_text(encoding="utf-8")


def test_b02_fallback_also_fails_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "write_result", _Fail.write_result)
    monkeypatch.setattr(backup, "write_fallback_result", lambda text, now=None: (_ for _ in ()).throw(OSError("no space")))
    dev = OrigFake()
    rows, slot = _changed_slot(dev)
    res = plan.apply_plan(dev, rows, [slot], backup_root=tmp_path / "bk")
    assert res.write_ok and not res.record_ok and "대체 위치 저장도 실패" in res.text() and "no space" in res.text()


def test_b02_cli_exit_code_follows_both(tmp_path, monkeypatch):
    import cli
    monkeypatch.setattr(backup, "fallback_dir", lambda: tmp_path / "fb")
    slot = _changed_slot(OrigFake())[1]
    a = SimpleNamespace(allow_write=True, slots=[P.slot_label(slot)], folder=str(MO_DIR), backup_root=str(tmp_path / "bk"), fake=True)
    with pytest.raises(SystemExit) as ok:
        cli.cmd_write(OrigFake(), a)
    assert ok.value.code == 0
    monkeypatch.setattr(backup, "write_result", _Fail.write_result)
    a.backup_root = str(tmp_path / "bk2")
    with pytest.raises(SystemExit) as bad:
        cli.cmd_write(OrigFake(), a)
    assert bad.value.code == 1                         # 장비 쓰기는 성공했어도 기록 실패면 1


class GateThenThrow(OrigFake):
    """대상 슬롯에서 멈춰 있다가 gate 가 열리면 메모리에 쓴 직후 USB 예외(ACK 전에 끊김)."""

    def __init__(self):
        super().__init__()
        self.entered, self.gate, self.closed = threading.Event(), threading.Event(), False

    def write_slot(self, slot, data):
        self.entered.set()
        assert self.gate.wait(10)
        self.slots[slot] = bytes(data)
        raise OSError("synthetic USB disconnect after write")

    def close(self):
        self.closed = True


def _wait(app, cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_b02_close_with_usb_error_and_record_failure_shows_summary(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    import app as appmod
    fb = tmp_path / "fb"
    monkeypatch.setattr(backup, "fallback_dir", lambda: fb)
    monkeypatch.setattr(device, "FakeDevice", GateThenThrow)
    monkeypatch.setattr(backup, "write_result", _Fail.write_result)
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    msgs = []
    win.ask_yes_no = lambda t: (msgs.append(t), True)[1]
    win.info = msgs.append
    win.show()
    win.connect_and_read()
    assert _wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    dev = win.dev
    tone = tmp_path / "tone"
    tone.mkdir()
    shutil.copy(next(MO_DIR.glob("50D*.mo")), tone)
    win.set_folder(tone)
    win._select(lambda r: r.slot == 200)
    win.apply()
    assert _wait(app, lambda: dev.entered.is_set())
    win.close()
    assert win.isVisible() and win._closing
    dev.gate.set()
    assert _wait(app, lambda: not win.isVisible())
    summary = [m for m in msgs if "미확인" in m]
    assert summary, msgs                                    # 닫는 중이어도 요약 창이 떴다
    text = summary[-1]
    assert "50D" in text and "기록 저장 실패" in text and "결과.txt 저장 실패" in text and str(fb) in text
    assert "synthetic USB disconnect" in text
    assert msgs.index(summary[-1]) == len(msgs) - 1         # 요약이 마지막(닫기 전에 표시)
    assert dev.closed and win.dev is None
    assert any(fb.glob("결과-*.txt"))


# ---------------------------------------------------------------- B03
def _two_slot_json(tmp_path):
    from test_tonepack import example, pack, preset, write_json
    return write_json(tmp_path, pack(preset(slot="01A"), preset(slot="01B", name="Second")), "two.json")


def _leftovers(tmp_path):
    return [p.name for p in tmp_path.iterdir() if "_mo_" in p.name or p.name.endswith(".partial") or ".partial-" in p.name]


def test_b03_double_check_failure_leaves_no_output(tmp_path, monkeypatch):
    src = _two_slot_json(tmp_path)
    orig, n = mofile.read_mo, {"c": 0}

    def flaky(path, user_names=None):
        e = orig(path, user_names)
        n["c"] += 1
        if n["c"] == 2:
            e.errors.append("주입한 검사 오류")
        return e
    monkeypatch.setattr(mofile, "read_mo", flaky)
    res = tonepack.convert([src], now=None)
    assert not res.ok and res.out_dir is None and any("주입한 검사 오류" in e.text() for e in res.errors)
    assert _leftovers(tmp_path) == []                       # 최종 폴더·.partial 모두 없음


def test_b03_write_oserror_returns_error_not_exception(tmp_path, monkeypatch):
    src = _two_slot_json(tmp_path)
    orig, n = mofile.write_mo, {"c": 0}

    def boom(folder, slot, mo):
        n["c"] += 1
        if n["c"] == 2:
            raise OSError("synthetic write error")
        return orig(folder, slot, mo)
    monkeypatch.setattr(mofile, "write_mo", boom)
    res = tonepack.convert([src])
    assert not res.ok and res.out_dir is None and "synthetic write error" in res.errors[0].text()
    assert _leftovers(tmp_path) == []


def test_b03_partial_cleanup_failure_reports_path(tmp_path, monkeypatch):
    src = _two_slot_json(tmp_path)
    monkeypatch.setattr(mofile, "write_mo", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(tonepack.shutil, "rmtree", lambda p: (_ for _ in ()).throw(OSError("locked")))
    res = tonepack.convert([src])
    assert not res.ok and any(".partial" in e.text() for e in res.errors)


def test_b03_success_renames_partial_to_final(tmp_path):
    src = _two_slot_json(tmp_path)
    res = tonepack.convert([src])
    assert res.ok and res.out_dir.exists() and len(res.files) == 2 and all(f.exists() for f in res.files)
    assert [p for p in tmp_path.iterdir() if "partial" in p.name] == []
    assert (res.out_dir / tonepack.META_NAME).exists()


def test_b03_window_message_matches_result(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    import app as appmod
    src = _two_slot_json(tmp_path)
    monkeypatch.setattr(mofile, "write_mo", lambda *a: (_ for _ in ()).throw(OSError("synthetic write error")))
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    shown = []
    win.show_issues = lambda title, issues: shown.append((title, list(issues)))
    win.info = lambda t: None
    res = win.load_tone_json([src])
    assert not res.ok and len(shown) == 1 and "만들지 않았습니다" in shown[0][0]
    assert win.ed_folder.text() == "" and _leftovers(tmp_path) == []      # 안내(만들지 않음)와 실제 결과가 같다
    win.close()


# ---------------------------------------------------------------- B04
def test_b04_backup_folder_excel_detail_has_200_rows(tmp_path):
    from openpyxl import load_workbook
    dev, folder = good_backup(tmp_path)
    rows, _ = plan.build_plan(dev.read_all(), folder)
    wb = load_workbook(excel.export_xlsx(tmp_path / "b.xlsx", rows, dev.read_all(), "folder"))
    ws = wb["톤 상세"]
    assert ws.max_row == 201 and ws["A2"].value == "01A" and ws["B2"].value == "01A AntiFrz CLN"


def test_b04_unreadable_slot_is_listed(tmp_path, dev_data):
    from openpyxl import load_workbook
    dev, good = good_backup(tmp_path, "g")
    dev.slots[200] = corrupt_type(dev_data[200])
    rows, _ = plan.build_plan(dev.read_all(), backup.make_backup(dev.read_all(), tmp_path / "b"))
    ws = load_workbook(excel.export_xlsx(tmp_path / "f.xlsx", rows, dev.read_all(), "folder"))["톤 상세"]
    assert ws.max_row == 201
    assert ws["A201"].value == "50D" and ws["B201"].value == excel.UNREADABLE
    ws2 = load_workbook(excel.export_xlsx(tmp_path / "d.xlsx", rows, dev.read_all(), "device"))["톤 상세"]
    assert ws2.max_row == 201 and ws2["B201"].value == excel.UNREADABLE     # 장비 내용 보기도 죽지 않는다


def test_b03_publish_retries_transient_permission_error(tmp_path, monkeypatch):
    """Windows 에서 방금 쓴 폴더 이름 바꾸기가 일시적으로 거부되어도(실제로 관찰됨) 다시 시도해 성공한다."""
    src = _two_slot_json(tmp_path)
    real, n = tonepack._rename, {"c": 0}

    def flaky(a, b):
        n["c"] += 1
        if n["c"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(a, b)
    monkeypatch.setattr(tonepack, "_rename", flaky)
    monkeypatch.setattr(tonepack.time, "sleep", lambda s: None)
    res = tonepack.convert([src])
    assert res.ok and n["c"] == 3 and res.out_dir.exists() and _leftovers(tmp_path) == [res.out_dir.name]


def test_b03_publish_gives_up_with_error_and_cleans_partial(tmp_path, monkeypatch):
    src = _two_slot_json(tmp_path)
    monkeypatch.setattr(tonepack, "_rename", lambda a, b: (_ for _ in ()).throw(PermissionError(5, "Access is denied")))
    monkeypatch.setattr(tonepack.time, "sleep", lambda s: None)
    res = tonepack.convert([src])
    assert not res.ok and res.out_dir is None and _leftovers(tmp_path) == []
