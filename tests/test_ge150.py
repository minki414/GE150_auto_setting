"""GE150(일반) 지원 시험 — 실장비 없이 FakeDevice·메모리 HID 대역으로만 한다."""
import dataclasses
import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ge150 import backup, catalog, codec, device, mofile, models, plan, tonepack
from ge150 import protocol as P
from test_audit_a import MemHid
from test_tonepack import NOW, example, pack, preset, write_json

GE, PRO = models.GE150, models.PRO


def u16(b, i):
    return int.from_bytes(b[i:i + 2], "little")


def ge_mo(sw=1, ty=2):
    """GE150 용 .mo: 모든 모듈 SWITCH=sw, TYPE=ty, 노브 k 는 10*모듈+k+1, EXP 는 11..16."""
    mods = {str(i): {"SWITCH": sw, "TYPE": ty,
                     "Data": {p["name"]: 10 * i + k + 1 for k, p in enumerate(catalog.params(i, ty, GE))}} for i in range(9)}
    exp = {k: 11 + j for j, k in enumerate(codec.EXP_ORDER)}
    return {"Exp": exp, "effectModule": mods, "fileInfo": dict(GE.file_info, preset_name="Layout Test 14")}


# ---------------------------------------------------------------- 기종 표
def test_profile_layout_numbers():
    assert PRO.exp_offset == 232 and PRO.module_offsets[:2] == (16, 40) and PRO.exp_offset + 12 == PRO.write_size == 244
    assert [(o - GE.name_len) // 2 for o in GE.module_offsets] == [0, 6, 11, 19, 26, 31, 42, 51, 60]
    assert (GE.exp_offset - GE.name_len) // 2 == 66 and GE.exp_offset + 12 == 158 and GE.read_size == 162
    assert models.active() is PRO


def test_profile_lookup_and_bad_layout():
    assert models.by_key("ge150") is GE and models.by_key("GE150PRO") is PRO and models.by_key("x") is None and models.by_key(None) is None
    assert models.from_read_size(244) is PRO and models.from_read_size(162) is GE and models.from_read_size(158) is None
    assert models.from_file_info({"device": "MOOER GE150"}) is GE and models.from_file_info({"device": "GE150PRO"}) is PRO
    assert models.from_file_info({"device": "?"}) is None and models.from_file_info({}) is None and models.from_file_info(None) is None
    with pytest.raises(ValueError):
        dataclasses.replace(GE, write_size=160)
    assert models.param_alias("F.BACK") == "FEEDBACK" and models.param_alias("pre delay") == "P.DELAY" and models.param_alias("TIME") is None


def test_using_restores_active():
    with models.using(GE):
        assert models.active() is GE
    assert models.active() is PRO


# ---------------------------------------------------------------- 코덱
def test_ge150_encode_layout():
    data = codec.encode(ge_mo(), GE)
    assert len(data) == 158
    for i, pos in enumerate([0, 6, 11, 19, 26, 31, 42, 51, 60]):
        assert u16(data, 14 + 2 * pos) == 1 and u16(data, 14 + 2 * pos + 2) == 2, i   # SW, TYPE = SW+1 위치
    assert [u16(data, 14 + 2 * (66 + j)) for j in range(6)] == [11, 12, 13, 14, 15, 16]
    assert data[:14] == b"Layout Test 14"
    assert u16(data, 14 + 2 * 2) == 1                       # FX 모델 2 의 P0 (= 모듈 0 의 첫 노브 값)


def test_ge150_roundtrip_and_162_input():
    mo = ge_mo()
    data = codec.encode(mo, GE)
    assert codec.decode(data, GE) == mo
    assert codec.decode(data + b"\x00" * 4, GE) == mo      # 읽기 응답(162)도 앞 158 만 해석
    assert codec.same_preset(data, data + b"\xff\xff\xff\xff", GE)
    with pytest.raises(codec.CodecError, match="158 또는 162바이트"):
        codec.decode(b"\x00" * 244, GE)


def test_ge150_default_name_is_14_and_longer_is_error():
    mo = ge_mo()
    mo["fileInfo"]["preset_name"] = "A" * 15
    with pytest.raises(codec.CodecError, match="14자"):
        codec.encode(mo, GE)


def test_param_count_over_cells_is_error():
    small = dataclasses.replace(GE, module_words=(5,) + GE.module_words[1:], write_size=156, read_size=160)
    with pytest.raises(codec.CodecError, match="칸 수"):
        codec.decode(b"\x00" * 160, small)


def test_pro_codec_unchanged_by_profile_arg(dev_data):
    d = codec.decode(dev_data[1])
    assert codec.decode(dev_data[1], PRO) == d and codec.encode(d, PRO) == codec.encode(d) and len(codec.encode(d)) == 244


# ---------------------------------------------------------------- 쓰기 프레임
def test_write_body_and_frame_len():
    with models.using(GE):
        body = P.write_body(1, b"\x07" * 158)
        assert body[:2] == b"\xc3\x01" and len(body) == 160 and P.build_frame(body)[2:4] == b"\xa0\x00"
        with pytest.raises(ValueError, match="158"):
            P.write_body(1, b"\x00" * 162)
    body = P.write_body(1, b"\x07" * 244)
    assert len(body) == 246 and P.build_frame(body)[2:4] == b"\xf6\x00"
    with pytest.raises(ValueError, match="244"):
        P.write_body(1, b"\x00" * 158)


# ---------------------------------------------------------------- 기종 판별·연결
def _preset_data(n, count=200):
    return {s: bytes([s % 251]) * n for s in range(1, count + 1)}


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(device, "WRITE_ACK_TIMEOUT", 0.15)
    monkeypatch.setattr(device, "READ_TIMEOUT", 0.6)


def _dev(hid):
    d = device.Device()
    d.h = hid
    return d


@pytest.mark.parametrize("size,prof", [(244, PRO), (162, GE)])
def test_read_all_detects_model_by_size(fast, size, prof):
    data = _preset_data(size)
    d = _dev(MemHid(preset_data=data))
    assert d.profile is None and d.read_all() == data and d.profile is prof


def _inject_first(h, body):
    """read_all 요청(A0 01)을 받으면 응답 맨 앞에 프레임 하나를 더 끼워 넣는다(같은 슬롯 중복·혼합 재현)."""
    orig = h.write

    def write(rep):
        n = orig(rep)
        if h.frames and h.frames[-1] == P.CMD_READ_ALL and not getattr(h, "_done", False):
            h._done = True
            h.queue.extendleft(reversed([list(r[1:]) for r in P.frame_to_reports(P.build_frame(body))]))
        return n

    h.write = write


def test_read_all_mixed_sizes_is_error(fast):
    data = _preset_data(162)
    data[7] = b"\x01" * 244
    d = _dev(MemHid(preset_data=data))
    with pytest.raises(device.DeviceError, match="섞여"):
        d.read_all()
    assert d.profile is None


def test_read_all_same_slot_with_other_size_is_error(fast):
    """같은 슬롯에 244 바이트 응답이 먼저 오고 162 바이트 응답이 뒤따름 — 예전에는 덮어써서 한 기종으로 판정했다."""
    h = MemHid(preset_data=_preset_data(162))
    _inject_first(h, bytes([P.RSP_PRESET, 5]) + b"\x01" * 244)
    d = _dev(h)
    with pytest.raises(device.DeviceError, match="섞여"):
        d.read_all()
    assert d.profile is None


def test_read_all_unknown_size_is_error(fast):
    d = _dev(MemHid(preset_data=_preset_data(200)))
    with pytest.raises(device.DeviceError, match="알 수 없는 프리셋 크기"):
        d.read_all()
    assert d.profile is None


def test_read_all_slot_zero_is_error(fast):
    data = _preset_data(162, 199)
    data[0] = b"\x02" * 162                      # 슬롯 0~199 (200개지만 1~200 이 아님)
    with pytest.raises(device.DeviceError, match="범위 밖"):
        _dev(MemHid(preset_data=data)).read_all()


def test_read_all_conflicting_duplicate_slot_is_error(fast):
    h = MemHid(preset_data=_preset_data(162))
    _inject_first(h, bytes([P.RSP_PRESET, 5]) + b"\x77" * 162)   # 슬롯 5 가 뒤따르는 내용과 다름
    with pytest.raises(device.DeviceError, match="서로 다릅니다"):
        _dev(h).read_all()


def test_read_all_identical_duplicate_is_tolerated(fast):
    data = _preset_data(162)
    h = MemHid(preset_data=data)
    _inject_first(h, bytes([P.RSP_PRESET, 5]) + data[5])
    d = _dev(h)
    assert d.read_all() == data and d.profile is GE


def test_write_refused_until_profile_is_known(fast):
    h = MemHid(preset_data=_preset_data(244))
    d = device.Device()
    d.h = h
    with pytest.raises(device.DeviceError, match="기종"):
        d.write_slot(1, b"\x00" * 244)             # 연결만 된 상태(읽기 전)
    assert h.frames == []                          # 아무 프레임도 나가지 않았다
    d.read_all()
    assert d.write_slot(1, b"\x00" * 244) is True
    mixed = _preset_data(244)
    mixed[3] = b"\x00" * 162
    h2 = MemHid(preset_data=mixed)
    d2 = device.Device()
    d2.h = h2
    d2.profile = PRO                               # 이전 읽기에서 정해졌던 기종
    with pytest.raises(device.DeviceError):
        d2.read_all()
    assert d2.profile is None                      # 읽기 실패 후에는 이전 기종을 남기지 않는다
    with pytest.raises(device.DeviceError, match="기종"):
        d2.write_slot(1, b"\x00" * 244)
    d.close()
    assert d.profile is None
    with pytest.raises(device.DeviceError, match="기종"):
        d.write_slot(1, b"\x00" * 244)


def test_connect_resets_profile(monkeypatch):
    import sys
    import types
    d = device.Device()
    d.profile = PRO
    fake_hid = types.SimpleNamespace(enumerate=lambda vid, pid: [])
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    with pytest.raises(device.DeviceError, match="찾을 수 없"):
        d.connect()
    assert d.profile is None


def test_ge150_write_frames_no_prewrite_and_trimmed(fast):
    h = MemHid(preset_data=_preset_data(162))
    d = _dev(h)
    d.read_all()
    assert d.write_slot(5, b"\x09" * 162) is True                 # 백업 원본(162)을 줘도 158 만 나간다
    assert h.c3_count == 1 and len(h.frames) == 2                  # A0 01(읽기) + C3 (B4 05 00 없음)
    assert h.frames[1][:2] == b"\xc3\x05" and len(h.frames[1]) == 160 and h.frames[1][2:] == b"\x09" * 158


def test_pro_write_frames_still_have_prewrite(fast):
    h = MemHid(preset_data=_preset_data(244))
    d = _dev(h)
    d.read_all()
    assert d.write_slot(5, b"\x09" * 244) is True
    assert h.frames[1] == P.CMD_PRE_WRITE and len(h.frames[2]) == 246


def test_user_samples_none_for_ge150(fast):
    h = MemHid(preset_data=_preset_data(162))
    d = _dev(h)
    d.read_all()
    n = len(h.frames)
    assert d.user_samples() is None and len(h.frames) == n          # 명령도 보내지 않는다
    assert device.FakeDevice(profile=GE).user_samples() is None and len(device.FakeDevice().user_samples()) == 40


def test_pick_interface():
    """감사 G 2회: 인터페이스가 둘 이상이면 번호·usage_page 와 무관하게 고르지 않고 거부한다."""
    pick = device.pick_interface
    pro = {"interface_number": 5, "path": b"hid#vid_0483&pid_5703&mi_05#7&aaa&0&0000#{x}"}
    assert pick([pro])["path"] == pro["path"]                       # 하나뿐 -> 그것
    ge = {"interface_number": 0, "path": b"hid#vid_0483&pid_5750#6&bbb&0&0000#{x}", "usage_page": 0xFF00}
    assert pick([ge])["path"] == ge["path"]
    # 한 인터페이스의 컬렉션 여러 개는 장비 1대 — 제조사 전용 usage_page 를 고른다
    c1 = {"interface_number": 5, "path": b"hid#vid_0483&pid_5703&mi_05&col01#7&aaa&0&0000#{x}", "usage_page": 1}
    c2 = {"interface_number": 5, "path": b"hid#vid_0483&pid_5703&mi_05&Col02#7&aaa&0&0001#{x}", "usage_page": 0xFF00}
    assert pick([c1, c2])["usage_page"] == 0xFF00 and pick([c2, c1])["usage_page"] == 0xFF00
    # 서로 다른 인터페이스(장비 여러 대 또는 구분 불가)는 어떤 순서·조합이든 거부: Pro(5번)+GE150(0번) 포함
    pro2 = dict(pro, path=pro["path"].replace(b"aaa", b"ccc"))
    for infos in ([pro, pro2], [pro2, pro], [pro, ge], [ge, pro], [ge, dict(ge, path=ge["path"].replace(b"bbb", b"ddd"))]):
        with pytest.raises(device.DeviceError, match="여러 대 연결되어 있습니다. 한 대만 연결해 주십시오."):
            pick(infos)
    with pytest.raises(device.DeviceError, match="찾을 수 없"):
        pick([])
    assert device.PIDS == (0x5703, 0x5750)


# ---------------------------------------------------------------- FakeDevice(GE150)
def test_fake_ge150_data():
    dev = device.FakeDevice(profile=GE)
    data = dev.read_all()
    assert dev.profile is GE and device.FakeDevice().profile is PRO
    assert len(data) == 200 and all(len(v) == 162 and v[158:] == b"\x00" * 4 for v in data.values())
    assert codec.decode_name(data[1], GE) == "GE150 Test 001" and codec.decode_name(data[200], GE) == "GE150 Test 200"
    mo = codec.decode(data[3], GE)
    assert mo["fileInfo"]["device"] == "MOOER GE150" and all(m["TYPE"] == 0 and m["SWITCH"] == 0 for m in mo["effectModule"].values())


def test_fake_ge150_write_keeps_tail_and_rejects_bad_size():
    dev = device.FakeDevice(profile=GE)
    assert dev.write_slot(2, b"\x05" * 158) and dev.slots[2] == b"\x05" * 158 + b"\x00" * 4
    with pytest.raises(ValueError):
        dev.write_slot(2, b"\x05" * 244)


# ---------------------------------------------------------------- tonepack
def _load(tmp_path, doc, profile):
    return tonepack.load_tonepacks([write_json(tmp_path, doc)], profile=profile)


def _set(p, mod, **kw):
    p["modules"][mod].update(kw)
    return p


def test_tonepack_pro_example_passes_on_ge150_with_alias(tmp_path):
    presets, errors, warnings = _load(tmp_path, pack(preset()), GE)       # 예시는 F.BACK·P.DELAY 로 적혀 있다
    assert errors == [] and len(presets) == 1
    m = presets[0]["mo"]["effectModule"]
    assert set(m["7"]["Data"]) == {"LEVEL", "FEEDBACK", "TIME", "SUB-D"} and set(m["8"]["Data"]) == {"PRE DELAY", "LEVEL", "DECAY", "TONE"}
    assert m["8"]["Data"]["PRE DELAY"] == 20 and presets[0]["mo"]["fileInfo"]["device"] == "MOOER GE150"


def test_tonepack_ge150_names_work_on_pro_too(tmp_path):
    p = preset()
    p["modules"]["DELAY"]["params"] = {"LEVEL": 20, "FEEDBACK": 25, "TIME": 380, "SUB-D": "OFF"}
    p["modules"]["REVERB"]["params"] = {"PRE DELAY": 20, "LEVEL": 25, "DECAY": 40, "TONE": 50}
    presets, errors, _ = _load(tmp_path, pack(p), PRO)
    assert errors == [] and set(presets[0]["mo"]["effectModule"]["7"]["Data"]) == {"LEVEL", "F.BACK", "TIME", "SUB-D"}


def test_tonepack_alias_given_twice_is_error(tmp_path):
    p = preset()
    p["modules"]["DELAY"]["params"] = {"LEVEL": 20, "F.BACK": 25, "FEEDBACK": 25, "TIME": 380, "SUB-D": "OFF"}
    assert any("두 번" in e.reason for e in _load(tmp_path, pack(p), GE)[1])


@pytest.mark.parametrize("mod,model", [("REVERB", "SHIMMER"), ("DS/OD", "TIGHT METAL")])
def test_tonepack_pro_only_model_is_error_on_ge150(tmp_path, mod, model):
    p = preset()
    if mod == "REVERB":
        p["modules"][mod] = {"on": True, "model": model, "params": {"P.DELAY": 20, "LEVEL": 25, "DECAY": 40, "TONE": 50, "SHIMMER": 50}}
    else:
        p["modules"][mod]["model"] = model
    _, errors, _ = _load(tmp_path, pack(p), GE)
    assert any(model in e.reason and e.module == mod for e in errors)
    assert _load(tmp_path, pack(p), PRO)[1] == []                                   # Pro 에서는 통과


def test_tonepack_name_length_follows_target(tmp_path):
    p15 = preset(name="A" * 15)
    assert any("14자" in e.reason for e in _load(tmp_path, pack(p15), GE)[1])
    assert _load(tmp_path, pack(p15), PRO)[1] == []
    assert _load(tmp_path, pack(preset(name="A" * 14)), GE)[1] == []


def test_tonepack_device_key_rules(tmp_path):
    d = pack(preset())
    d["format"], d["device"] = "GE150-TONEPACK", "GE150PRO"
    assert _load(tmp_path, d, PRO)[2] == []                                          # 같으면 경고 없음
    w = _load(tmp_path, d, GE)[2]
    assert len(w) == 1 and "GE150 Pro" in w[0].reason and "GE150 입니다" in w[0].reason   # 다르면 경고 1건
    d["device"] = "MOOER"
    assert any("device" in e.reason for e in _load(tmp_path, d, GE)[1])
    d["device"] = None
    assert any("device" in e.reason for e in _load(tmp_path, d, GE)[1])


def test_tonepack_both_format_names_accepted(tmp_path):
    for fmt in ("GE150-TONEPACK", "GE150PRO-TONEPACK"):
        d = pack(preset())
        d["format"] = fmt
        assert _load(tmp_path, d, PRO)[1] == []
    d["format"] = "GE150-TONEPAK"
    assert any("GE150-TONEPACK" in e.reason for e in _load(tmp_path, d, PRO)[1])


def test_tonepack_convert_ge150_outputs(tmp_path):
    res = tonepack.convert([write_json(tmp_path, pack(preset()))], tmp_path / "o", now=NOW, profile=GE)
    assert res.ok, [e.text() for e in res.errors]
    mo = json.loads(res.files[0].read_text(encoding="utf-8"))
    assert mo["fileInfo"]["device"] == "MOOER GE150" and mo["fileInfo"]["schema"] == "GE150 Preset" and mo["fileInfo"]["app_version"] == "V1.3.0"
    assert json.loads((res.out_dir / tonepack.META_NAME).read_text(encoding="utf-8"))["device"] == "GE150"
    assert models.active() is PRO                                                    # 변환이 끝나면 활성 기종은 그대로
    with models.using(GE):
        assert mofile.read_mo(res.files[0]).errors == []                             # 만든 .mo 는 GE150 에서 읽힌다
        assert len(codec.encode(mo)) == 158


def test_tonepack_peek_device(tmp_path):
    d = pack(preset())
    assert tonepack.peek_device([write_json(tmp_path, d)]) is None
    d["device"] = "GE150"
    assert tonepack.peek_device([write_json(tmp_path, d, "b.json")]) is GE
    assert tonepack.peek_device([tmp_path / "missing.json"]) is None and tonepack.peek_device([]) is None


# ---------------------------------------------------------------- mofile
def test_mo_of_other_model_is_rejected(dev_data):
    mo = codec.decode(dev_data[1])                                                   # Pro 기준으로 읽은 .mo (device GE150PRO)
    assert mofile.validate_mo(mo) == []
    with models.using(GE):
        errs = mofile.validate_mo(mo)
        assert len(errs) == 1 and "다른 기종(GE150 Pro)용" in errs[0]
        mo["fileInfo"].pop("device")
        assert not any("다른 기종" in e for e in mofile.validate_mo(mo))              # device 없으면 통과
        mo["fileInfo"]["device"] = "SOMETHING"
        assert not any("다른 기종" in e for e in mofile.validate_mo(mo))
    ge = ge_mo()
    assert any("다른 기종(GE150)용" in e for e in mofile.validate_mo(ge))             # 반대 방향(활성 Pro)


def test_mofile_name_limit_follows_active():
    assert mofile.validate_name("A" * 15) == []
    with models.using(GE):
        assert mofile.validate_name("A" * 15) == ["이름이 14자를 넘습니다."]


def test_sample_warnings_per_model(dev_data):
    mo = codec.decode(dev_data[1])
    mo["effectModule"]["3"]["TYPE"] = 26
    mo["effectModule"]["2"]["TYPE"] = 55
    assert len(mofile.sample_warnings(mo)) == 2
    with models.using(GE):
        g = ge_mo(ty=26)                                                              # CAB 26 만 사용자 샘플(AMP 는 55 개라 26 은 일반 모델)
        w = mofile.sample_warnings(g, ["EMPTY"] * 40)
        assert w == ["CAB(IR) 사용자 샘플 칸 사용(1번)"]                               # 이름 목록은 쓰지 않으므로 '비어 있음' 문구 없음
        assert mofile.sample_warnings(ge_mo(ty=2)) == []


# ---------------------------------------------------------------- 흐름: 백업 -> 적용 -> 재읽기 대조 -> 복원
def _ge_tone_folder(tmp_path):
    res = tonepack.convert([write_json(tmp_path, pack(preset(slot="01A"), preset(slot="01B", name="Second Tone")))],
                           tmp_path / "tone", now=NOW, profile=GE)
    assert res.ok
    return res.out_dir


def test_ge150_flow_backup_apply_verify_restore(tmp_path):
    models.set_active(GE)
    dev = device.FakeDevice(profile=GE)
    original = dict(dev.read_all())
    rows, _ = plan.build_plan(dev.read_all(), _ge_tone_folder(tmp_path))
    assert [r.status for r in rows[:2]] == [plan.ST_CHANGE] * 2 and all(r.errors == [] for r in rows[:2])
    res = plan.apply_plan(dev, rows, [1, 2], tmp_path / "bk")
    assert res.success and res.ok == [1, 2] and dev.written == [1, 2], res.text()
    assert dev.slots[1][:158] == codec.encode(rows[0].entry.mo, GE) and dev.slots[1][158:] == b"\x00" * 4
    assert dev.slots[3] == original[3]
    after = dev.read_all()
    assert [r.status for r in plan.build_plan(after, tmp_path / "tone")[0][:2]] == [plan.ST_SAME] * 2

    bk = next((tmp_path / "bk").iterdir())
    proof = json.loads((bk / "backup.json").read_text(encoding="utf-8"))
    assert proof["device"] == "GE150" and proof["preset_size"] == 162 and len((bk / "device.bin").read_bytes()) == 200 * 162
    rows2, _ = plan.build_plan(after, bk)
    assert [r.slot for r in rows2 if r.status == plan.ST_RESTORE] == [1, 2]
    res2 = plan.apply_plan(dev, rows2, [1, 2], tmp_path / "bk2")
    assert res2.success and dev.slots == original                                    # 원래대로(뒤 4바이트까지)


def test_ge150_restore_ignores_difference_in_tail_bytes(tmp_path):
    models.set_active(GE)
    dev = device.FakeDevice(profile=GE)
    bk = backup.make_backup(dev.read_all(), tmp_path / "bk")
    dev.slots[5] = dev.slots[5][:158] + b"\x01\x02\x03\x04"                            # 쓰지 않는 뒤 4바이트만 다름
    rows, _ = plan.build_plan(dev.read_all(), bk)
    assert rows[4].status == plan.ST_SAME


def test_pro_backup_is_refused_on_ge150_and_vice_versa(tmp_path):
    pro = device.FakeDevice()
    pro_bk = backup.make_backup(pro.read_all(), tmp_path / "pro")
    ge = device.FakeDevice(profile=GE)
    models.set_active(GE)
    ge_bk = backup.make_backup(ge.read_all(), tmp_path / "ge")
    with pytest.raises(backup.BackupError, match="다른 기종"):
        plan.build_plan(ge.read_all(), pro_bk)
    models.set_active(PRO)
    with pytest.raises(backup.BackupError, match="다른 기종"):
        plan.build_plan(pro.read_all(), ge_bk)


def test_old_backup_without_device_is_treated_as_pro(tmp_path):
    pro = device.FakeDevice()
    bk = backup.make_backup(pro.read_all(), tmp_path / "pro")
    f = bk / backup.PROOF_NAME
    proof = json.loads(f.read_text(encoding="utf-8"))
    proof.pop("device")
    proof.pop("preset_size")
    f.write_text(json.dumps(proof), encoding="utf-8")
    assert len(backup.verify_backup(bk)) == 200
    models.set_active(GE)
    with pytest.raises(backup.BackupError, match="다른 기종"):
        backup.verify_backup(bk)


def test_make_backup_rejects_wrong_size_for_active(tmp_path):
    ge_data = device.FakeDevice(profile=GE).read_all()
    with pytest.raises(RuntimeError, match="244"):
        backup.make_backup(ge_data, tmp_path / "x")                                   # 활성 기종이 Pro 일 때 162 바이트 자료
    assert not (tmp_path / "x").exists() or not any((tmp_path / "x").iterdir())


# ---------------------------------------------------------------- cli
def test_cli_convert_device_priority(tmp_path):
    import cli
    src = write_json(tmp_path, pack(preset()))
    cli.main(["convert", str(src), "--out", str(tmp_path / "a")])                         # 기본 = GE150PRO
    assert json.loads(next((tmp_path / "a").glob("*.mo")).read_text(encoding="utf-8"))["fileInfo"]["device"] == "GE150PRO"
    cli.main(["convert", str(src), "--out", str(tmp_path / "b"), "--device", "GE150"])    # 옵션
    assert json.loads(next((tmp_path / "b").glob("*.mo")).read_text(encoding="utf-8"))["fileInfo"]["device"] == "MOOER GE150"
    d = pack(preset())
    d["device"] = "GE150"
    src2 = write_json(tmp_path, d, "withdev.json")
    cli.main(["convert", str(src2), "--out", str(tmp_path / "c")])                        # JSON 의 device
    assert json.loads(next((tmp_path / "c").glob("*.mo")).read_text(encoding="utf-8"))["fileInfo"]["device"] == "MOOER GE150"
    cli.main(["convert", str(src2), "--out", str(tmp_path / "e"), "--device", "GE150PRO"])  # 옵션이 JSON 보다 우선(경고만)
    assert json.loads(next((tmp_path / "e").glob("*.mo")).read_text(encoding="utf-8"))["fileInfo"]["device"] == "GE150PRO"
    with pytest.raises(SystemExit):
        cli.main(["convert", str(src), "--device", "NOPE"])


def test_cli_fake_commands_set_active_model(monkeypatch, capsys):
    import cli
    monkeypatch.setattr(cli, "open_device", lambda fake: device.FakeDevice(profile=GE))   # 가짜 GE150 로 read 명령을 돌려 본다
    cli.main(["--fake", "read"])
    out = capsys.readouterr().out
    assert "01A GE150 Test 001" in out and "50D GE150 Test 200" in out
    assert models.active() is GE


# ---------------------------------------------------------------- 창
def _wait(app, cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _window(tmp_path, fake_profile=None):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk", fake_profile=fake_profile)
    win.msgs, win.issues = [], []
    win.info = win.msgs.append
    win.ask_yes_no = lambda t: (win.msgs.append(t), True)[1]
    win.show_issues = lambda title, issues: win.issues.append((title, issues))
    win.show()
    return app, win


def test_window_title_and_ge150_connection_label(tmp_path):
    app, win = _window(tmp_path, GE)
    assert win.windowTitle() == "GE150 / GE150 Pro 프리셋 적용기"
    win.connect_and_read()
    assert _wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    assert models.active() is GE
    assert win.lbl_dev.text().startswith("장비: ● GE150 연결됨") and "실장비 시험 전 기종입니다. 처음에는 한 슬롯만 적용해 확인해 주십시오." in win.lbl_dev.text()
    assert win.table.rowCount() == 200 and win.table.item(0, 2).text() == "GE150 Test 001"
    win.close()


def test_window_pro_connection_label(tmp_path):
    app, win = _window(tmp_path)
    win.connect_and_read()
    assert _wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    assert win.lbl_dev.text().startswith("장비: ● GE150 Pro 연결됨") and "시험 전" not in win.lbl_dev.text() and models.active() is PRO
    win.close()


def test_window_ge150_apply_confirm_has_note_and_json_uses_connected_model(tmp_path):
    app, win = _window(tmp_path, GE)
    win.connect_and_read()
    assert _wait(app, lambda: len(win.dev_data) == 200 and not win.busy)
    asked = []
    win.ask_model = lambda: asked.append(1)                                          # 장비가 연결돼 있으면 묻지 않는다
    res = win.load_tone_json([write_json(tmp_path, pack(preset(slot="01A")))])
    assert res.ok and not asked and mofile.read_mo(res.files[0]).errors == []
    assert json.loads(res.files[0].read_text(encoding="utf-8"))["fileInfo"]["device"] == "MOOER GE150"
    assert [r.slot for r in win.rows if r.status == plan.ST_CHANGE] == [1]
    win.apply()
    assert _wait(app, lambda: len(win.msgs) >= 2 and not win.busy and win.rows and win.rows[0].status == plan.ST_SAME)
    assert "처음에는 한 슬롯만 적용해 확인해 주십시오." in win.msgs[0] and win.dev.written == [1]
    assert "완료" in win.msgs[1]
    win.close()


def test_window_json_without_device_asks_model(tmp_path):
    app, win = _window(tmp_path)                                                       # 장비 연결 안 함
    calls = []
    win.ask_model = lambda: (calls.append(1), GE)[1]
    res = win.load_tone_json([write_json(tmp_path, pack(preset()))])
    assert calls == [1] and res.ok
    assert json.loads(res.files[0].read_text(encoding="utf-8"))["fileInfo"]["device"] == "MOOER GE150"
    win.close()


def test_window_json_with_device_key_does_not_ask(tmp_path):
    app, win = _window(tmp_path)
    win.ask_model = lambda: pytest.fail("묻지 않아야 합니다")
    d = pack(preset())
    d["device"] = "GE150"
    res = win.load_tone_json([write_json(tmp_path, d)])
    assert res.ok and json.loads(res.files[0].read_text(encoding="utf-8"))["fileInfo"]["device"] == "MOOER GE150"
    win.close()


def test_window_json_cancel_model_choice_does_not_convert(tmp_path):
    app, win = _window(tmp_path)
    win.ask_model = lambda: None
    src = write_json(tmp_path, pack(preset()))
    assert win.load_tone_json([src]) is None
    assert win.lbl_prog.text() == "톤 JSON 변환 취소" and not [p for p in tmp_path.iterdir() if p.name.startswith("pack_mo")]
    win.close()


@pytest.mark.parametrize("button,expected", [("GE150", GE), ("GE150 Pro", PRO), ("취소", None)])
def test_ask_model_dialog_buttons(tmp_path, button, expected):
    from PySide6.QtCore import QTimer
    app, win = _window(tmp_path)
    import app as appmod

    def click():
        box = app.activeModalWidget()
        if box is None:
            QTimer.singleShot(20, click)
            return
        next(b for b in box.buttons() if b.text() == button).click()

    QTimer.singleShot(20, click)
    assert appmod.MainWindow.ask_model.real(win) is expected
    win.close()


def test_main_fake_argument_parsing(monkeypatch):
    pytest.importorskip("PySide6")
    import app as appmod
    seen = {}

    class Stub:
        def __init__(self, fake=False, backup_root=None, fake_profile=None):
            seen.update(fake=fake, profile=fake_profile)

        def show(self): pass
        def connect_and_read(self): pass
        def handle_dropped(self, paths): pass

    class StubApp:
        def __init__(self, argv): pass
        def exec(self): return 0

    monkeypatch.setattr(appmod, "MainWindow", Stub)
    monkeypatch.setattr(appmod, "QApplication", StubApp)
    for argv, fake, prof in ((["--fake"], True, None), (["--fake=ge150"], True, GE), (["--fake=GE150PRO"], True, PRO), ([], False, None)):
        appmod.main(argv)
        assert seen == {"fake": fake, "profile": prof}, argv


def test_editor_state_detects_ge150_editor(monkeypatch):
    """GE150 공식 에디터(실행기 GE150.exe·본체 MOOER_GE150.exe)가 켜져 있어도 연결·적용을 막는다."""
    from ge150 import device

    class R:
        returncode = 0

        def __init__(self, out):
            self.stdout = out

    for name in ("MOOER_GE150.exe", "GE150.exe", "GE150PRO.exe"):
        monkeypatch.setattr(device.subprocess, "run", lambda *a, _n=name, **k: R('"%s","1","Console","1","1 K"\n' % _n))
        assert device.editor_state() == device.EDITOR_RUNNING, name
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: R('"GE150PROUpdate.exe","1","Console","1","1 K"\n'))
    assert device.editor_state() == device.EDITOR_NONE


def test_excel_user_strings_are_never_formulas(tmp_path):
    """장비 이름·곡·아티스트가 '=' 로 시작해도 수식(<f>)이 아니라 글자로 저장된다."""
    import zipfile
    from openpyxl import load_workbook
    from ge150 import excel
    dev = device.FakeDevice()
    data = dev.read_all()
    mo = codec.decode(data[1])
    mo["fileInfo"]["preset_name"] = "=1+1"
    row = plan.Row(slot=1, device_name="=HYPERLINK(1)", new_name="=1+1", status=plan.ST_CHANGE, data=codec.encode(mo), exact=True)
    out = excel.export_xlsx(tmp_path / "f.xlsx", [row], data, "folder", meta={1: {"song": "=SUM(1)", "artist": "+cmd", "role": "@x"}})
    ws = load_workbook(out)["요약"]
    cells = {c.value: c for c in ws[2]}
    assert {"=HYPERLINK(1)", "=1+1", "=SUM(1)"} <= set(cells) and all(cells[k].data_type == "s" for k in ("=HYPERLINK(1)", "=1+1", "=SUM(1)"))
    ws2 = load_workbook(out)["톤 상세"]
    assert ws2[2][1].value == "=1+1" and ws2[2][1].data_type == "s"
    with zipfile.ZipFile(out) as z:
        assert not any(b"<f>" in z.read(n) or b"<f " in z.read(n) for n in z.namelist() if n.startswith("xl/worksheets/"))
