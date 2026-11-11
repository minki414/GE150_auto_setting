"""H단계 시험: 장비 -> 톤 JSON 내보내기(ge150/export.py), cli export-json, 창의 [장비→톤 JSON 저장] 버튼."""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cli
from ge150 import backup, catalog, codec, device, export, models, tonepack
from ge150 import protocol as P

GE, PRO = models.GE150, models.PRO


def _doc_roundtrip(tmp_path, data, profile, **kw):
    """내보내기 -> 파일 -> 톤 JSON 검사 -> 장비 바이트. (doc, warnings, presets, errors)"""
    warns = []
    doc = export.device_to_doc(data, profile, "시험", warnings=warns, **kw)
    path = export.write_doc(tmp_path / "out.json", doc)
    presets, errors, _ = tonepack.load_tonepacks([path], profile=profile)
    return doc, warns, presets, errors


# ---------------------------------------------------------------- 항목
def test_item_has_nine_modules_with_display_values(dev_data):
    mo = codec.decode(dev_data[1], PRO)
    item = export.preset_to_item(1, mo, PRO)
    assert item["slot"] == "01A" and item["name"] == mo["fileInfo"]["preset_name"] and "exp" not in item
    assert list(item["modules"]) == catalog.MODULES
    for i, label in enumerate(catalog.MODULES):
        m = mo["effectModule"][str(i)]
        spec = item["modules"][label]
        assert spec["on"] is bool(m["SWITCH"]) and spec["model"] == catalog.model(i, m["TYPE"], PRO)["name"]
        assert list(spec["params"]) == [p["name"] for p in catalog.params(i, m["TYPE"], PRO)]


def test_item_display_values_list_eq_and_raw():
    mo = codec.decode(device.FakeDevice().read_all()[1], PRO)
    mo["effectModule"]["3"].update(TYPE=0)
    mo["effectModule"]["3"]["Data"] = {"TUBE": 4, "MIC": 1, "CENTER": 30, "DISTANCE": 40}
    mo["effectModule"]["5"].update(TYPE=0)
    mo["effectModule"]["5"]["Data"] = {"100Hz": 0, "250Hz": 16, "630Hz": 32, "1.6KHz": 20, "4KHz": 1}
    mo["effectModule"]["7"].update(TYPE=0)
    mo["effectModule"]["7"]["Data"] = {"LEVEL": 20, "F.BACK": 25, "TIME": 380, "SUB-D": 3}
    mods = export.preset_to_item(1, mo, PRO)["modules"]
    assert mods["CAB"]["params"] == {"TUBE": "6V6", "MIC": "U47", "CENTER": 30, "DISTANCE": 40}       # 목록형 = 글자
    assert mods["EQ"]["params"] == {"100Hz": -16, "250Hz": 0, "630Hz": 16, "1.6KHz": 4, "4KHz": -15}   # 음수 범위 = raw + min
    assert mods["DELAY"]["params"] == {"LEVEL": 20, "F.BACK": 25, "TIME": 380, "SUB-D": "1/4D"}         # 그 밖 = raw 그대로


def test_item_exp_only_when_not_default(dev_data):
    mo = codec.decode(dev_data[1], PRO)
    mo["Exp"] = dict(tonepack.DEFAULT_EXP)
    assert "exp" not in export.preset_to_item(1, mo, PRO)
    mo["Exp"]["VOL_MAX"] = 77
    item = export.preset_to_item(1, mo, PRO)
    assert item["exp"] == {k: mo["Exp"][k] for k in codec.EXP_ORDER} and item["exp"]["VOL_MAX"] == 77


def test_item_uses_catalog_names_of_the_profile():
    mods = export.preset_to_item(1, codec.decode(device.FakeDevice(profile=GE).read_all()[1], GE), GE)["modules"]
    assert list(mods["DELAY"]["params"]) == ["LEVEL", "FEEDBACK", "TIME", "SUB-D"] and "PRE DELAY" in mods["REVERB"]["params"]


# ---------------------------------------------------------------- 문서·왕복
def test_doc_header_and_songs(dev_data):
    songs = {1: ("검정치마", "Antifreeze"), 2: ("", "캐논 (설명)")}
    doc = export.device_to_doc(dev_data, PRO, "제목", songs)
    assert (doc["format"], doc["version"], doc["device"], doc["title"]) == ("GE150-TONEPACK", 1, "GE150PRO", "제목")
    assert [p["slot"] for p in doc["presets"]] == [P.slot_label(s) for s in range(1, 201)]
    p = doc["presets"]
    assert [(x["song"], x["artist"], x["role"]) for x in p[:4]] == [("Antifreeze", "검정치마", r) for r in ("CLEAN", "CRUNCH", "DRIVE", "SOLO")]
    assert (p[4]["song"], p[4]["artist"], p[7]["role"]) == ("캐논 (설명)", "", "SOLO")
    assert "song" not in p[8] and "role" not in p[8]                      # 목록에 없는 뱅크는 곡 정보 없음


