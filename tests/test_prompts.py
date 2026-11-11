"""AI 요청 프롬프트(prompts/*.txt) 시험: 저장된 파일이 생성 도구의 결과와 같고, 모델 목록이 카탈로그와 일치하는지."""
import json
import re
import sys

import pytest

from conftest import ROOT
from ge150 import catalog, models, tonepack

sys.path.insert(0, str(ROOT / "tools"))
import make_prompts  # noqa: E402

PROFILES = list(models.PROFILES)


def parse_models(text):
    """프롬프트의 [모델 목록] -> {모듈 이름: {모델 이름: [노브 이름…]}}"""
    body = text[text.index("[모델 목록] —"):]
    out, mod, names = {}, None, None
    for line in body.splitlines():
        if re.fullmatch(r"\[[A-Z/ ]+\]", line):
            mod = line[1:-1]
            out[mod] = {}
        elif line.startswith("- 모델: "):
            names = line[len("- 모델: "):].split(" / ")
        elif line.startswith("  노브: "):
            knobs = [re.sub(r"\(.*\)$", "", k) for k in line[len("  노브: "):].split(", ")]   # 노브 이름·범위 표기에는 ", " 가 없다
            for n in names:
                assert n not in out[mod], n
                out[mod][n] = knobs
    return out


@pytest.mark.parametrize("p", PROFILES, ids=lambda p: p.key)
def test_saved_prompt_equals_generated(p):
    saved = (make_prompts.OUT_DIR / make_prompts.FILES[p.key]).read_text(encoding="utf-8")
    assert saved == make_prompts.render(p), "카탈로그나 틀을 바꿨다면 python tools/make_prompts.py 를 다시 실행하십시오"


@pytest.mark.parametrize("p", PROFILES, ids=lambda p: p.key)
def test_model_list_matches_catalog_both_ways(p):
    listed = parse_models(make_prompts.render(p))
    assert list(listed) == catalog.MODULES
    with models.using(p):
        for i, mod in enumerate(catalog.MODULES):
            usable = {}
            for t in range(catalog.model_count(i)):
                m = catalog.model(i, t)
                if m["name"] != "EMPTY" and not m["userSample"] and all(q["type"] != 0 or q["step"] == 1 for q in m["params"]):
                    usable[m["name"]] = [q["name"] for q in m["params"]]
            assert listed[mod] == usable, mod                       # 목록의 모델·노브가 카탈로그에 있고, 카탈로그의 쓸 수 있는 모델은 빠짐없다
    assert "PITCH SHIFT" not in listed["MOD"] and "DETUNE" not in listed["MOD"] and "EMPTY" not in str(listed["CAB"])
    assert "CUSTOM EQ" in listed["EQ"]                              # 목록에는 있고 규칙에서 '쓰지 말 것'으로 안내


def test_knob_names_per_model():
    pro, ge = (make_prompts.render(p) for p in PROFILES)
    assert "F.BACK" in pro and "P.DELAY" in pro and "FEEDBACK" not in parse_models(pro)["DELAY"]["DIGITAL"]
    assert parse_models(ge)["DELAY"]["DIGITAL"] == ["LEVEL", "FEEDBACK", "TIME", "SUB-D"]
    assert parse_models(ge)["REVERB"]["ROOM"][0] == "PRE DELAY" and '"F.BACK"' not in ge and '"P.DELAY"' not in ge
    assert "SHIMMER" in pro and "SHIMMER" not in ge and "TIGHT METAL" in pro and "TIGHT METAL" not in ge


@pytest.mark.parametrize("p", PROFILES, ids=lambda p: p.key)
def test_prompt_header_and_example_json(p, tmp_path):
    text = make_prompts.render(p)
    assert "%d자 이하" % p.name_len in text and text.count("%d자 이하" % p.name_len) == 2
    assert "합주" not in text and "여기에 곡을 적으십시오" in text and "번호 아티스트 - 곡명" in text
    start = text.index('{"format"')
    doc = json.loads(text[start:text.index("\n\n[7]")])
    assert doc["format"] == "GE150-TONEPACK" and doc["device"] == p.key
    assert len(doc["presets"][0]["name"]) <= p.name_len
    f = tmp_path / "ex.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    presets, errors, warnings = tonepack.load_tonepacks([f], profile=p)      # 예시가 그 기종에서 오류·경고 없이 통과
    assert errors == [] and warnings == [] and len(presets) == 1
