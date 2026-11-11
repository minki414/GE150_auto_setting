"""H단계 시험: Pro 톤 JSON -> GE150 톤 JSON 변환(ge150/convert_model.py)과 cli to-ge150."""
import copy
import json
from collections import Counter

import pytest

import cli
from ge150 import convert_model, models, tonepack

GE = models.GE150


def _mods():
    return {
        "FX/COMP": {"on": True, "model": "YELLOW COMP", "params": {"ATTACK": 50, "THRES": 45, "RATIO": 30, "LEVEL": 75}},
        "DS/OD": {"on": True, "model": "TUBE DR", "params": {"VOLUME": 90, "TONE": 50, "GAIN": 15}},
        "AMP": {"on": True, "model": "65 US DX", "params": {"GAIN": 62, "BASS": 50, "MID": 50, "TREBLE": 55, "PRES": 50, "MST": 80}},
        "CAB": {"on": True, "model": "US DLX 112", "params": {"TUBE": "6V6", "MIC": "SM57", "CENTER": 30, "DISTANCE": 40}},
        "NS GATE": {"on": True, "model": "NOISE GATE", "params": {"ATTACK": 50, "RELEASE": 50, "THRES": 35}},
        "EQ": {"on": False, "model": "MOOER G", "params": {"100Hz": 0, "250Hz": 0, "630Hz": 0, "1.6KHz": 0, "4KHz": 0}},
        "MOD": {"on": True, "model": "ANA-CHORUS", "params": {"RATE": 30, "MIX": 40, "TONE": 50, "DEPTH": 45}},
        "DELAY": {"on": False, "model": "DIGITAL", "params": {"LEVEL": 20, "F.BACK": 25, "TIME": 380, "SUB-D": "OFF"}},
        "REVERB": {"on": True, "model": "ROOM", "params": {"P.DELAY": 20, "LEVEL": 25, "DECAY": 40, "TONE": 50}},
    }


def doc_with(name="Test CLN", slot="01A", **changes):
    """Pro 톤 JSON 1프리셋. changes = {모듈: 덮어쓸 항목}."""
    mods = _mods()
    for k, v in changes.items():
        mods[{"FX_COMP": "FX/COMP", "DS_OD": "DS/OD", "NS_GATE": "NS GATE"}.get(k, k)].update(v)
    return {"format": "GE150-TONEPACK", "device": "GE150PRO", "version": 1, "title": "t (GE150 Pro)",
            "presets": [{"slot": slot, "name": name, "song": "곡", "modules": mods}]}


def conv(doc):
    stats = Counter()
    new, notes = convert_model.to_ge150(doc, stats)
    return new, notes, stats


def mod_of(new, key, i=0):
    return new["presets"][i]["modules"][key]


def check_ge150(tmp_path, doc):
    p = tmp_path / "ge.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return tonepack.load_tonepacks([p], profile=GE)


# ---------------------------------------------------------------- 문서
def test_header_and_input_untouched():
    src = doc_with()
    before = copy.deepcopy(src)
    new, notes, _ = conv(src)
    assert src == before                                                    # 입력은 바꾸지 않는다
    assert (new["format"], new["device"], new["version"], new["title"]) == ("GE150-TONEPACK", "GE150", 1, "t (GE150)")
    assert new["presets"][0]["song"] == "곡" and notes == []
    nodev = doc_with()
    del nodev["device"]
    assert conv(nodev)[0]["device"] == "GE150"


# ---------------------------------------------------------------- 모델 대응
@pytest.mark.parametrize("old,new_model", [("S-COMP", "YELLOW COMP"), ("RED COMP", "BLUE COMP")])
def test_comp_sens_to_thres(old, new_model):
    new, notes, st = conv(doc_with(FX_COMP={"model": old, "params": {"SENS": 30, "LEVEL": 70}}))
    assert mod_of(new, "FX/COMP") == {"on": True, "model": new_model, "params": {"ATTACK": 50, "THRES": 70, "RATIO": 40, "LEVEL": 70}}
    assert st["%s → %s" % (old, new_model)] == 1 and notes[0].startswith("01A · FX/COMP · %s → %s" % (old, new_model))


