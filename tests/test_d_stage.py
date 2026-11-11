"""D2(파일 이름·백업 성공 기준)·D3(장비 이름)·D4(감사 C C01~C03) 시험."""
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import MO_DIR
from ge150 import backup, codec, device, mofile, plan, tonepack
from ge150 import protocol as P

OrigFake = device.FakeDevice
NASTY = "<>\\^`{|} Name 16"   # 실장비에서 백업을 실패시킨 이름(16자)


def with_name(raw: bytes, name: str) -> bytes:
    return name.encode("ascii").ljust(16, b"\x00") + raw[16:]


# ---------------------------------------------------------------- D2
def test_nasty_name_is_16_chars():
    assert len(NASTY) == 16


def test_d2_backup_with_nasty_name_succeeds_for_all_200(tmp_path):
    dev = OrigFake()
    dev.slots[5] = with_name(dev.slots[5], NASTY)
    failures = []
    folder = backup.make_backup(dev.read_all(), tmp_path / "bk", failures=failures)
    assert failures == [] and len(list(folder.glob("*.mo"))) == 200
    assert backup.verify_backup(folder)[5] == dev.slots[5]
    f = folder / mofile.mo_filename(5, NASTY)
    assert f.exists() and f.name == "02A ___^`{_} Name 16.mo"
    assert mofile.read_mo(f).mo["fileInfo"]["preset_name"] == NASTY


def test_d2_apply_is_not_blocked_by_nasty_name(tmp_path):
    dev = OrigFake()
    dev.slots[5] = with_name(dev.slots[5], NASTY)
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    sel = [next(r.slot for r in rows if r.status == plan.ST_CHANGE)]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path)
    assert res.success, res.text()


@pytest.mark.parametrize("raw,expected", [
    ("a\\b", "a_b"), ("a/b", "a_b"), ("a:b", "a_b"), ("a*b", "a_b"), ("a?b", "a_b"), ('a"b', "a_b"),
    ("a<b>c", "a_b_c"), ("a|b", "a_b"), ("a\x01b\x1fc", "a_b_c"), ("end.", "end_"), ("end  ", "end__"),
    ("end. .", "end___"), ("ok name", "ok name"), ("dots.in.middle", "dots.in.middle"),
])
def test_d2_safe_filename(raw, expected):
    assert mofile.safe_filename(raw) == expected


def test_d2_mo_filename_trailing_dot_and_format():
    assert mofile.mo_filename(1, "Foo.") == "01A Foo_.mo"
    assert mofile.mo_filename(200, "A\\B") == "50D A_B.mo"


def test_d2_duplicate_file_names_get_numbers(tmp_path):
    mo = codec.decode(OrigFake().slots[1])
    a = mofile.write_mo(tmp_path, 1, mo, unique=True)
    b = mofile.write_mo(tmp_path, 1, mo, unique=True)
    c = mofile.write_mo(tmp_path, 1, mo, unique=True)
    assert [a.name, b.name, c.name] == ["01A 01A AntiFrz CLN.mo", "01A 01A AntiFrz CLN (2).mo", "01A 01A AntiFrz CLN (3).mo"]
    d = mofile.write_mo(tmp_path, 1, mo)            # unique 가 아니면 기존처럼 덮어쓴다
    assert d == a and len(list(tmp_path.glob("*.mo"))) == 3


def test_d2_mo_write_failure_does_not_fail_backup_or_block_write(tmp_path, monkeypatch):
    real = mofile.write_mo

    def flaky(folder, slot, mo, unique=False):
        if slot in (7, 8):
            raise OSError("synthetic .mo write error")
        return real(folder, slot, mo, unique)
    monkeypatch.setattr(mofile, "write_mo", flaky)
    dev = OrigFake()
    fails = []
    folder = backup.make_backup(dev.read_all(), tmp_path / "bk0", failures=fails)
    assert [s for s, _ in fails] == [7, 8] and ".mo 저장 실패" in fails[0][1]
    assert backup.verify_backup(folder)[7] == dev.slots[7]          # device.bin·backup.json 은 성공
    rows, _ = plan.build_plan(dev.read_all(), MO_DIR)
    sel = [next(r.slot for r in rows if r.status == plan.ST_CHANGE)]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk")
    assert res.write_ok and res.success and dev.written == sel   # 쓰기를 막지 않는다
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert "경고: 백업 .mo 생략 02C" in txt and "경고: 백업 .mo 생략 02D" in txt and ".mo 저장 실패" in txt