def test_roundtrip_pro_dump_matches_normalize(tmp_path, dev_data):
    doc, warns, presets, errors = _doc_roundtrip(tmp_path, dev_data, PRO)
    bad = {e.slot for e in errors}
    assert len(doc["presets"]) == 200 and len(presets) + len(bad) == 200
    print("Pro 시험 덤프: 왕복 %d개, 금지 모델로 제외 %d개" % (len(presets), len(bad)))
    assert len(presets) >= 190 and warns                                  # 금지 모델 프리셋은 경고로 미리 알린다
    for p in presets:
        assert codec.encode(p["mo"], PRO) == codec.normalize(dev_data[p["slot"]], PRO), p["label"]


def _ge150_sample_data():
    """GE150 용 장비 자료: 샘플 톤(GE150판)을 변환해 장비 바이트(읽기 크기 162)로 만든다 — 값이 모두 올바른 범위에 있다."""
    sample = Path(__file__).resolve().parents[1] / "samples" / "합주_GE150.json"
    presets, errors, _ = tonepack.load_tonepacks([sample], profile=GE)
    assert errors == []
    return {p["slot"]: codec.encode(p["mo"], GE) + bytes(4) for p in presets}


def test_roundtrip_ge150_data_all_pass(tmp_path):
    data = _ge150_sample_data()
    doc, warns, presets, errors = _doc_roundtrip(tmp_path, data, GE)
    assert errors == [] and warns == [] and len(presets) == 200 and doc["device"] == "GE150"
    for p in presets:
        assert codec.encode(p["mo"], GE) == codec.normalize(data[p["slot"]], GE)


def test_out_of_range_values_are_exported_with_warning(tmp_path):
    """합성 장비 자료(모든 노브 0)는 DELAY TIME 0 처럼 톤 JSON 범위 밖 값을 가진다 — 그대로 내보내고 경고로 알린다."""
    data = device.FakeDevice(profile=GE).read_all()
    doc, warns, presets, errors = _doc_roundtrip(tmp_path, data, GE)
    assert len(doc["presets"]) == 200 and doc["presets"][0]["modules"]["DELAY"]["params"]["TIME"] == 0
    assert any("01A" in w and "TIME" in w for w in warns) and len(presets) == 0 and len(errors) >= 200


def test_roundtrip_keeps_non_default_exp(tmp_path):
    data = dict(_ge150_sample_data())
    mo = codec.decode(data[5], GE)
    mo["Exp"] = {"MODULE_CTRL": 1, "PARA_CTRL": 2, "FUN_SWITCH": 3, "VOL_SWITCH": 0, "VOL_MIN": 4, "VOL_MAX": 90}
    data[5] = codec.encode(mo, GE) + bytes(4)
    _, _, presets, errors = _doc_roundtrip(tmp_path, data, GE)
    assert errors == [] and next(p for p in presets if p["slot"] == 5)["mo"]["Exp"] == mo["Exp"]


def test_forbidden_models_exported_with_warning(tmp_path):
    """사용자 샘플 칸(EMPTY)·PITCH 모델은 내용 그대로 내보내고, 가져올 때 오류가 된다는 경고를 돌려준다."""
    data = dict(device.FakeDevice().read_all())
    mo = codec.decode(data[1], PRO)
    mo["effectModule"]["2"] = {"SWITCH": 1, "TYPE": 55, "Data": {p["name"]: 0 for p in catalog.params(2, 55, PRO)}}
    pitch = next(t for t in range(catalog.model_count(6)) if catalog.model(6, t)["name"] == "PITCH SHIFT")
    mo["effectModule"]["6"] = {"SWITCH": 1, "TYPE": pitch, "Data": {p["name"]: 0 for p in catalog.params(6, pitch, PRO)}}
    data[1] = codec.encode(mo, PRO)
    doc, warns, presets, errors = _doc_roundtrip(tmp_path, data, PRO)
    first = doc["presets"][0]["modules"]
    assert first["AMP"]["model"] == "EMPTY" and first["MOD"]["model"] == "PITCH SHIFT"
    assert any("01A" in w and "EMPTY" in w for w in warns) and any("01A" in w and "PITCH" in w for w in warns)
    assert any(e.slot == "01A" for e in errors)                           # 경고가 알린 대로 실제로 가져올 때 오류


def test_undecodable_slot_is_skipped_with_warning():
    data = dict(device.FakeDevice().read_all())
    data[9] = b"\xff" * 244
    warns = []
    doc = export.device_to_doc(data, PRO, "t", warnings=warns)
    assert len(doc["presets"]) == 199 and "03A" not in [p["slot"] for p in doc["presets"]]
    assert any("03A" in w and "해석" in w for w in warns)