def test_limit_to_yellow_comp():
    new, _, _ = conv(doc_with(FX_COMP={"model": "LIMIT", "params": {"THRES": 33, "LEVEL": 61}}))
    assert mod_of(new, "FX/COMP")["model"] == "YELLOW COMP"
    assert mod_of(new, "FX/COMP")["params"] == {"ATTACK": 30, "THRES": 33, "RATIO": 90, "LEVEL": 61}


def test_intel_reducer_comp_defaults_and_turned_off():
    new, notes, _ = conv(doc_with(FX_COMP={"model": "INTEL REDUCER", "on": True, "params": {"THRES": 1, "DEPTH": 2, "LEVEL": 3}}))
    assert mod_of(new, "FX/COMP") == {"on": False, "model": "YELLOW COMP", "params": {"ATTACK": 50, "THRES": 50, "RATIO": 50, "LEVEL": 50}}
    assert "대응 모델 없음" in notes[0]


def test_ns_gate_intel_reducer_is_left_alone():
    new, notes, _ = conv(doc_with(NS_GATE={"model": "INTEL REDUCER", "params": {"SENS": 40}}))
    assert mod_of(new, "NS GATE")["model"] == "INTEL REDUCER" and notes == []


@pytest.mark.parametrize("old,new_model", [
    ("VX YUBE OD", "808"), ("TIGHT METAL", "TIGHT DS"), ("THE JUICER", "FULL DRV"), ("RUMBLE DRIVE", "FULL DRV"), ("SOLO", "RIET"),
    ("BLUES MOOD", "JIMMY OD"), ("BLUES CRAB", "JIMMY OD"), ("HUSTLE DRIVE", "OBSESSIVE DIST")])
def test_ds_od_models_keep_knobs(tmp_path, old, new_model):
    doc = doc_with(DS_OD={"model": old, "params": {"VOLUME": 91, "TONE": 42, "GAIN": 17}})
    new, notes, st = conv(doc)
    assert mod_of(new, "DS/OD") == {"on": True, "model": new_model, "params": {"VOLUME": 91, "TONE": 42, "GAIN": 17}}
    assert st["%s → %s" % (old, new_model)] == 1 and len(notes) == 1
    assert check_ge150(tmp_path, new)[1] == []


@pytest.mark.parametrize("old,new_model", [("DUAL PHASER", "PHASER"), ("MODERN PHASER", "PHASER"), ("MODERN ROTARY", "ROTARY")])
def test_mod_models_keep_knobs(tmp_path, old, new_model):
    knobs = {"RATE": 1, "LEVEL": 2, "DEPTH": 3} if "PHASER" in old else {"RATE": 1, "MIX": 2, "TONE": 3}
    new, _, _ = conv(doc_with(MOD={"model": old, "params": knobs}))
    assert mod_of(new, "MOD") == {"on": True, "model": new_model, "params": knobs}
    assert check_ge150(tmp_path, new)[1] == []


def test_flanger_pro_drops_delay():
    new, _, _ = conv(doc_with(MOD={"model": "FLANGER PRO", "params": {"RATE": 10, "MIX": 20, "FEEDBACK": 30, "DELAY": 40}}))
    assert mod_of(new, "MOD") == {"on": True, "model": "FLANGER", "params": {"RATE": 10, "MIX": 20, "FEEDBACK": 30}}
    new, _, _ = conv(doc_with(MOD={"model": "FLANGER PRO", "params": {"RATE": 10, "MIX": 20, "F.BACK": 31, "DELAY": 40}}))   # 별칭으로 적어도
    assert mod_of(new, "MOD")["params"]["FEEDBACK"] == 31


