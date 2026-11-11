import copy
import json

from ge150 import catalog, codec, mofile


def _write(tmp_path, name, mo):
    p = tmp_path / name
    p.write_text(json.dumps(mo, ensure_ascii=False), encoding="utf-8")
    return p


def _mo(dev_data, slot=1):
    return codec.decode(dev_data[slot])


def test_slot_from_filename():
    f = mofile.slot_from_filename
    assert f("01A x.mo") == 1 and f("13a Buzz.mo") == 49 and f("50d.mo") == 200
    assert f("Dual Lead.mo") is None and f("51A x.mo") is None and f("1A x.mo") is None


def test_scan_duplicate_slot_and_unassigned(tmp_path, dev_data):
    _write(tmp_path, "01A a.mo", _mo(dev_data))
    _write(tmp_path, "01a b.mo", _mo(dev_data, 2))
    _write(tmp_path, "Dual.mo", _mo(dev_data, 3))
    _write(tmp_path, "02B ok.mo", _mo(dev_data, 4))
    (tmp_path / "03A bad.mo").write_text("{not json", encoding="utf-8")
    es = {e.path.name: e for e in mofile.scan_folder(tmp_path)}
    assert es["01A a.mo"].errors and es["01a b.mo"].errors
    assert es["Dual.mo"].slot is None and not es["Dual.mo"].errors
    assert es["02B ok.mo"].slot == 6 and not es["02B ok.mo"].errors
    assert any("JSON" in x for x in es["03A bad.mo"].errors)


def test_name_validation(dev_data):
    mo = _mo(dev_data)
    mo["fileInfo"]["preset_name"] = "A" * 17
    assert any("16자" in e for e in mofile.validate_mo(mo))
    mo["fileInfo"]["preset_name"] = "한글이름"
    assert any("문자" in e for e in mofile.validate_mo(mo))
    mo["fileInfo"]["preset_name"] = "Ab 1-_.'"
    assert mofile.validate_mo(mo) == []


def test_range_errors(dev_data):
    mo = _mo(dev_data)
    bad = copy.deepcopy(mo)
    bad["effectModule"]["5"]["Data"]["100Hz"] = 33
    assert any("100Hz" in e for e in mofile.validate_mo(bad))
    bad = copy.deepcopy(mo)
    bad["effectModule"]["2"]["TYPE"] = 75
    assert any("모델 번호" in e for e in mofile.validate_mo(bad))
    bad = copy.deepcopy(mo)
    del bad["effectModule"]["1"]["Data"]["TONE"]
    assert any("TONE" in e for e in mofile.validate_mo(bad))


def test_sample_warning(dev_data):
    mo = _mo(dev_data)
    assert mofile.sample_warnings(mo) == []
    mo["effectModule"]["2"]["TYPE"] = 60
    mo["effectModule"]["2"]["Data"] = {p["name"]: 0 for p in catalog.params(2, 60)}
    mo["effectModule"]["3"]["TYPE"] = 30
    w = mofile.sample_warnings(mo)
    assert len(w) == 2 and "AMP" in w[0] and "CAB" in w[1]
    w2 = mofile.sample_warnings(mo, ["EMPTY"] * 40)
    assert all("비어" in x for x in w2)


def test_write_mo_format_and_roundtrip(tmp_path, dev_data):
    mo = _mo(dev_data)
    p = mofile.write_mo(tmp_path, 1, mo)
    assert p.name == "01A %s.mo" % mo["fileInfo"]["preset_name"]   # D3: 파일 이름은 항상 '<슬롯> <이름>.mo'
    text = p.read_text(encoding="utf-8")
    assert text.endswith("}\n") and text == mofile.dumps_mo(json.loads(text)) and '\n    "Exp"' in text
    assert mofile.read_mo(p).mo == mo
    assert mofile.mo_filename(2, "a/b:c") == "01B a_b_c.mo"
    assert mofile.mo_filename(2, "01b Same") == "01B 01b Same.mo" and mofile.mo_filename(2, "") == "01B_.mo"


def test_official_files_valid(mo_files):
    for s, (p, j) in mo_files.items():
        assert mofile.validate_mo(j) == [], p.name
