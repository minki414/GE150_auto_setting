"""GE150 Pro 톤 JSON -> GE150 톤 JSON 자동 변환. GE150 에 없는 Pro 전용 모델을 가장 비슷한 GE150 모델로 바꿉니다.

to_ge150(doc, stats=None) -> (새 문서, notes)   notes = 바뀐 내용 한 줄씩("슬롯 · 모듈 · 내용"), stats(Counter)에는 종류별 건수

소리가 같지는 않습니다(대응표는 노브 값을 근사한 것). 대응이 없거나 옮길 수 없는 것은 오류 메모로 남기고 그대로 둡니다 —
그대로 둔 항목은 GE150 톤 JSON 검사에서 오류로 드러나, 조용히 틀린 값이 장비에 들어가지 않습니다.
"""
import copy

from . import catalog, models
from .tonepack import FORMATS, _norm

NAME_LEN = models.GE150.name_len
_AMP, _CAB = 2, 3   # 사용자 샘플 칸(EMPTY)이 있는 모듈 번호


def _comp(attack, ratio):
    """S-COMP·RED COMP -> YELLOW COMP·BLUE COMP: 감도(SENS)가 높을수록 THRES 를 낮춘다."""
    return lambda g: {"ATTACK": attack, "THRES": 100 - g("SENS"), "RATIO": ratio, "LEVEL": g("LEVEL")}


def _plain(**fixed):
    return lambda g: dict(fixed)


# (모듈 번호, 정규화한 Pro 모델 이름) -> (GE150 모델, 노브 만드는 함수 또는 None=같은 이름 노브 그대로, 끌지 여부)
# 노브 함수는 g(이름, 기본값=50) 로 원본 노브 값을 읽는다(F.BACK/FEEDBACK 같은 별칭도 같은 노브로 읽음).
_RULES = {
    (0, "scomp"): ("YELLOW COMP", _comp(50, 40), False),
    (0, "redcomp"): ("BLUE COMP", _comp(50, 40), False),
    (0, "limit"): ("YELLOW COMP", lambda g: {"ATTACK": 30, "THRES": g("THRES"), "RATIO": 90, "LEVEL": g("LEVEL")}, False),
    (0, "intelreducer"): ("YELLOW COMP", _plain(ATTACK=50, THRES=50, RATIO=50, LEVEL=50), True),   # 대응 모델 없음
    (1, "vxyubeod"): ("808", None, False),
    (1, "tightmetal"): ("TIGHT DS", None, False),
    (1, "thejuicer"): ("FULL DRV", None, False),
    (1, "rumbledrive"): ("FULL DRV", None, False),
    (1, "solo"): ("RIET", None, False),
    (1, "bluesmood"): ("JIMMY OD", None, False),
    (1, "bluescrab"): ("JIMMY OD", None, False),
    (1, "hustledrive"): ("OBSESSIVE DIST", None, False),
    (6, "dualphaser"): ("PHASER", None, False),
    (6, "modernphaser"): ("PHASER", None, False),
    (6, "flangerpro"): ("FLANGER", lambda g: {"RATE": g("RATE"), "MIX": g("MIX"), "FEEDBACK": g("FEEDBACK")}, False),   # DELAY 노브는 버림
    (6, "modernrotary"): ("ROTARY", None, False),
    (6, "opticaltrmolo"): ("TREMOLO", lambda g: {"RATE": g("SPEED"), "MIX": g("INTENSITY"), "TONE": g("TONE")}, False),
    (6, "octave"): ("ANA-CHORUS", _plain(RATE=50, MIX=50, TONE=50, DEPTH=50), True),   # 대응 모델 없음
    (8, "shimmer"): ("HALL", lambda g: {"PRE DELAY": g("P.DELAY", 20), "LEVEL": g("LEVEL"), "DECAY": g("DECAY"), "TONE": g("TONE")}, False),
}


def _getter(params: dict):
    vals = {}
    for k, v in params.items():
        vals[_norm(k)] = v
        alias = models.param_alias(k)
        if alias:
            vals.setdefault(_norm(alias), v)
    return lambda name, default=50: vals.get(_norm(name), default)


def _ge150_has(mod: int, norm_name: str) -> bool:
    return any(_norm(catalog.model(mod, t, models.GE150)["name"]) == norm_name for t in range(catalog.model_count(mod, models.GE150)))


