"""톤 JSON -> .mo 변환 시험(장비 없이). 규칙 정본: docs/톤JSON_형식.md"""
import copy
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ge150 import catalog, codec, excel, mofile, plan, tonepack
from ge150 import device
from ge150 import protocol as P

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "tonepack_example.json"
NOW = datetime(2026, 11, 10, 15, 0, 0)


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def write_json(tmp_path, doc, name="pack.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def load(tmp_path, doc):
    return tonepack.load_tonepacks([write_json(tmp_path, doc)])


def errs(tmp_path, doc):
    return [e.text() for e in load(tmp_path, doc)[1]]


def preset(**kw):
    p = copy.deepcopy(example()["presets"][0])
    p.update(kw)
    return p


def pack(*presets):
    d = example()
    d["presets"] = list(presets)
    return d


def mod(p, key, **kw):
    p["modules"][key].update(kw)
    return p


# ---------------------------------------------------------------- 1. 프롬프트 예시
def test_prompt_example_converts(tmp_path):
    src = tmp_path / "tonepack.json"
    src.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    res = tonepack.convert([src], now=NOW)
    assert res.ok and not res.errors and len(res.files) == 1
    assert res.out_dir.name == "tonepack_mo_20261110-150000" and res.out_dir.parent == tmp_path
    f = res.files[0]
    assert f.name == "01A Antifreeze CLN.mo"
    mo = json.loads(f.read_text(encoding="utf-8"))
    assert mo["fileInfo"]["preset_name"] == "Antifreeze CLN"   # D3: 장비 이름 = name 그대로
    assert mo["effectModule"]["3"]["Data"]["TUBE"] == 4                    # "6V6" -> 4
    assert mo["effectModule"]["5"]["Data"] == {k: 16 for k in ("100Hz", "250Hz", "630Hz", "1.6KHz", "4KHz")}  # 0dB -> 16
    assert mo["effectModule"]["7"]["Data"]["TIME"] == 380                  # 그대로
    assert mo["effectModule"]["7"]["Data"]["SUB-D"] == 0 and mo["effectModule"]["3"]["Data"]["MIC"] == 0
    assert mo["Exp"] == tonepack.DEFAULT_EXP
    assert [m["SWITCH"] for m in (mo["effectModule"][str(i)] for i in range(9))] == [1, 0, 1, 1, 1, 0, 1, 0, 1]
    assert mofile.read_mo(f).errors == []
    assert len(codec.encode(mo)) == 244 and codec.decode(codec.encode(mo))["effectModule"] == mo["effectModule"]


# ---------------------------------------------------------------- 2. 왕복(장비 200개 -> 톤 JSON -> .mo)
def _to_tonepack(dev_data):
    """시험용 역변환: 장비 바이트 -> 톤 JSON. 사용자 샘플·PITCH 모델·범위 밖 값 슬롯은 제외."""
    presets, skipped = [], []
    for s in range(1, 201):
        mo = codec.decode(dev_data[s])
        name = mo["fileInfo"]["preset_name"]
        short = name                                                        # D3: 장비 이름이 곧 name
        ok = not mofile.validate_mo(mo) and bool(short.strip())
        mods = {}
        for i in range(9):
            m = mo["effectModule"][str(i)]
            ps = catalog.params(i, m["TYPE"])
            if m["SWITCH"] not in (0, 1) or catalog.is_user_sample(i, m["TYPE"]) or any(p["type"] == 0 and p["step"] != 1 for p in ps):
                ok = False
                break
            params = {}
            for p in ps:
                v = m["Data"][p["name"]]
                params[p["name"]] = p["options"][v] if p["type"] == 1 else (v + int(p["min"]) if p["min"] < 0 else v)
            mods[catalog.MODULES[i]] = {"on": m["SWITCH"] == 1, "model": catalog.model_name(i, m["TYPE"]), "params": params}
        if not ok:
            skipped.append(s)
            continue
        presets.append({"slot": P.slot_label(s), "name": short, "modules": mods})
    return {"format": "GE150PRO-TONEPACK", "version": 1, "presets": presets}, skipped


def test_roundtrip_device_to_tonepack_to_mo(tmp_path, dev_data):
    doc, skipped = _to_tonepack(dev_data)
    assert len(doc["presets"]) >= 160 and len(doc["presets"]) + len(skipped) == 200
    res = tonepack.convert([write_json(tmp_path, doc)], tmp_path / "out", now=NOW)
    assert res.ok, [e.text() for e in res.errors][:5]
    assert len(res.files) == len(doc["presets"])
    for p in res.presets:
        orig = codec.decode(dev_data[p["slot"]])
        assert p["mo"]["effectModule"] == orig["effectModule"], p["label"]
        assert p["mo"]["fileInfo"]["preset_name"] == orig["fileInfo"]["preset_name"]   # 이름까지 그대로
    # 만든 폴더를 그대로 계획에 넣으면 오류가 없고 장비와 같은 슬롯은 '동일'
    rows, _ = plan.build_plan(dev_data, res.out_dir)
    assert not any(r.status == plan.ST_ERROR for r in rows)
    assert sum(1 for r in rows if r.entry) == len(doc["presets"])


# ---------------------------------------------------------------- 3. 오류
@pytest.mark.parametrize("mut,frag", [
    (lambda d: d.update(format="X"), "format"),
    (lambda d: d.update(version=2), "version"),
    (lambda d: d.update(version="1"), "version"),
    (lambda d: d.update(version=True), "version"),
    (lambda d: d.update(presets=[]), "presets"),
    (lambda d: d.update(presets=[preset()] * 201), "presets"),
    (lambda d: d.update(presets="x"), "presets"),
])
def test_file_level_errors(tmp_path, mut, frag):
    d = pack(preset())
    mut(d)
    e = errs(tmp_path, d)
    assert len(e) == 1 and frag in e[0]


def test_not_json_and_not_object(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    assert "읽을 수 없습니다" in tonepack.load_tonepacks([p])[1][0].text()
    assert errs(tmp_path, [1, 2])


@pytest.mark.parametrize("slot", ["1A", "51A", "00A", "01E", "", None, 5, "01AA"])
def test_bad_slot_format(tmp_path, slot):
    e = errs(tmp_path, pack(preset(slot=slot)))
    assert any("slot 형식" in x for x in e), e


def test_slot_lowercase_is_normalized(tmp_path):
    pr, e, w = load(tmp_path, pack(preset(slot="01a")))
    assert not e and pr[0]["label"] == "01A"


def test_duplicate_slot_in_file_and_between_files(tmp_path):
    e = errs(tmp_path, pack(preset(slot="02A"), preset(slot="02A", name="Other")))
    assert len(e) == 1 and "중복" in e[0] and "02A" in e[0]
    a = write_json(tmp_path, pack(preset(slot="03A")), "a.json")
    b = write_json(tmp_path, pack(preset(slot="03A", name="B"), preset(slot="03B", name="B2")), "b.json")
    pr, er, _ = tonepack.load_tonepacks([a, b])
    assert len(er) == 1 and "중복" in er[0].reason and "a.json" in er[0].reason and er[0].file == "b.json"
    assert [p["label"] for p in pr] == ["03A", "03B"]


@pytest.mark.parametrize("name", ["A" * 17, "한글이름", "AntiFreeze한", "", "   ", None, 7, "Tab\tName"])
def test_bad_name(tmp_path, name):
    e = errs(tmp_path, pack(preset(name=name)))
    assert any("name" in x for x in e), e


@pytest.mark.parametrize("name", ["A" * 16, "Anti-Freeze", "AntiFrz!", "<>\\^`{|} Name 16", "01A Old Style", "~!@#$%^&*()_+-=["])
def test_name_16_chars_and_symbols_ok(tmp_path, name):
    pr, e, _ = load(tmp_path, pack(preset(slot="02C", name=name)))
    assert not e, [x.text() for x in e]
    assert pr[0]["name"] == name.rstrip() and pr[0]["mo"]["fileInfo"]["preset_name"] == name.rstrip()   # 슬롯 번호를 붙이지 않음


def test_name_12_chars_ok_and_device_name_16(tmp_path):
    pr, e, _ = load(tmp_path, pack(preset(slot="50D", name="A" * 12)))
    assert not e and pr[0]["name"] == "A" * 12


def test_unknown_module_key(tmp_path):
    p = preset()
    p["modules"]["WAH"] = {"on": True, "model": "X", "params": {}}
    e = errs(tmp_path, pack(p))
    assert len(e) == 1 and "알 수 없는 모듈" in e[0] and "WAH" in e[0]


def test_unknown_model_with_suggestions(tmp_path):
    p = mod(preset(), "FX/COMP", model="YELOW COMP")
    pr, er, _ = load(tmp_path, pack(p))
    assert len(er) == 1 and er[0].module == "FX/COMP"
    assert "YELLOW COMP" in er[0].reason and er[0].reason.count(",") == 2          # 비슷한 이름 3개


def test_model_matching_rules(tmp_path):
    p = preset()
    mod(p, "FX/COMP", model="01: yellow-comp")
    mod(p, "AMP", model="65us dx")
    mod(p, "NS GATE", model="intel reducer", params={"SENS": 40})
    pr, e, _ = load(tmp_path, pack(p))
    assert not e, e
    m = pr[0]["mo"]["effectModule"]
    assert m["0"]["TYPE"] == 6 and m["2"]["TYPE"] == 0
    assert m["4"]["TYPE"] == 1                       # NS GATE 의 INTEL REDUCER(FX/COMP 의 11번과 구분)
    p2 = mod(preset(), "FX/COMP", model="INTEL REDUCER", params={"THRES": 1, "DEPTH": 2, "LEVEL": 3})
    assert load(tmp_path, pack(p2))[0][0]["mo"]["effectModule"]["0"]["TYPE"] == 11


def test_unknown_knob(tmp_path):
    p = preset()
    p["modules"]["AMP"]["params"]["WOW"] = 5
    pr, er, _ = load(tmp_path, pack(p))
    assert len(er) == 1 and er[0].knob == "WOW" and er[0].module == "AMP"


@pytest.mark.parametrize("module,knob,val", [
    ("AMP", "GAIN", 101), ("AMP", "GAIN", -1), ("EQ", "100Hz", 17), ("EQ", "4KHz", -17),
    ("DELAY", "TIME", 39), ("DELAY", "TIME", 2501), ("REVERB", "P.DELAY", 501),
])
def test_out_of_range(tmp_path, module, knob, val):
    p = preset()
    p["modules"][module]["params"][knob] = val
    e = load(tmp_path, pack(p))[1]
    assert len(e) == 1 and e[0].knob == knob and "범위" in e[0].reason


def test_range_boundaries_ok(tmp_path):
    p = preset()
    p["modules"]["EQ"]["params"].update({"100Hz": -16, "250Hz": 16})
    p["modules"]["DELAY"]["params"]["TIME"] = 2500
    p["modules"]["REVERB"]["params"]["P.DELAY"] = 500
    pr, e, _ = load(tmp_path, pack(p))
    assert not e and pr[0]["mo"]["effectModule"]["5"]["Data"]["100Hz"] == 0 and pr[0]["mo"]["effectModule"]["5"]["Data"]["250Hz"] == 32


@pytest.mark.parametrize("val", [50.5, 50.0, "50", True, None, [50]])
def test_not_integer(tmp_path, val):
    p = preset()
    p["modules"]["AMP"]["params"]["GAIN"] = val
    e = load(tmp_path, pack(p))[1]
    assert len(e) == 1 and e[0].knob == "GAIN" and "정수" in e[0].reason


@pytest.mark.parametrize("val", [3, True, "EL99", None])
def test_list_knob_needs_valid_text(tmp_path, val):
    p = preset()
    p["modules"]["CAB"]["params"]["TUBE"] = val
    e = load(tmp_path, pack(p))[1]
    assert len(e) == 1 and e[0].knob == "TUBE" and e[0].module == "CAB"


def test_list_knob_case_insensitive(tmp_path):
    p = preset()
    p["modules"]["DELAY"]["params"]["SUB-D"] = "1/8d"
    pr, e, _ = load(tmp_path, pack(p))
    assert not e and pr[0]["mo"]["effectModule"]["7"]["Data"]["SUB-D"] == 6


def test_forbidden_models(tmp_path):
    for module, model in (("AMP", "EMPTY"), ("CAB", "EMPTY"), ("MOD", "PITCH SHIFT"), ("MOD", "DETUNE")):
        p = mod(preset(), module, model=model, params={})
        e = load(tmp_path, pack(p))[1]
        assert len(e) == 1 and e[0].module == module, (module, model, [x.text() for x in e])
        assert "쓸 수 없습니다" in e[0].reason


def test_on_must_be_bool_and_exp_validation(tmp_path):
    p = mod(preset(), "AMP", on="yes")
    assert any("on" in x for x in errs(tmp_path, pack(p)))
    p = preset(exp={"MODULE_CTRL": 1})
    assert any("exp" in x for x in errs(tmp_path, pack(p)))
    p = preset(exp={"FUN_SWITCH": 1, "MODULE_CTRL": 2, "PARA_CTRL": 3, "VOL_MAX": 90, "VOL_MIN": 10, "VOL_SWITCH": 0})
    pr, e, _ = load(tmp_path, pack(p))
    assert not e and pr[0]["mo"]["Exp"]["VOL_MAX"] == 90


def test_error_blocks_output(tmp_path):
    src = write_json(tmp_path, pack(preset(), preset(slot="01B", name="x" * 17)))
    res = tonepack.convert([src], tmp_path / "out", now=NOW)
    assert not res.ok and res.out_dir is None and not (tmp_path / "out").exists()


# ---------------------------------------------------------------- 4. 경고(기본값)
def test_missing_module_and_knob_defaults(tmp_path):
    p = preset()
    del p["modules"]["MOD"]
    del p["modules"]["EQ"]
    for k in ("TIME", "SUB-D"):
        del p["modules"]["DELAY"]["params"][k]
    del p["modules"]["REVERB"]["params"]["P.DELAY"]
    del p["modules"]["AMP"]["params"]["GAIN"]
    del p["modules"]["CAB"]["params"]["MIC"]
    pr, e, w = load(tmp_path, pack(p))
    assert not e
    m = pr[0]["mo"]["effectModule"]
    assert m["6"]["SWITCH"] == 0 and m["6"]["TYPE"] == 0 and set(m["6"]["Data"].values()) == {50}   # MOD 첫 모델 PHASER
    assert m["5"]["SWITCH"] == 0 and m["5"]["TYPE"] == 0 and set(m["5"]["Data"].values()) == {16}   # EQ 0dB
    assert m["7"]["Data"]["TIME"] == 400 and m["7"]["Data"]["SUB-D"] == 0
    assert m["8"]["Data"]["P.DELAY"] == 20 and m["2"]["Data"]["GAIN"] == 50 and m["3"]["Data"]["MIC"] == 0
    text = [x.text() for x in w]
    assert any("모듈 MOD" in t and "채웁니다" in t for t in text) and any("모듈 EQ" in t for t in text)
    assert any("노브 TIME" in t and "400" in t for t in text) and any("노브 GAIN" in t for t in text)


def test_freq_warning_and_default(tmp_path):
    p = mod(preset(), "EQ", model="CUSTOM EQ", params={"GAIN1": 3, "FREQ1": 800})
    pr, e, w = load(tmp_path, pack(p))
    assert not e
    d = pr[0]["mo"]["effectModule"]["5"]["Data"]
    assert d == {"GAIN1": 19, "FREQ1": 800, "GAIN2": 16, "FREQ2": 9000, "GAIN3": 16, "FREQ3": 9000}
    assert sum("FREQ 값은 장비에서 확인되기 전" in x.text() for x in w) == 1


# ---------------------------------------------------------------- 5. 합치기·덮어쓰지 않음·메타
def test_merge_files_and_no_overwrite(tmp_path):
    a = write_json(tmp_path, pack(preset(slot="01A"), preset(slot="01B", name="B")), "tonepack_01-25.json")
    b = write_json(tmp_path, pack(preset(slot="26A", name="C")), "tonepack_26-50.json")
    r1 = tonepack.convert([a, b], now=NOW)
    assert r1.ok and len(r1.files) == 3 and r1.out_dir.name == "tonepack_01-25_mo_20261110-150000"   # 첫 파일 이름 기준
    marker = r1.files[0].read_text(encoding="utf-8")
    r2 = tonepack.convert([a, b], now=NOW)                         # 같은 시각 -> 같은 이름이 이미 있음
    assert r2.ok and r2.out_dir != r1.out_dir and r2.out_dir.exists() and r1.files[0].read_text(encoding="utf-8") == marker
    r3 = tonepack.convert([a], tmp_path / "fixed", now=NOW)
    r4 = tonepack.convert([a], tmp_path / "fixed", now=NOW)
    assert r3.out_dir.name == "fixed" and r4.out_dir.name == "fixed_20261110-150000" and r3.out_dir != r4.out_dir


def test_meta_file_and_excel_columns(tmp_path, dev_data):
    d = pack(preset(slot="50D", song="Antifreeze", artist="검정치마", role="CLEAN"))
    res = tonepack.convert([write_json(tmp_path, d)], tmp_path / "o", now=NOW)
    meta = tonepack.load_meta(res.out_dir)
    assert meta[200] == {"song": "Antifreeze", "artist": "검정치마", "role": "CLEAN", "name": "Antifreeze CLN"}
    assert tonepack.load_meta(tmp_path) == {}
    dev = device.FakeDevice()
    rows, _ = plan.build_plan(dev.read_all(), res.out_dir)
    from openpyxl import load_workbook
    ws = load_workbook(excel.export_xlsx(tmp_path / "x.xlsx", rows, dev.read_all(), "folder", meta=meta))["요약"]
    hdr = [c.value for c in ws[1]]
    assert hdr[-3:] == ["곡", "아티스트", "역할"]
    row = [c.value for c in ws[201]]
    assert row[0] == "50D" and row[-3:] == ["Antifreeze", "검정치마", "CLEAN"]
    assert not any(c.value for c in ws[2][-3:])                     # 톤 JSON 에서 오지 않은 슬롯은 빈 칸
    # 메타 없이(.mo 폴더만) 내보내도 동작
    excel.export_xlsx(tmp_path / "y.xlsx", rows, dev.read_all(), "device")


def test_cli_convert(tmp_path, capsys):
    import cli
    src = write_json(tmp_path, pack(preset()))
    cli.main(["convert", str(src), "--out", str(tmp_path / "cliout")])
    assert (tmp_path / "cliout" / "01A Antifreeze CLN.mo").exists() and "변환 완료" in capsys.readouterr().out
    bad = write_json(tmp_path, pack(preset(name="한글")), "bad.json")
    with pytest.raises(SystemExit) as ex:
        cli.main(["convert", str(bad)])
    assert ex.value.code == 1 and "오류" in capsys.readouterr().out


# ---------------------------------------------------------------- 6. 창: 끌어다 놓기
def _drop_event(paths):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    ev._keep = md  # QDropEvent 는 mime 데이터를 소유하지 않으므로 수명을 이어 준다(안 그러면 접근 위반)
    return ev


def _window(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    win.msgs, win.issue_windows = [], []
    win.info = win.msgs.append
    win.show_issues = lambda title, issues: win.issue_windows.append((title, list(issues)))
    win.show()
    win.connect_and_read()
    t0 = time.time()
    while time.time() - t0 < 20 and not (len(win.dev_data) == 200 and not win.busy):
        app.processEvents()
        time.sleep(0.01)
    assert len(win.dev_data) == 200
    return app, win


def test_app_drop_json_converts_and_shows_table(tmp_path):
    app, win = _window(tmp_path)
    src = write_json(tmp_path, pack(preset(slot="02B", name="Dropped")), "drop.json")
    assert win.acceptDrops()
    ev = _drop_event([src])
    win.dropEvent(ev)
    out = Path(win.ed_folder.text())
    assert out.parent == tmp_path and out.name.startswith("drop_mo_") and (out / "02B 02B Dropped.mo").exists() is False
    assert (out / "02B Dropped.mo").exists()
    row = win.rows[5]
    assert row.label == "02B" and row.new_name == "Dropped" and row.status == "변경"
    assert win.table.item(5, 3).text() == "Dropped" and win.table.item(5, 4).text() == "변경"
    assert win._checked_slots() == [6] and win.issue_windows == []
    win.close()


def test_app_drop_multiple_json_merges(tmp_path):
    app, win = _window(tmp_path)
    a = write_json(tmp_path, pack(preset(slot="01A")), "a.json")
    b = write_json(tmp_path, pack(preset(slot="26A", name="Two")), "b.json")
    win.handle_dropped([a, b])
    assert sorted(r.slot for r in win.rows if r.status in ("변경", "동일")) == [1, 101]
    win.close()


def test_app_drop_errors_and_warnings_windows(tmp_path):
    app, win = _window(tmp_path)
    bad = write_json(tmp_path, pack(preset(slot="01A", name="한글"), preset(slot="01A", name="Dup")), "bad.json")
    win.handle_dropped([bad])
    assert win.ed_folder.text() == "" and len(win.issue_windows) == 1
    title, issues = win.issue_windows[0]
    assert "오류" in title and {i.slot for i in issues} == {"01A"} and all(i.reason for i in issues)
    assert not list(tmp_path.glob("bad_mo_*"))                       # 오류가 있으면 .mo 를 만들지 않음
    p = preset(slot="03C")
    del p["modules"]["MOD"]
    warn_src = write_json(tmp_path, pack(p), "warn.json")
    win.issue_windows.clear()
    win.handle_dropped([warn_src])
    assert win.ed_folder.text() and len(win.issue_windows) == 1 and "경고" in win.issue_windows[0][0]
    assert win.rows[10].new_name == "Antifreeze CLN"
    win.close()


def test_app_drop_folder_opens_as_tone_folder_and_json_button(tmp_path):
    app, win = _window(tmp_path)
    folder = tmp_path / "tone"
    folder.mkdir()
    mo = codec.decode(device.FakeDevice().read_all()[3])
    mo["fileInfo"]["preset_name"] = "03C Folder"
    mofile.write_mo(folder, 3, mo)
    win.handle_dropped([folder])
    assert win.ed_folder.text() == str(folder) and win.rows[2].new_name == "03C Folder"
    win.handle_dropped([tmp_path / "nothing.txt"])
    assert ".json" in win.msgs[-1]
    src = write_json(tmp_path, pack(preset(slot="04D", name="Btn")), "btn.json")
    win.pick_files = lambda: [str(src)]
    win.btn_json.click()
    assert win.rows[15].new_name == "Btn"
    win.close()


# ---------------------------------------------------------------- 모든 모델 이름이 자기 자신을 찾는지
def test_every_model_name_resolves_to_itself():
    for mod in range(9):
        norm_seen = {}
        for t in range(catalog.model_count(mod)):
            name = catalog.model_name(mod, t)
            if name == "EMPTY":                               # 사용자 샘플 칸은 금지 모델
                continue
            found, _ = tonepack._find_model(mod, name)
            assert found == t, (catalog.MODULES[mod], name, found, t)
            assert tonepack._norm(name) not in norm_seen, ("정규화 이름 충돌", name, norm_seen.get(tonepack._norm(name)))
            norm_seen[tonepack._norm(name)] = name


def test_app_path_argument_before_device_read(tmp_path):
    """exe 에 경로 인자를 주거나 아이콘에 끌어다 놓으면 장비를 읽기 전에 변환하고, 읽기가 끝나면 표에 나타난다."""
    from PySide6.QtWidgets import QApplication
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    win.info = lambda t: None
    win.show_issues = lambda *a: None
    src = write_json(tmp_path, pack(preset(slot="05C", name="Arg")), "arg.json")
    win.handle_dropped([str(src)])
    assert win.ed_folder.text() and win.table.rowCount() == 0
    win.connect_and_read()
    t0 = time.time()
    while time.time() - t0 < 20 and not (len(win.dev_data) == 200 and not win.busy):
        app.processEvents()
        time.sleep(0.01)
    assert win.rows[18].new_name == "Arg" and win.table.rowCount() == 200
    win.close()
