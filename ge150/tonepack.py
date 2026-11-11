"""톤 JSON(GE150-TONEPACK / 옛 이름 GE150PRO-TONEPACK, v1) -> .mo 변환. 규칙의 정본은 docs/톤JSON_형식.md 입니다.

load_tonepacks(paths, profile=None) -> (presets, errors, warnings)   검사·변환만(파일을 만들지 않음)
convert(paths, out_dir=None, profile=None) -> ConvertResult           검사 통과 시 .mo 폴더 생성(덮어쓰지 않음)
profile = 변환 대상 기종(None 이면 활성 기종). 모델·이름 길이·노브 이름은 모두 대상 기종 기준으로 검사한다.
"""
import difflib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import catalog, codec, models, mofile
from . import protocol as P

FORMATS, VERSION = ("GE150-TONEPACK", "GE150PRO-TONEPACK"), 1   # 앞이 현재 이름, 뒤는 예전 이름(같은 뜻)
META_NAME = "tonepack-meta.json"
SLOT_RE = re.compile(r"^\d{2}[A-Da-d]$")
# 정본 3절: exp 를 생략하면 볼륨 페달 기본값
DEFAULT_EXP = {"FUN_SWITCH": 0, "MODULE_CTRL": 0, "PARA_CTRL": 6, "VOL_MAX": 100, "VOL_MIN": 0, "VOL_SWITCH": 1}


@dataclass
class Issue:
    reason: str
    file: str = ""
    slot: str = ""
    module: str = ""
    knob: str = ""

    def text(self) -> str:
        where = " · ".join(x for x in (self.file, ("슬롯 " + self.slot) if self.slot else "",
                                       ("모듈 " + self.module) if self.module else "",
                                       ("노브 " + self.knob) if self.knob else "") if x)
        return "%s: %s" % (where, self.reason) if where else self.reason


@dataclass
class ConvertResult:
    out_dir: Path | None = None
    presets: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    files: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors and self.out_dir is not None


def _norm(s: str) -> str:
    """모델·모듈 이름 비교용: 앞 번호 제거, 대소문자·공백·하이픈 무시."""
    return re.sub(r"[\s\-]+", "", re.sub(r"^\s*\d+\s*:\s*", "", s)).lower()


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _find_model(mod: int, name: str):
    """모듈 안에서만 모델 이름을 찾는다(FX/COMP 와 NS GATE 에 같은 INTEL REDUCER 가 있음). -> (type, None) 또는 (None, 제안 목록)"""
    n = _norm(name)
    names = [catalog.model_name(mod, t) for t in range(catalog.model_count(mod))]
    for t, nm in enumerate(names):
        if _norm(nm) == n:
            return t, None
    uniq = list(dict.fromkeys(names))
    close = difflib.get_close_matches(n, [_norm(x) for x in uniq], n=3, cutoff=0.0)
    by_norm = {_norm(x): x for x in uniq}
    return None, [by_norm[c] for c in close]