def test_d2_device_bin_failure_still_fails_backup(tmp_path, monkeypatch):
    orig = Path.write_bytes
    monkeypatch.setattr(Path, "write_bytes", lambda self, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        backup.make_backup(OrigFake().read_all(), tmp_path / "bk")
    monkeypatch.setattr(Path, "write_bytes", orig)


# ---------------------------------------------------------------- D3
def doc_with(name, slot="02C"):
    from test_tonepack import pack, preset
    return pack(preset(slot=slot, name=name))


def test_d3_device_name_is_name_and_file_name_has_slot(tmp_path):
    from test_tonepack import write_json
    res = tonepack.convert([write_json(tmp_path, doc_with("Hello World 16ch"))], tmp_path / "o")
    assert res.ok and len(res.presets[0]["name"]) == 16
    assert res.files[0].name == "02C Hello World 16ch.mo"
    assert json.loads(res.files[0].read_text(encoding="utf-8"))["fileInfo"]["preset_name"] == "Hello World 16ch"


def test_d3_symbols_and_filename_cleanup(tmp_path):
    from test_tonepack import write_json
    res = tonepack.convert([write_json(tmp_path, doc_with(NASTY))], tmp_path / "o")
    assert res.ok, [e.text() for e in res.errors]
    assert res.files[0].name == "02C ___^`{_} Name 16.mo"
    assert json.loads(res.files[0].read_text(encoding="utf-8"))["fileInfo"]["preset_name"] == NASTY
    res2 = tonepack.convert([write_json(tmp_path, doc_with("Trailing dot."), "t.json")], tmp_path / "o2")
    assert res2.ok and res2.files[0].name == "02C Trailing dot_.mo"


@pytest.mark.parametrize("name", ["A" * 17, "한글", "Name한", "탭\tName"])
def test_d3_rejects_17_chars_hangul_control(tmp_path, name):
    from test_tonepack import load
    e = load(tmp_path, doc_with(name))[1]
    assert len(e) == 1 and "name" in e[0].reason and e[0].slot == "02C"


def test_d3_example_fixture_matches_prompt(tmp_path):
    """작업장 루트의 개인 프롬프트가 있을 때만: 그 안의 JSON 예시가 지금의 변환기를 오류 없이 통과하는지."""
    fx = json.loads((Path(__file__).resolve().parent / "fixtures" / "tonepack_example.json").read_text(encoding="utf-8"))
    path = Path(__file__).resolve().parents[2] / "톤데이터_요청프롬프트.txt"
    if not path.is_file():
        pytest.skip("개인 프롬프트 파일이 없어 건너뜁니다")
    prompt = path.read_text(encoding="utf-8")
    assert fx["presets"][0]["name"] == "Antifreeze CLN" and '"name":"Antifreeze CLN"' in prompt
    example = json.loads(prompt[prompt.index('{"format"'):prompt.index("\n\n[7]")])
    from test_tonepack import load
    assert load(tmp_path, example)[1] == []


# ---------------------------------------------------------------- D4 (감사 C)
def test_c01_fallback_results_are_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "fallback_dir", lambda: tmp_path / "fb")
    now = datetime(2026, 11, 10, 16, 0, 0)
    a = backup.write_fallback_result("첫 기록: 미확인 50D", now)
    b = backup.write_fallback_result("두 번째 기록", now)
    c = backup.write_fallback_result("세 번째 기록", now)
    assert len({a, b, c}) == 3
    assert a.read_text(encoding="utf-8") == "첫 기록: 미확인 50D"
    assert b.name.endswith("-2.txt") and c.name.endswith("-3.txt")
    assert b.read_text(encoding="utf-8") == "두 번째 기록"