def _fit_name(name: str) -> str:
    """GE150 은 이름이 14자까지다. ① 끝 단어 SOLO -> SOL ② 마지막 단어(역할) 앞부분의 공백 제거 ③ 앞부분을 잘라 14자."""
    name = name.rstrip()
    if len(name) <= NAME_LEN:
        return name
    head, _, last = name.rpartition(" ")
    if last.upper() == "SOLO":
        last = "SOL"
    if not head:
        return last[:NAME_LEN]
    name = head + " " + last
    if len(name) > NAME_LEN:
        head = head.replace(" ", "")
        name = head + " " + last
    if len(name) > NAME_LEN:
        room = NAME_LEN - len(last) - 1
        name = head[:room] + " " + last if room > 0 else last   # 역할 단어만으로 넘치면 앞 공백 없이 자른다
    return name[:NAME_LEN]


def _fmt(params: dict) -> str:
    return ", ".join("%s %s" % kv for kv in params.items())


def to_ge150(doc: dict, stats=None):
    """Pro 톤 JSON(dict) -> (GE150 톤 JSON dict, notes). 입력은 바꾸지 않습니다."""
    notes = []
    count = (lambda key: stats.update([key])) if stats is not None else (lambda key: None)
    out = {"format": FORMATS[0], "device": models.GE150.key}
    out.update({k: copy.deepcopy(v) for k, v in doc.items() if k not in ("format", "device")})
    if isinstance(out.get("title"), str):
        out["title"] = out["title"].replace("GE150 Pro", "GE150")
    for item in out.get("presets", []):
        if not isinstance(item, dict):
            continue
        slot = item.get("slot") if isinstance(item.get("slot"), str) else "?"

        def note(module, text):
            notes.append("%s · %s · %s" % (slot, module, text))

        if isinstance(item.get("name"), str):
            fitted = _fit_name(item["name"])
            if fitted != item["name"].rstrip():
                note("이름", "%s → %s" % (item["name"].rstrip(), fitted))
                count("이름 줄임")
                item["name"] = fitted
        mods = item.get("modules")
        for key, spec in (mods.items() if isinstance(mods, dict) else []):
            mod = next((i for i, m in enumerate(catalog.MODULES) if _norm(m) == _norm(key)), None)
            if mod is None or not isinstance(spec, dict) or not isinstance(spec.get("model"), str):
                continue
            label, n = catalog.MODULES[mod], _norm(spec["model"])
            params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
            if n == "empty" and mod in (_AMP, _CAB):
                note(label, "오류: 변환 불가 — 사용자 샘플 칸(EMPTY)은 GE150 으로 옮길 수 없습니다.")
                count("오류: 변환 불가")
                continue
            rule = _RULES.get((mod, n))
            if rule is None and not _ge150_has(mod, n):
                note(label, "오류: 대응표 없음 — 모델 %s 은(는) GE150 에 없고 바꿀 모델도 정하지 않았습니다." % spec["model"])
                count("오류: 대응표 없음")
                continue
            if rule is None or rule[1] is None:   # 모델은 그대로이거나 이름만 바뀌고 노브는 같은 이름 그대로
                renamed = {}
                for k, v in params.items():
                    new_k = models.PARAM_ALIASES.get(str(k).strip().upper(), k)
                    if new_k != k:
                        count("노브 이름 %s → %s" % (str(k).strip().upper(), new_k))   # 이름만 바뀌는 흔한 변경이라 건수만 센다
                    renamed[new_k] = v
                new_params = renamed
            else:
                try:
                    new_params = rule[1](_getter(params))
                except TypeError:   # 숫자가 아닌 노브 값은 계산할 수 없다
                    note(label, "오류: 변환 불가 — 노브 값이 정수가 아닙니다.")
                    count("오류: 변환 불가")
                    continue
            if rule is None:   # GE150 에도 있는 모델: 노브 이름만 GE150 쪽으로
                if "params" in spec:
                    spec["params"] = new_params
                continue
            old = spec["model"]
            spec["model"], spec["params"] = rule[0], new_params
            if rule[2]:
                spec["on"] = False
                note(label, "%s → %s (대응 모델 없음, 기본값으로 두고 끔)" % (old, rule[0]))
            elif rule[1] is not None:
                note(label, "%s → %s (%s)" % (old, rule[0], _fmt(new_params)))
            else:
                note(label, "%s → %s" % (old, rule[0]))
            count("%s → %s" % (old, rule[0]))
    return out, notes


def summary(stats) -> list:
    """종류별 건수를 '이름: N건' 줄 목록으로(많은 순이 아니라 이름 순)."""
    return ["%s: %d건" % (k, stats[k]) for k in sorted(stats)]


# 개정 기록 | 2026-11-12 | 신설: Pro 톤 JSON -> GE150 변환(모델 대응표·이름 14자 규칙) | H단계
