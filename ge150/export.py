"""장비 200개 -> 톤 JSON(GE150-TONEPACK v1) 내보내기. 결과는 톤 JSON 형식과 같아서 그대로 다시 가져올 수 있습니다.

device_to_doc(data, profile, title, songs=None, warnings=None) -> dict   장비 바이트 {슬롯: bytes} -> 톤 JSON 문서
parse_song_list(text) -> {뱅크: (아티스트, 곡)}                           곡 목록 글 -> 프리셋의 song·artist·role 에 넣을 자료
write_doc(path, doc, overwrite=False)                                    파일로 저장(기본은 덮어쓰지 않음)

가져올 때 오류가 되는 프리셋(사용자 샘플 칸 EMPTY, PITCH 계열 모델, 표현할 수 없는 값)도 내용 그대로 내보내고
warnings 목록에 알려 줍니다. 장비에 있는 것을 숨기거나 바꾸지 않기 위해서입니다.
"""
import json
import re
from pathlib import Path

from . import catalog, codec
from . import protocol as P
from .tonepack import DEFAULT_EXP, FORMATS, VERSION, _norm

ROLES = ("CLEAN", "CRUNCH", "DRIVE", "SOLO")   # 슬롯 A~D 역할(1곡 = 1뱅크 = 4슬롯)
_SONG_RE = re.compile(r"^\s*(\d{1,2})\s+(\S.*?)\s*$")
_HINT_RE = re.compile(r"\s{2,}\([^()]*\)\s*$")   # 샘플 곡 목록 줄 끝의 장비 이름 표기 "  (GoBack)"


def _display(p: dict, raw: int):
    """raw -> 톤 JSON 표시값(목록형은 글자, 음수 범위는 raw+min, 그 밖은 raw). 표현할 수 없으면 (raw, 사유)."""
    if p["type"] == 1:
        opts = p["options"]
        if 0 <= raw < len(opts):
            return opts[raw], None
        return raw, "목록 번호 %d 은(는) 선택지에 없습니다." % raw
    lo, hi = catalog.raw_range(p)
    val = int(p["min"]) + raw if p["min"] < 0 else raw
    if not lo <= raw <= hi:
        return val, "값 %d 이(가) 범위를 벗어났습니다." % val
    return val, None


def _item(slot: int, mo: dict, profile, meta=None):
    """프리셋 1개 -> (톤 JSON 항목, 경고 목록). meta(song·artist·role)는 읽기 좋게 name 바로 뒤에 둔다."""
    label, warns = P.slot_label(slot), []
    mods = {}
    for i in range(codec.MODULE_COUNT):
        m = mo["effectModule"][str(i)]
        spec = catalog.model(i, m["TYPE"], profile)
        name = spec["name"]
        if _norm(name) == "empty":
            warns.append("%s · %s · 사용자 샘플 칸(EMPTY)은 톤 JSON 으로 가져올 때 오류가 됩니다." % (label, catalog.MODULES[i]))
        elif any(p["type"] == 0 and p["step"] != 1 for p in spec["params"]):
            warns.append("%s · %s · 모델 %s 은(는) PITCH 값 규칙이 확정되지 않아 가져올 때 오류가 됩니다." % (label, catalog.MODULES[i], name))
        params = {}
        for p in spec["params"]:
            val, why = _display(p, m["Data"][p["name"]])
            if why:
                warns.append("%s · %s · %s · %s 가져올 때 오류가 됩니다." % (label, catalog.MODULES[i], p["name"], why))
            params[p["name"]] = val
        mods[catalog.MODULES[i]] = {"on": bool(m["SWITCH"]), "model": name, "params": params}
    item = {"slot": label, "name": mo["fileInfo"]["preset_name"], **(meta or {}), "modules": mods}
    if mo["Exp"] != DEFAULT_EXP:   # 기본값(볼륨 페달)과 다를 때만 적는다 — 톤 JSON 은 exp 를 생략하면 기본값
        item["exp"] = {k: mo["Exp"][k] for k in codec.EXP_ORDER}
    return item, warns


def preset_to_item(slot: int, mo: dict, profile) -> dict:
    """톤 JSON 프리셋 1개: slot, name, modules(9개 전부 on/model/params). exp 는 기본값과 다를 때만."""
    return _item(slot, mo, profile)[0]


