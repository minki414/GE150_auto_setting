"""H단계 시험: 저장소에 넣은 샘플 톤(samples/)이 각 기종으로 오류 없이 변환되는지."""
import json
from pathlib import Path

import pytest

from ge150 import models, tonepack
from ge150 import protocol as P

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SLOTS = [P.slot_label(s) for s in range(1, 201)]


@pytest.mark.parametrize("name,profile", [("합주_GE150Pro.json", models.PRO), ("합주_GE150.json", models.GE150)])
def test_sample_converts_without_errors(name, profile):
    path = SAMPLES / name
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["device"] == profile.key and [p["slot"] for p in doc["presets"]] == SLOTS      # 200개, 01A~50D
    presets, errors, warnings = tonepack.load_tonepacks([path], profile=profile)
    assert [e.text() for e in errors] == [] and len(presets) == 200
    assert not any("용이고 변환 대상은" in w.text() for w in warnings)                          # 기종 불일치 경고 없음
    assert all(len(p["name"]) <= profile.name_len for p in presets)


def test_sample_has_song_info_for_every_slot():
    doc = json.loads((SAMPLES / "합주_GE150Pro.json").read_text(encoding="utf-8"))
    assert all(p.get("song") and "artist" in p for p in doc["presets"])
    assert [p["role"] for p in doc["presets"][:4]] == ["CLEAN", "CRUNCH", "DRIVE", "SOLO"]


def test_ge150_sample_only_differs_by_conversion():
    """GE150 판의 모델은 모두 GE150 목록에 있고, 변환 내역 파일의 오류 건수는 0이다."""
    notes = (SAMPLES / "GE150_변환내역.txt").read_text(encoding="utf-8")
    assert "GE150 검사 결과: 오류 0건" in notes
    pro = json.loads((SAMPLES / "합주_GE150Pro.json").read_text(encoding="utf-8"))
    ge = json.loads((SAMPLES / "합주_GE150.json").read_text(encoding="utf-8"))
    for a, b in zip(pro["presets"], ge["presets"]):
        assert a["song"] == b["song"] and a["slot"] == b["slot"]
        for key in ("AMP", "CAB", "EQ", "NS GATE"):                                          # 이 모듈들은 두 기종이 같다
            assert a["modules"][key] == b["modules"][key]