def test_optical_tremolo_knob_mapping():
    new, _, _ = conv(doc_with(MOD={"model": "OPTICAL TRMOLO", "params": {"SPEED": 11, "INTENSITY": 22, "TONE": 33}}))
    assert mod_of(new, "MOD") == {"on": True, "model": "TREMOLO", "params": {"RATE": 11, "MIX": 22, "TONE": 33}}


def test_octave_defaults_and_turned_off():
    new, notes, _ = conv(doc_with(MOD={"model": "OCTAVE", "params": {"SUB": 1, "SUB TONE": 2, "UPPER": 3, "UPPER TONE": 4, "DRY": 5}}))
    assert mod_of(new, "MOD") == {"on": False, "model": "ANA-CHORUS", "params": {"RATE": 50, "MIX": 50, "TONE": 50, "DEPTH": 50}}
    assert "대응 모델 없음" in notes[0]


def test_shimmer_to_hall():
    new, _, _ = conv(doc_with(REVERB={"model": "SHIMMER", "params": {"P.DELAY": 33, "LEVEL": 22, "DECAY": 54, "TONE": 58, "SHIMMER": 70}}))
    assert mod_of(new, "REVERB") == {"on": True, "model": "HALL", "params": {"PRE DELAY": 33, "LEVEL": 22, "DECAY": 54, "TONE": 58}}


def test_knob_names_renamed_everywhere_and_counted(tmp_path):
    new, notes, st = conv(doc_with())
    assert list(mod_of(new, "DELAY")["params"]) == ["LEVEL", "FEEDBACK", "TIME", "SUB-D"]
    assert list(mod_of(new, "REVERB")["params"]) == ["PRE DELAY", "LEVEL", "DECAY", "TONE"]
    assert st["노브 이름 F.BACK → FEEDBACK"] == 1 and st["노브 이름 P.DELAY → PRE DELAY"] == 1 and notes == []
    assert check_ge150(tmp_path, new)[1] == []


def test_off_modules_are_converted_too_and_stay_off(tmp_path):
    new, notes, _ = conv(doc_with(DS_OD={"on": False, "model": "SOLO", "params": {"VOLUME": 1, "TONE": 2, "GAIN": 3}},
                                  REVERB={"on": False, "model": "SHIMMER", "params": {"P.DELAY": 1, "LEVEL": 2, "DECAY": 3, "TONE": 4, "SHIMMER": 5}}))
    assert mod_of(new, "DS/OD")["model"] == "RIET" and mod_of(new, "DS/OD")["on"] is False
    assert mod_of(new, "REVERB")["model"] == "HALL" and len(notes) == 2
    assert check_ge150(tmp_path, new)[1] == []


# ---------------------------------------------------------------- 오류 메모
def test_user_sample_slots_not_converted():
    new, notes, st = conv(doc_with(AMP={"model": "EMPTY", "params": {}}, CAB={"model": "EMPTY", "params": {}}))
    assert mod_of(new, "AMP")["model"] == "EMPTY" and mod_of(new, "CAB")["model"] == "EMPTY"
    assert sum("오류: 변환 불가" in n for n in notes) == 2 and st["오류: 변환 불가"] == 2


def test_unknown_pro_only_model_gets_error_note_and_stays(tmp_path):
    new, notes, st = conv(doc_with(DS_OD={"model": "FUTURE DRIVE", "params": {"VOLUME": 1, "TONE": 2, "GAIN": 3}}))
    assert mod_of(new, "DS/OD")["model"] == "FUTURE DRIVE" and st["오류: 대응표 없음"] == 1
    assert "오류: 대응표 없음" in notes[0] and "01A · DS/OD" in notes[0]
    assert check_ge150(tmp_path, new)[1]                                   # 그대로 둔 것은 GE150 검사에서 오류로 드러난다


def test_non_integer_knob_value_is_an_error_note_not_a_crash():
    new, notes, _ = conv(doc_with(FX_COMP={"model": "S-COMP", "params": {"SENS": "높음", "LEVEL": 70}}))
    assert mod_of(new, "FX/COMP")["model"] == "S-COMP" and "오류: 변환 불가" in notes[0]