def device_to_doc(data: dict, profile, title: str, songs=None, warnings=None) -> dict:
    """장비 {슬롯: bytes} -> 톤 JSON 문서. songs = {뱅크: (아티스트, 곡)} 이면 song·artist·role 을 넣습니다.
    warnings 목록을 주면 가져올 때 오류가 될 내용·해석하지 못해 빠진 슬롯을 거기에 덧붙입니다."""
    presets = []
    for slot in sorted(data):
        try:
            mo = codec.decode(data[slot], profile)
        except (codec.CodecError, ValueError, KeyError) as ex:
            if warnings is not None:
                warnings.append("%s · 해석할 수 없어 내보내지 않았습니다: %s" % (P.slot_label(slot), ex))
            continue
        bank, meta = (slot - 1) // 4 + 1, None
        if songs and bank in songs:
            meta = {"song": songs[bank][1], "artist": songs[bank][0], "role": ROLES[(slot - 1) % 4]}
        item, warns = _item(slot, mo, profile, meta)
        if warnings is not None:
            warnings.extend(warns)
        presets.append(item)
    return {"format": FORMATS[0], "version": VERSION, "device": profile.key, "title": title, "presets": presets}


def parse_song_list(text: str) -> dict:
    """곡 목록 글 -> {뱅크 번호: (아티스트, 곡)}. 줄 형식은 `NN 아티스트 - 곡명`.
    ' - ' 가 없는 줄(`NN 곡명 (설명)`)은 아티스트 없이 줄 전체를 곡으로 봅니다. 번호로 시작하지 않는 줄은 무시합니다.
    줄 끝의 공백 2칸 이상 + 괄호(`  (GoBack)`)는 샘플 목록에 덧붙인 장비 이름 표기라서 곡명에서 뺍니다."""
    out = {}
    for line in text.splitlines():
        m = _SONG_RE.match(line)
        if not m or not 1 <= int(m.group(1)) <= 50:
            continue
        rest = _HINT_RE.sub("", m.group(2)).strip()
        artist, sep, song = rest.partition(" - ")
        out[int(m.group(1))] = (artist.strip(), song.strip()) if sep else ("", rest)
    return out


def dumps(doc: dict) -> str:
    """사람이 읽기 쉬운 JSON 글: 프리셋마다 칸을 나누고 모듈은 한 줄씩(들여쓰기 전체를 쓰면 줄이 3만 줄에 가깝다)."""
    one = lambda v: json.dumps(v, ensure_ascii=False)   # noqa: E731
    presets = doc.get("presets")
    if not isinstance(presets, list) or not all(isinstance(it, dict) and isinstance(it.get("modules"), dict) for it in presets):
        return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"   # 틀이 맞지 않는(검사에서 오류가 될) 문서는 그대로 저장
    head = ["%s: %s," % (one(k), one(v)) for k, v in doc.items() if k != "presets"]
    blocks = []
    for it in doc["presets"]:
        lines = ["   %s: %s," % (one(k), one(v)) for k, v in it.items() if k not in ("modules", "exp")]
        mods = ",\n".join("    %s: %s" % (one(k), one(v)) for k, v in it["modules"].items())
        lines.append('   "modules": {\n%s\n   }%s' % (mods, "," if "exp" in it else ""))
        if "exp" in it:
            lines.append('   "exp": ' + one(it["exp"]))
        blocks.append("  {\n" + "\n".join(lines) + "\n  }")
    return "{\n " + "\n ".join(head) + '\n "presets": [\n' + ",\n".join(blocks) + "\n ]\n}\n"


def write_doc(path, doc: dict, overwrite=False) -> Path:
    """톤 JSON 문서를 UTF-8 로 저장한다. 같은 이름의 파일이 있으면 overwrite 가 아닌 한 FileExistsError."""
    path = Path(path)
    with open(path, "w" if overwrite else "x", encoding="utf-8", newline="\n") as f:
        f.write(dumps(doc))
    return path


# 개정 기록 | 2026-11-12 | 신설: 장비 -> 톤 JSON 내보내기, 곡 목록 읽기 | H단계
