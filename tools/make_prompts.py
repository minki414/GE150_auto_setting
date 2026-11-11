"""AI 에게 톤 JSON 을 요청하는 프롬프트를 기종별로 만든다 -> prompts/GE150Pro_AI_프롬프트.txt, prompts/GE150_AI_프롬프트.txt

사용: python tools/make_prompts.py
[모델 목록]은 ge150/data/catalog*.json 에서 만들어 목록이 늘 카탈로그와 같다. 곡 목록은 빈칸(사용자가 채움).
시험(tests/test_prompts.py)이 저장된 파일이 이 도구의 결과와 같은지 확인하므로, 카탈로그를 바꾸면 다시 실행한다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ge150 import models  # noqa: E402

OUT_DIR = ROOT / "prompts"
FILES = {"GE150PRO": "GE150Pro_AI_프롬프트.txt", "GE150": "GE150_AI_프롬프트.txt"}

# 노브 이름이 기종마다 다른 두 가지(GE150 FEEDBACK = Pro F.BACK, GE150 PRE DELAY = Pro P.DELAY)
KNOB_NAMES = {"GE150PRO": ("F.BACK", "P.DELAY"), "GE150": ("FEEDBACK", "PRE DELAY")}

TEMPLATE = """=== MOOER {label} 톤 데이터 요청 (톤 JSON v1) ===
아래 [곡 목록]의 곡마다 {label}용 기타 톤 4개(A~D)를 만들어 JSON으로 출력하십시오.
결과 JSON은 전용 적용기로 장비에 바로 들어갑니다. 형식이 틀리면 거부됩니다.

[1] 자료 조사 (필수)
- 곡마다 실제 톤 자료를 먼저 찾으십시오: 공개 톤 데이터셋(github Pera-Labs/guitar-tone-dataset), Equipboard, Rig Rundown·인터뷰,
  MOOER CustomTone, Tone3000, guitarpatches, 커버·톤 재현 영상(YouTube), 국내 커뮤니티(뮬 등).
- 찾은 자료의 앰프·페달·공간계를 [모델 목록]에서 가장 가까운 모델로 옮기십시오.
- 자료를 끝내 못 찾은 곡(최소화)은 그 곡의 장르·편곡·기타 사운드를 조사해 어울리는 톤으로 만드십시오.
- sources에는 실제로 열어 본 URL만 적습니다. 지어낸 링크·홈페이지 주소로 채우기 금지.
- confidence: A=아티스트 공식 장비·인터뷰 / B=신뢰할 만한 재현·분석 / C=곡 조사 기반 추정 / D=근거 없음

[2] 슬롯 역할 (한 곡 = 한 뱅크 = A~D)
- A CLEAN(클린) / B CRUNCH(크런치) / C DRIVE(드라이브·후렴) / D SOLO(솔로·멜로디)
- C와 D는 DS/OD를 반드시 켭니다(on:true).
- 곡에 필요하면 코러스·딜레이·리버브 등 추가 가능. 한 뱅크를 두 곡으로 나누기 금지.

[3] 음량 (중요 — 공장 프리셋 기준값. 이보다 작게 만들지 마십시오)
- 체감 음량 A < B < C < D. 솔로(D)가 가장 크고 앞으로 나와야 합니다.
- 슬롯별 기준값:
  A 클린 : 클린 채널 앰프의 GAIN 55~75(클린 채널은 GAIN이 볼륨 역할, 찌그러지지 않음), MST 75~85, COMP 켜면 LEVEL 70~80
  B 크런치: AMP GAIN 35~50, MST 75~85. DS/OD를 쓰면 VOLUME 85~95, GAIN 10~30
  C 드라이브: DS/OD 켬(VOLUME 85~95, GAIN 0~25 = 부스터처럼 앰프를 밀어 줌), AMP GAIN 45~60, MST 80~85
  D 솔로 : DS/OD 켬(VOLUME 95~100, GAIN 0~25), AMP GAIN 40~55, MST 85~90, 미드 살림, DELAY LEVEL 15~25
- 게인은 드라이브 페달보다 앰프에서 만들고, 페달은 주로 볼륨 부스트로 씁니다(퍼즈·RAT처럼 곡 자체가 페달 소리인 경우는 예외).
- 공간계(REVERB·DELAY) LEVEL은 20~35 정도. EQ는 필요할 때만.

[4] 다양성 (돌려막기 금지)
- 곡마다 자료에 맞는 앰프·드라이브를 고르십시오. 같은 틀을 여러 곡에 복사하지 마십시오.
- 50뱅크 전체에서 AMP 모델 20종 이상, DS/OD 모델 12종 이상을 씁니다.
- 노브 값까지 똑같은 프리셋 금지. 같은 가수의 곡도 곡마다 차이를 둡니다.

[5] 이름 (장비 화면 표시, {name_len}자 이하)
- "곡 이름 + 공백 + 역할"(CLN / CRN / DRV / SOLO). 곡 이름만 보고 어떤 곡인지 바로 알 수 있게 쓰십시오.
- 영어 곡명은 그대로(줄여야 하면 앞 단어를 살림). 한국어 곡명은 뜻이 통하는 짧은 영어로.
  예) 입춘 → "Spring CLN", Creep → "Creep SOLO", 그대에게 → "To You DRV", 밤이 깊었네 → "Deep Night CRN"
- 뜻을 알 수 없는 줄임말 금지(예: "Ipchun", "WeWe", "Nerd2521").
- 영문·숫자·공백·키보드 기호만. 한글 금지. 슬롯 번호(01A 등)는 넣지 않습니다.