# ---------------------------------------------------------------- 이름 14자
@pytest.mark.parametrize("name,fitted", [
    ("GoBack CLN", "GoBack CLN"),                          # 14자 이하는 그대로(SOLO 도 건드리지 않음)
    ("Antifreeze SOLO", "Antifreeze SOL"),                 # ① SOLO -> SOL 로 충분
    ("Fall For Me CLN", "FallForMe CLN"),                  # ② 역할 앞부분 공백 제거
    ("Fender Twin SOLO", "FenderTwin SOL"),                # ① 다음 ②
    ("Very Long Song Name DRV", "VeryLongSo DRV"),             # ③ 앞부분을 잘라 14자(역할은 살림)
    ("Abcdefghijklmnopq", "Abcdefghijklmn"),               # 단어 하나뿐이면 그냥 14자
])
def test_name_rules(name, fitted):
    new, notes, st = conv(doc_with(name=name))
    assert new["presets"][0]["name"] == fitted and len(fitted) <= 14
    assert (len(notes), st["이름 줄임"]) == ((0, 0) if name == fitted else (1, 1))
    if name != fitted:
        assert notes[0] == "01A · 이름 · %s → %s" % (name, fitted)


def test_converted_pro_doc_passes_ge150_check(tmp_path):
    doc = doc_with(name="Fender Twin SOLO",
                   FX_COMP={"model": "S-COMP", "params": {"SENS": 30, "LEVEL": 70}},
                   DS_OD={"model": "BLUES CRAB", "params": {"VOLUME": 80, "TONE": 50, "GAIN": 20}},
                   MOD={"model": "OPTICAL TRMOLO", "params": {"SPEED": 11, "INTENSITY": 22, "TONE": 33}},
                   REVERB={"model": "SHIMMER", "params": {"P.DELAY": 33, "LEVEL": 22, "DECAY": 54, "TONE": 58, "SHIMMER": 70}})
    before = check_ge150(tmp_path, doc)[1]
    assert len(before) >= 4                                                # 변환 전에는 GE150 에서 오류
    _, errors, warnings = check_ge150(tmp_path, conv(doc)[0])
    assert errors == [] and warnings == []


# ---------------------------------------------------------------- cli
def test_cli_to_ge150(tmp_path, capsys):
    src = tmp_path / "pro.json"
    src.write_text(json.dumps(doc_with(name="Fall For Me SOLO", MOD={"model": "OCTAVE", "params": {}}), ensure_ascii=False), encoding="utf-8")
    out, notes = tmp_path / "ge.json", tmp_path / "notes.txt"
    with pytest.raises(SystemExit) as ex:                                  # 성공도 sys.exit(0)
        cli.main(["to-ge150", str(src), "--out", str(out), "--notes", str(notes)])
    assert ex.value.code == 0
    printed, text = capsys.readouterr().out, notes.read_text(encoding="utf-8")
    assert printed == text and "OCTAVE → ANA-CHORUS: 1건" in text and "오류 0건" in text and "이름 줄임: 1건" in text
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["device"] == "GE150" and doc["presets"][0]["name"] == "FallForMe SOL"
    for extra in (["--out", str(out)], ["--out", str(tmp_path / "ge2.json"), "--notes", str(notes)]):   # 어느 쪽도 덮어쓰지 않는다
        with pytest.raises(SystemExit) as ex:
            cli.main(["to-ge150", str(src)] + extra)
        assert "덮어쓰지" in str(ex.value)
    assert not (tmp_path / "ge2.json").exists()


def test_cli_to_ge150_exit_1_when_result_has_errors(tmp_path):
    src = tmp_path / "pro.json"
    src.write_text(json.dumps(doc_with(AMP={"model": "EMPTY", "params": {}}), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit) as ex:
        cli.main(["to-ge150", str(src), "--out", str(tmp_path / "ge.json")])
    assert ex.value.code == 1 and (tmp_path / "ge.json").exists()