# ---------------------------------------------------------------- 곡 목록
def test_parse_song_list():
    text = ("제목 줄은 무시\n\n01 검정치마 - Antifreeze\n"
            "22 Oasis - Don't Look Back in Anger  (Don't Look)\n"
            "41 캐논 (밴드 합주 편곡: 클린 → 크런치)\n"
            "42 Fly Me to the Moon (재즈 스탠더드: 클린)  (Fly Me To)\n"
            "46 등려군 - 월량대표아적심 (月亮代表我的心)  (Yueliang)\n"
            "47 범용 - Fender Deluxe/Twin 계열 (C·D는 가벼운 오버드라이브)\n"
            "- 38~40 크리스마스 곡: 안내 줄\n51 범위 밖 - 곡\nabc 12 - x\n")
    assert export.parse_song_list(text) == {
        1: ("검정치마", "Antifreeze"),
        22: ("Oasis", "Don't Look Back in Anger"),
        41: ("", "캐논 (밴드 합주 편곡: 클린 → 크런치)"),
        42: ("", "Fly Me to the Moon (재즈 스탠더드: 클린)"),
        46: ("등려군", "월량대표아적심 (月亮代表我的心)"),
        47: ("범용", "Fender Deluxe/Twin 계열 (C·D는 가벼운 오버드라이브)"),
    }


def test_sample_song_list_has_50_banks():
    songs = export.parse_song_list((Path(__file__).resolve().parents[1] / "samples" / "합주곡_목록.txt").read_text(encoding="utf-8"))
    assert sorted(songs) == list(range(1, 51)) and songs[1] == ("검정치마", "Antifreeze") and songs[41][0] == ""


# ---------------------------------------------------------------- 파일·cli
def test_write_doc_refuses_overwrite_and_dumps_valid_json(tmp_path, dev_data):
    doc = export.device_to_doc(dev_data, PRO, "t")
    doc["presets"][3]["exp"] = {k: 1 for k in codec.EXP_ORDER}
    assert json.loads(export.dumps(doc)) == doc
    path = export.write_doc(tmp_path / "a.json", doc)
    with pytest.raises(FileExistsError):
        export.write_doc(path, doc)
    export.write_doc(path, doc, overwrite=True)
    assert json.loads(path.read_text(encoding="utf-8")) == doc


def _make_backup(tmp_path, profile):
    dev = device.FakeDevice(profile=profile)
    models.set_active(profile)
    return backup.make_backup(dev.read_all(), tmp_path / "bk")


@pytest.mark.parametrize("profile", [PRO, GE])
def test_cli_export_json_from_backup(tmp_path, capsys, profile):
    folder = _make_backup(tmp_path, profile)
    models.set_active(PRO if profile is GE else GE)       # 활성 기종과 달라도 backup.json 의 device 를 따른다
    songs = tmp_path / "songs.txt"
    songs.write_text("01 가수 - 곡\n", encoding="utf-8")
    out = tmp_path / "x.json"
    cli.main(["export-json", str(out), "--from-backup", str(folder), "--songs", str(songs), "--title", "내 제목"])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["device"] == profile.key and doc["title"] == "내 제목" and len(doc["presets"]) == 200
    assert doc["presets"][0]["song"] == "곡" and "저장" in capsys.readouterr().out
    with pytest.raises(SystemExit) as ex:                 # 이미 있으면 덮어쓰지 않는다
        cli.main(["export-json", str(out), "--from-backup", str(folder)])
    assert "덮어쓰지" in str(ex.value) and json.loads(out.read_text(encoding="utf-8"))["title"] == "내 제목"


def test_cli_export_json_rejects_tampered_backup(tmp_path):
    folder = _make_backup(tmp_path, PRO)
    (folder / "device.bin").write_bytes(b"\0" * 10)
    with pytest.raises(SystemExit) as ex:
        cli.main(["export-json", str(tmp_path / "y.json"), "--from-backup", str(folder)])
    assert "백업 손상" in str(ex.value) and not (tmp_path / "y.json").exists()


def test_cli_export_json_never_opens_real_device_with_backup(tmp_path, monkeypatch):
    folder = _make_backup(tmp_path, PRO)
    monkeypatch.setattr(cli, "open_device", lambda fake: pytest.fail("백업에서 읽을 때는 장비를 열면 안 됩니다"))
    cli.main(["export-json", str(tmp_path / "z.json"), "--from-backup", str(folder)])
    assert (tmp_path / "z.json").exists()


def test_cli_export_json_fake_device(tmp_path):
    cli.main(["--fake", "export-json", str(tmp_path / "f.json")])
    assert json.loads((tmp_path / "f.json").read_text(encoding="utf-8"))["device"] == "GE150PRO"


# ---------------------------------------------------------------- 창
def test_window_export_json_button(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from test_app_smoke import wait
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk", fake_profile=GE)
    msgs = []
    win.info = msgs.append
    win.show()
    win.export_json()
    assert msgs[-1] == "먼저 장비를 읽어 주십시오."                                   # 장비를 읽기 전에는 저장하지 않는다
    win.connect_and_read()
    assert wait(app, win, lambda: len(win.dev_data) == 200 and not win.busy)
    assert win.btn_export_json.text() == "장비→톤 JSON 저장" and win.btn_export_json.isEnabled()
    win.pick_save_json = lambda default: ""
    win.export_json()                                                              # 취소하면 아무것도 쓰지 않는다
    assert len(msgs) == 1
    out = tmp_path / "w.json"
    win.pick_save_json = lambda default: str(out)
    win.export_json()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["device"] == "GE150" and len(doc["presets"]) == 200 and "song" not in doc["presets"][0]
    assert "200개" in msgs[-1] and str(out) in msgs[-1]
    win.close()