@pytest.mark.parametrize("name", ["tonepack_mo_20261110-150000.partial", "x.partial-2", "X.PARTIAL"])
def test_c02_partial_folder_is_not_a_tone_folder(tmp_path, dev_data, name):
    d = tmp_path / name
    d.mkdir()
    mo = codec.decode(dev_data[1])
    mofile.write_mo(d, 1, mo)
    with pytest.raises(ValueError) as ex:
        plan.build_plan(dev_data, d)
    assert "불완전한 변환 폴더" in str(ex.value)
    ok = tmp_path / "normal_folder"
    ok.mkdir()
    mofile.write_mo(ok, 1, mo)
    plan.build_plan(dev_data, ok)                   # 일반 폴더는 그대로 열린다


def test_c02_leftover_partial_from_failed_cleanup_cannot_be_applied(tmp_path, dev_data, monkeypatch):
    from test_tonepack import pack, preset, write_json
    src = write_json(tmp_path, pack(preset(slot="01A"), preset(slot="01B", name="Second")), "two.json")
    real, n = mofile.write_mo, {"c": 0}

    def boom(folder, slot, mo, unique=False):
        n["c"] += 1
        if n["c"] == 2:
            raise OSError("synthetic write error")
        return real(folder, slot, mo, unique)
    monkeypatch.setattr(mofile, "write_mo", boom)
    monkeypatch.setattr(tonepack.shutil, "rmtree", lambda p: (_ for _ in ()).throw(OSError("locked")))
    res = tonepack.convert([src])
    leftovers = [p for p in tmp_path.iterdir() if ".partial" in p.name]
    assert not res.ok and len(leftovers) == 1 and len(list(leftovers[0].glob("*.mo"))) == 1
    with pytest.raises(ValueError):
        plan.build_plan(dev_data, leftovers[0])     # 수동으로 골라도 입력이 되지 않는다


def test_c02_window_refuses_partial_folder(tmp_path):
    from PySide6.QtWidgets import QApplication
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    msgs = []
    win.info = msgs.append
    d = tmp_path / "a_mo_x.partial"
    d.mkdir()
    win.set_folder(d)
    assert win.ed_folder.text() == "" and "불완전한 변환 폴더" in msgs[-1]
    win.handle_dropped([d])
    assert win.ed_folder.text() == ""
    win.close()


def test_c03_standalone_cli_backup_writes_warnings_to_result_file(tmp_path, capsys):
    import cli
    dev = OrigFake()
    dev.slots[200] = dev.slots[200][:18] + b"\xff\xff" + dev.slots[200][20:]   # 해석 불가 50D
    cli.cmd_backup(dev, SimpleNamespace(folder=str(tmp_path / "b")))
    folder = next((tmp_path / "b").iterdir())
    txt = (folder / "결과.txt").read_text(encoding="utf-8")
    assert "백업: 완료" in txt and "경고: 백업 .mo 생략 50D" in txt and "해석 불가" in txt and ".mo 199개" in txt
    assert "50D" in capsys.readouterr().out


def test_c03_standalone_cli_backup_mo_write_failure_recorded(tmp_path, monkeypatch):
    import cli
    real = mofile.write_mo
    monkeypatch.setattr(mofile, "write_mo", lambda f, s, m, unique=False: (_ for _ in ()).throw(OSError("x")) if s == 3 else real(f, s, m, unique))
    cli.cmd_backup(OrigFake(), SimpleNamespace(folder=str(tmp_path / "b")))
    txt = (next((tmp_path / "b").iterdir()) / "결과.txt").read_text(encoding="utf-8")
    assert "경고: 백업 .mo 생략 01C" in txt and ".mo 저장 실패" in txt
