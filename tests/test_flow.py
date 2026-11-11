import shutil

import pytest

from ge150 import backup, codec, device, mofile, plan

from conftest import MO_DIR


def test_full_flow_with_fake_device(tmp_path, dev_data):
    dev = device.FakeDevice()
    dev.connect()
    tone = tmp_path / "tone"
    tone.mkdir()
    mo = codec.decode(dev_data[200])
    mo["fileInfo"]["preset_name"] = "Flow Test"
    mo["effectModule"]["5"]["Data"]["100Hz"] = 20
    mofile.write_mo(tone, 200, mo)

    rows, un = plan.build_plan(dev.read_all(), tone)                 # 폴더 -> 계획
    assert un == [] and [r.status for r in rows].count(plan.ST_CHANGE) == 1
    assert rows[199].status == plan.ST_CHANGE and rows[0].status == plan.ST_NONE

    before = dev.read_all()
    res = plan.apply_plan(dev, rows, [200], backup_root=tmp_path / "bk")  # 백업 -> 쓰기 -> 다시 읽어 대조
    assert res.success, res.text()
    after = dev.read_all()
    assert codec.decode(after[200])["fileInfo"]["preset_name"] == "Flow Test"
    assert all(after[s] == before[s] for s in range(1, 200))         # 선택하지 않은 슬롯은 불변
    assert dev.written == [200]
    assert len(list(res.backup_dir.glob("*.mo"))) == 200
    assert (res.backup_dir / "device.bin").read_bytes() == backup.device_bin(before)
    assert (res.backup_dir / "결과.txt").exists()

    # 되돌리기 = 백업 폴더(device.bin 있음)를 톤 폴더로 고른 것: 원본 바이트 그대로 복원(상태 '백업 복원')
    rows2, _ = plan.build_plan(dev.read_all(), res.backup_dir)
    changed = [r.slot for r in rows2 if r.status == plan.ST_RESTORE]
    assert changed == [200]
    assert plan.apply_plan(dev, rows2, changed, backup_root=tmp_path / "bk2").success
    assert dev.read_all() == before                                  # 바이트 전체 일치


def test_all_official_mo_apply(tmp_path):
    dev = device.FakeDevice()
    rows, un = plan.build_plan(dev.read_all(), MO_DIR)
    assert len(un) == 1 and not any(r.status == plan.ST_ERROR for r in rows)
    sel = [r.slot for r in rows if r.status == plan.ST_CHANGE]
    assert len(sel) == 99
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    assert res.success and len(res.ok) == 99
    assert all(r.status == plan.ST_SAME for r in plan.build_plan(dev.read_all(), MO_DIR)[0] if r.entry)


def test_error_slot_not_written_and_write_failure_stops(tmp_path, dev_data):
    tone = tmp_path / "tone"
    tone.mkdir()
    shutil.copy(next(MO_DIR.glob("50D*.mo")), tone)
    shutil.copy(next(MO_DIR.glob("49D*.mo")), tone)
    bad = codec.decode(dev_data[1])
    bad["fileInfo"]["preset_name"] = "X" * 20
    (tone / "01A bad.mo").write_text(mofile.dumps_mo(bad), encoding="utf-8")
    dev = device.FakeDevice(fail_slots={196})   # 49D = 196
    rows, _ = plan.build_plan(dev.read_all(), tone)
    assert rows[0].status == plan.ST_ERROR
    r = plan.apply_plan(dev, rows, [1], backup_root=tmp_path / "b1")
    assert r.error and dev.written == []                       # 오류 슬롯은 쓰지 않음
    r = plan.apply_plan(dev, rows, [196, 200], backup_root=tmp_path / "b2")
    assert r.failed == [196] and r.skipped == [200] and not r.success and dev.written == []


def test_backup_failure_blocks_write(tmp_path):
    class BadBackupDevice(device.FakeDevice):
        def read_all(self):
            d = super().read_all()
            d.pop(5)                                           # 200개가 아니라 백업이 거부된다
            return d
    rows, _ = plan.build_plan(device.FakeDevice().read_all(), MO_DIR)
    dev = BadBackupDevice()
    res = plan.apply_plan(dev, rows, [next(r.slot for r in rows if r.status == plan.ST_CHANGE)], backup_root=tmp_path)
    assert res.error and "백업" in res.error and dev.written == []


def test_stop_request(tmp_path):
    dev = device.FakeDevice()
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    sel = [r.slot for r in rows if r.status == plan.ST_CHANGE][:5]
    n = {"c": 0}

    def stop():
        n["c"] += 1
        return n["c"] > 2
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path, should_stop=stop)
    assert res.stopped and len(res.ok) == 2 and len(res.skipped) == 3 and not res.mismatch


def test_cli_and_excel(tmp_path):
    import cli
    from openpyxl import load_workbook
    from ge150 import excel
    cli.main(["--fake", "export", str(tmp_path / "ex")])
    assert len(list((tmp_path / "ex").glob("*.mo"))) == 200
    cli.main(["--fake", "plan", str(MO_DIR)])
    dev = device.FakeDevice()
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    for src in ("device", "folder"):
        wb = load_workbook(excel.export_xlsx(tmp_path / (src + ".xlsx"), rows, dev.read_all(), src))
        assert wb.sheetnames == ["요약", "톤 상세"] and wb["요약"].max_row == 201
        assert wb["톤 상세"].max_row >= 101
    with pytest.raises(SystemExit):
        cli.main(["--fake", "write", str(MO_DIR), "--slots", "50D"])  # --allow-write 없이는 거부


def test_unassigned_slot_override(tmp_path, dev_data):
    mo = codec.decode(dev_data[3])
    mo["fileInfo"]["preset_name"] = "Pick Me"
    mofile.write_mo(tmp_path, 3, mo).rename(tmp_path / "Pick Me.mo")
    dev = device.FakeDevice()
    rows, un = plan.build_plan(dev.read_all(), tmp_path)
    assert len(un) == 1 and all(r.status == plan.ST_NONE for r in rows)
    rows, un = plan.build_plan(dev.read_all(), tmp_path, overrides={un[0].path: 7})
    assert un == [] and rows[6].new_name == "Pick Me" and rows[6].status == plan.ST_CHANGE