[6] JSON 규칙
- 모듈 9개를 모두 적습니다(끈 모듈도 모델·노브 포함): FX/COMP, DS/OD, AMP, CAB, NS GATE, EQ, MOD, DELAY, REVERB
- model·노브 이름은 [모델 목록]의 글자 그대로. 그 모델의 노브를 빠짐없이 적습니다.
- 값: 일반 노브는 정수 0~100. EQ 대역은 -16~16(dB). DELAY TIME 40~2500(ms). {pd} 0~500.
  글자 노브(TUBE, MIC, SUB-D 등)는 목록의 글자 그대로. SUB-D는 "OFF".
- 쓰지 말 것: EQ의 CUSTOM EQ(대신 MOOER G / HM / G-6), "exp" 키.
- 올바른 JSON만 출력(주석·설명 금지). 길면 tonepack_01-25.json / tonepack_26-50.json으로 나눠도 됩니다(슬롯 겹침 금지).

{{"format":"GE150-TONEPACK","device":"{key}","version":1,"title":"<묶음 이름>","presets":[
 {{"slot":"01A","name":"Wonderwall CLN","song":"Wonderwall","artist":"Oasis","role":"CLEAN",
  "modules":{{
   "FX/COMP":{{"on":true,"model":"YELLOW COMP","params":{{"ATTACK":50,"THRES":45,"RATIO":30,"LEVEL":75}}}},
   "DS/OD":{{"on":false,"model":"TUBE DR","params":{{"VOLUME":90,"TONE":50,"GAIN":15}}}},
   "AMP":{{"on":true,"model":"65 US DX","params":{{"GAIN":62,"BASS":50,"MID":50,"TREBLE":55,"PRES":50,"MST":80}}}},
   "CAB":{{"on":true,"model":"US DLX 112","params":{{"TUBE":"6V6","MIC":"SM57","CENTER":30,"DISTANCE":40}}}},
   "NS GATE":{{"on":true,"model":"NOISE GATE","params":{{"ATTACK":50,"RELEASE":50,"THRES":35}}}},
   "EQ":{{"on":false,"model":"MOOER G","params":{{"100Hz":0,"250Hz":0,"630Hz":0,"1.6KHz":0,"4KHz":0}}}},
   "MOD":{{"on":true,"model":"ANA-CHORUS","params":{{"RATE":30,"MIX":40,"TONE":50,"DEPTH":45}}}},
   "DELAY":{{"on":false,"model":"DIGITAL","params":{{"LEVEL":20,"{fb}":25,"TIME":380,"SUB-D":"OFF"}}}},
   "REVERB":{{"on":true,"model":"ROOM","params":{{"{pd}":20,"LEVEL":25,"DECAY":40,"TONE":50}}}}}},
  "memo":"<톤 의도·근거 한 줄>","sources":["<실제 열어 본 URL>"],"confidence":"B"}}
]}}

[7] 출력 전 점검
- slot 01A~50D 중복 없음 / name {name_len}자 이하·곡명 식별 가능 / 모듈 9개·노브 전부 / 값 범위·정수
- C·D의 DS/OD on / [3] 기준값 범위 / MST가 A≤B≤C<D / AMP 20종·DS 12종 이상 / 곡마다 sources 또는 조사 근거(memo)

[곡 목록] — 여기에 곡을 적으십시오 (아래 두 줄은 형식 예시이니 지우고 쓰십시오)
- 한 줄에 `번호 아티스트 - 곡명`. 번호 = 뱅크 01~50 (1곡 = 1뱅크 = A~D 4슬롯).
- 뱅크 번호를 그대로 slot에 쓰십시오(예: 13번 곡 → 13A, 13B, 13C, 13D).
01 Oasis - Wonderwall
02 아이유 - 좋은 날

[모델 목록] — 괄호 없는 노브는 0~100 정수. 같은 줄의 모델은 노브 구성이 같음.

{models}
"""


def _knob(p: dict) -> str:
    if p["type"] == 1:
        return '%s("%s")' % (p["name"], "|".join(p["options"]))
    lo, hi = int(p["min"]), int(p["max"])
    unit = (" " + p["suffix"]) if p["suffix"] else ""
    return p["name"] if (lo, hi, unit) == (0, 100, "") else "%s(%d~%d%s)" % (p["name"], lo, hi, unit)


def _usable(m: dict) -> bool:
    """EMPTY(사용자 샘플 칸)와 PITCH 계열(단계 != 1, 값 규칙 미확정)은 AI 에게 알려 주지 않는다."""
    return not m["userSample"] and m["name"].upper() != "EMPTY" and all(p["type"] != 0 or p["step"] == 1 for p in m["params"])


def model_list(chains: list) -> str:
    """모듈마다 노브 구성이 같은 모델을 한 줄로 묶는다(처음 나온 순서 유지)."""
    out = []
    for ch in chains:
        groups = {}
        for m in ch["models"]:
            if _usable(m):
                groups.setdefault(tuple(_knob(p) for p in m["params"]), []).append(m["name"])
        out.append("[%s]" % ch["name"])
        for knobs, names in groups.items():
            out.append("- 모델: %s\n  노브: %s" % (" / ".join(names), ", ".join(knobs)))
        out.append("")
    return "\n".join(out).rstrip("\n")


def render(profile) -> str:
    chains = json.loads((ROOT / "ge150" / "data" / profile.catalog_file).read_text(encoding="utf-8"))["chains"]
    fb, pd = KNOB_NAMES[profile.key]
    return TEMPLATE.format(label=profile.label, key=profile.key, name_len=profile.name_len, fb=fb, pd=pd,
                           models=model_list(chains))


if __name__ == "__main__":
    OUT_DIR.mkdir(exist_ok=True)
    for p in models.PROFILES:
        path = OUT_DIR / FILES[p.key]
        path.write_text(render(p), encoding="utf-8", newline="\n")
        print("저장:", path.name)