def _default_display(p: dict):
    """빠진 노브의 기본값(정본 4절): 0~100 -> 50, EQ -> 0dB, TIME -> 400ms, P.DELAY -> 20, FREQ -> 범위 중간, 목록형 -> 첫 항목."""
    if p["type"] == 1:
        return p["options"][0]
    if p["min"] < 0:
        return 0
    if p["suffix"] == "ms":
        return 400
    if p["suffix"] == "Hz":
        return int((p["min"] + p["max"]) // 2)
    return 50 if p["max"] == 100 else 20


def _to_raw(p: dict, v):
    """표시값 -> raw. 문제가 있으면 (None, 사유)."""
    if p["type"] == 1:
        if not isinstance(v, str):
            return None, "목록형 노브는 글자로 적어야 합니다(선택지: %s)." % " | ".join(p["options"])
        for i, o in enumerate(p["options"]):
            if o.strip().lower() == v.strip().lower():
                return i, None
        return None, "'%s' 은(는) 선택지에 없습니다(선택지: %s)." % (v, " | ".join(p["options"]))
    if not _is_int(v):
        return None, "정수로 적어야 합니다(받은 값: %r)." % (v,)
    lo, hi = catalog.display_range(p)
    if not lo <= v <= hi:
        return None, "값 %d 이(가) 범위(%d~%d)를 벗어났습니다." % (v, lo, hi)
    return catalog.display_to_raw(p, v), None


def _build_module(mod: int, spec, issue, warn):
    """모듈 1개 -> .mo 의 effectModule 항목(SWITCH/TYPE/Data). 오류가 있으면 None."""
    label = catalog.MODULES[mod]
    if not isinstance(spec, dict):
        issue("모듈 값이 객체가 아닙니다.", label)
        return None
    bad = False
    on = spec.get("on")
    if not isinstance(on, bool):
        issue("on 은 true 또는 false 여야 합니다.", label)
        bad = True
    mname = spec.get("model")
    if not isinstance(mname, str) or not mname.strip():
        issue("model 이 없습니다.", label)
        return None
    if _norm(mname) == "empty":
        issue("사용자 샘플 칸(EMPTY)은 쓸 수 없습니다.", label)
        return None
    ty, sugg = _find_model(mod, mname)
    if ty is None:
        issue("모델 '%s' 을(를) 찾을 수 없습니다. 비슷한 이름: %s" % (mname, ", ".join(sugg)), label)
        return None
    ps = catalog.params(mod, ty)
    if any(p["type"] == 0 and p["step"] != 1 for p in ps):  # PITCH 계열(0.1 단계) — 값 규칙 미확정
        issue("모델 %s 은(는) PITCH 값 규칙이 확정되지 않아 쓸 수 없습니다." % catalog.model_name(mod, ty), label)
        return None
    params = spec.get("params", {})
    if not isinstance(params, dict):
        issue("params 가 객체가 아닙니다.", label)
        return None
    by_norm = {}
    for p in ps:
        by_norm[_norm(p["name"])] = p
    given = {}
    for k, v in params.items():
        p = by_norm.get(_norm(str(k)))
        if p is None and models.param_alias(k):   # F.BACK = FEEDBACK, P.DELAY = PRE DELAY (기종마다 부르는 이름이 다름)
            p = by_norm.get(_norm(models.param_alias(k)))
        if p is None:
            issue("모델 %s 에 없는 노브입니다(노브: %s)." % (catalog.model_name(mod, ty), ", ".join(x["name"] for x in ps)),
                  label, str(k))
            bad = True
        elif p["name"] in given:
            issue("같은 노브가 두 번 적혔습니다.", label, str(k))
            bad = True
        else:
            given[p["name"]] = v
    data = {}
    for p in ps:
        if p["name"] in given:
            raw, why = _to_raw(p, given[p["name"]])
            if why:
                issue(why, label, p["name"])
                bad = True
                continue
            if p.get("suffix") == "Hz":
                warn("FREQ 값은 장비에서 확인되기 전입니다(그대로 적용).", label, p["name"])
        else:
            dv = _default_display(p)
            raw, _ = _to_raw(p, dv)
            warn("노브가 없어 기본값 %s 을(를) 적용합니다." % dv, label, p["name"])
        data[p["name"]] = raw
        if raw is not None and catalog.check_value(mod, ty, p["name"], raw):  # 이중 확인(catalog 범위)
            issue(catalog.check_value(mod, ty, p["name"], raw), label, p["name"])
            bad = True
    if bad:
        return None
    return {"Data": data, "SWITCH": 1 if on else 0, "TYPE": ty}


def _default_module(mod: int) -> dict:
    return {"Data": {p["name"]: _to_raw(p, _default_display(p))[0] for p in catalog.params(mod, 0)}, "SWITCH": 0, "TYPE": 0}


def _build_preset(item, fname: str, errors: list, warnings: list, seen: dict):
    """프리셋 1개 -> dict(slot,label,name,mo,meta) 또는 None."""
    slot_txt = item.get("slot") if isinstance(item, dict) else None
    label = slot_txt if isinstance(slot_txt, str) else ""
    n0 = len(errors)

    def issue(reason, module="", knob=""):
        errors.append(Issue(reason, fname, label, module, knob))

    def warn(reason, module="", knob=""):
        warnings.append(Issue(reason, fname, label, module, knob))

    if not isinstance(item, dict):
        issue("프리셋이 객체가 아닙니다.")
        return None
    slot = None
    if not isinstance(slot_txt, str) or not SLOT_RE.match(slot_txt) or P.parse_slot(slot_txt) is None:
        issue("slot 형식이 올바르지 않습니다(01A~50D).")
    else:
        slot = P.parse_slot(slot_txt)
        label = P.slot_label(slot)
        if slot in seen:
            issue("같은 슬롯이 중복되었습니다(먼저 나온 곳: %s)." % seen[slot])
            slot = None
        else:
            seen[slot] = fname  # 오류가 있는 프리셋이어도 슬롯은 선점한다(뒤의 중복을 놓치지 않게)
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        issue("name 이 없습니다.")
    elif mofile.validate_name(name.rstrip()):  # D3: 공백 포함 16자 이하, ASCII 0x20~0x7E(기호 포함)
        issue("name 은 공백 포함 %d자 이하의 영문·숫자·공백·키보드 기호(ASCII)만 쓸 수 있습니다(받은 값: %s)." % (models.active().name_len, name))
    mods = item.get("modules")
    built = {}
    if not isinstance(mods, dict):
        issue("modules 가 없거나 객체가 아닙니다.")
    else:
        known = {_norm(m): i for i, m in enumerate(catalog.MODULES)}
        specs = {}
        for k, v in mods.items():
            i = known.get(_norm(str(k)))
            if i is None:
                issue("알 수 없는 모듈 키입니다(모듈: %s)." % ", ".join(catalog.MODULES), str(k))
            elif i in specs:
                issue("같은 모듈이 두 번 적혔습니다.", catalog.MODULES[i])
            else:
                specs[i] = v
        for i in range(codec.MODULE_COUNT):
            if i not in specs:
                warn("모듈이 없어 OFF·첫 모델·기본값으로 채웁니다.", catalog.MODULES[i])
                built[str(i)] = _default_module(i)
                continue
            m = _build_module(i, specs[i], lambda r, mo="", kn="": issue(r, mo, kn),
                              lambda r, mo="", kn="": warn(r, mo, kn))
            if m is not None:
                built[str(i)] = m
    exp = dict(DEFAULT_EXP)
    if "exp" in item:
        e = item["exp"]
        if not isinstance(e, dict) or any(not _is_int(e.get(k)) or not 0 <= e.get(k) <= 0xFFFF for k in codec.EXP_ORDER):
            issue("exp 는 %s 정수(0~65535)를 모두 가진 객체여야 합니다." % ", ".join(codec.EXP_ORDER))
        else:
            exp = {k: e[k] for k in codec.EXP_ORDER}
    if len(errors) > n0 or slot is None:
        return None
    dev_name = name.rstrip()   # D3: 장비 이름 = name 그대로(슬롯 번호는 장비가 따로 표시한다)
    mo = {"Exp": exp, "effectModule": built, "fileInfo": dict(models.active().file_info, preset_name=dev_name)}
    s = lambda k: item[k] if isinstance(item.get(k), str) else ""
    return {"slot": slot, "label": P.slot_label(slot), "name": dev_name, "mo": mo,
            "meta": {"song": s("song"), "artist": s("artist"), "role": s("role")}}


def _load_one(path: Path, presets: list, errors: list, warnings: list, seen: dict, titles: list):
    fname = path.name
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as ex:
        errors.append(Issue("JSON 을 읽을 수 없습니다: %s" % ex, fname))
        return
    if not isinstance(doc, dict) or doc.get("format") not in FORMATS:
        errors.append(Issue('format 이 "%s" 가 아닙니다.' % FORMATS[0], fname))
        return
    if "device" in doc:
        src = models.by_key(doc["device"])
        if src is None:
            errors.append(Issue("device 는 %s 중 하나여야 합니다(받은 값: %r)." % (" 또는 ".join(p.key for p in models.PROFILES), doc["device"]), fname))
            return
        if src is not models.active():
            warnings.append(Issue("이 톤 JSON 은 %s 용이고 변환 대상은 %s 입니다. 대상 기종에 없는 모델·긴 이름은 오류로 표시됩니다."
                                  % (src.label, models.active().label), fname))
    if not _is_int(doc.get("version")) or doc["version"] != VERSION:
        errors.append(Issue("version 이 %d 이 아닙니다(받은 값: %r)." % (VERSION, doc.get("version")), fname))
        return
    items = doc.get("presets")
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        errors.append(Issue("presets 는 1~200개의 목록이어야 합니다.", fname))
        return
    if isinstance(doc.get("title"), str):
        titles.append(doc["title"])
    for item in items:
        p = _build_preset(item, fname, errors, warnings, seen)
        if p:
            presets.append(p)


def load_tonepacks(paths, profile=None):
    """여러 톤 JSON 을 하나로 합쳐 검사·변환한다. -> (presets, errors, warnings). 파일은 만들지 않는다."""
    with models.using(profile):
        return _load(paths)[:3]


def peek_device(paths):
    """톤 JSON 첫 파일의 device 키가 가리키는 기종(없거나 읽을 수 없거나 모르는 값이면 None)."""
    try:
        doc = json.loads(Path(paths[0]).read_text(encoding="utf-8-sig"))
        return models.by_key(doc["device"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def _load(paths):
    presets, errors, warnings, seen, titles = [], [], [], {}, []
    for p in paths:
        _load_one(Path(p), presets, errors, warnings, seen, titles)
    return presets, errors, warnings, titles


def _unique_dir(base: Path, stamp: str) -> Path:
    """같은 이름의 폴더가 있으면 시각(필요하면 번호)을 붙여 새 이름을 쓴다(덮어쓰지 않음)."""
    if not base.exists():
        return base
    cand = base.with_name("%s_%s" % (base.name, stamp))
    n = 2
    while cand.exists():
        cand = base.with_name("%s_%s-%d" % (base.name, stamp, n))
        n += 1
    return cand


_rename = os.rename  # 시험에서 바꿔 끼울 수 있게 변수로 둔다


def _publish(partial: Path, out: Path, tries=15, delay=0.3):
    """.partial -> 최종 이름. Windows 에서는 방금 쓴 폴더를 백신·색인 프로그램이 잠깐 붙잡아 이름 바꾸기가
    PermissionError(WinError 5/32)로 가끔 실패하므로(시험에서 실제로 관찰) 잠시 기다리며 다시 시도한다."""
    for i in range(tries):
        try:
            _rename(partial, out)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def _remove_partial(partial: Path):
    """실패한 .partial 폴더를 지운다. 지우지 못하면 그 경로를 돌려준다(오류 문구에 적기 위해)."""
    try:
        shutil.rmtree(partial)
        return ""
    except OSError:
        return str(partial)


def convert(paths, out_dir=None, now=None, profile=None) -> ConvertResult:
    """검사 통과 시 .mo 폴더를 만든다(profile = 대상 기종, None 이면 활성 기종).

    B03: 먼저 '<출력 폴더>.partial'에 쓰고, 이중 검사까지 통과해야 최종 이름으로 바꾼다. 검사 실패·파일 오류(OSError)는
    예외를 던지지 않고 ConvertResult 오류로 돌려주며 .partial 을 지운다(실패한 출력이 정상 결과 폴더로 남지 않게).
    """
    with models.using(profile):
        return _convert(paths, out_dir, now)


def _convert(paths, out_dir, now) -> ConvertResult:
    paths = [Path(p) for p in paths]
    res = ConvertResult()
    presets, res.errors, res.warnings, titles = _load(paths)
    res.presets = presets
    if res.errors:
        return res
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = Path(out_dir) if out_dir else paths[0].parent / ("%s_mo_%s" % (paths[0].stem, stamp))
    out = _unique_dir(base, stamp)
    partial = out.with_name(out.name + ".partial")
    n = 2
    while partial.exists():  # 이전에 지우지 못하고 남은 .partial 과 겹치지 않게
        partial = out.with_name("%s.partial-%d" % (out.name, n))
        n += 1
    names = []
    try:
        partial.mkdir(parents=True)
        for p in sorted(presets, key=lambda x: x["slot"]):
            f = mofile.write_mo(partial, p["slot"], p["mo"])
            # 이중 확인: 만든 .mo 를 기존 .mo 검사에 다시 통과시킨다
            chk = mofile.read_mo(f)
            if chk.errors or chk.slot != p["slot"] or chk.mo != p["mo"]:
                res.errors.append(Issue("만든 .mo 검사 실패: %s" % " / ".join(chk.errors or ["내용 불일치"]), f.name, p["label"]))
            names.append(f.name)
        meta = {"format": "GE150PRO-TONEPACK-META", "device": models.active().key, "title": titles[0] if titles else "",
                "sources": [x.name for x in paths],
                "presets": {p["label"]: dict(p["meta"], name=p["name"]) for p in presets}}
        (partial / META_NAME).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if not res.errors:
            _publish(partial, out)
    except OSError as ex:
        res.errors.append(Issue("출력 파일을 만들 수 없습니다: %s" % ex, "", ""))
    if res.errors:
        left = _remove_partial(partial) if partial.exists() else ""
        if left:
            res.errors.append(Issue("임시 폴더를 지우지 못했습니다. 직접 지워 주십시오: %s" % left))
        return res
    res.files = [out / x for x in names]
    res.out_dir = out
    return res


def load_meta(folder) -> dict:
    """변환 결과 폴더의 tonepack-meta.json -> {슬롯 번호: {song, artist, role, name}}. 없거나 깨졌으면 빈 사전."""
    try:
        doc = json.loads((Path(folder) / META_NAME).read_text(encoding="utf-8"))
        return {P.parse_slot(k): v for k, v in doc["presets"].items() if P.parse_slot(k)}
    except (OSError, ValueError, KeyError, AttributeError):
        return {}
