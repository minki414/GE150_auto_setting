"""톤 데이터(.mo) 폴더 읽기, 슬롯 판정, 검사, 쓰기."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog, codec, models
from .protocol import parse_slot, slot_label

# 이름에 허용할 문자(장비 시험 뒤 좁힐 예정 — 지금은 인쇄 가능한 ASCII 전부). 바꾸려면 이 상수만 고친다.
ALLOWED_NAME_CHARS = frozenset(chr(c) for c in range(0x20, 0x7F))
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')   # Windows 파일 이름에 못 쓰는 문자 + 제어 문자


@dataclass
class MoEntry:
    path: Path
    slot: int | None            # 파일 이름 앞 3글자로 정한 슬롯(없으면 None = 슬롯 미지정)
    mo: dict | None = None      # 읽은 JSON(형식 오류면 None)
    name: str = ""              # 장비에 표시될 이름(preset_name, 뒤 공백 제거)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def slot_from_filename(filename: str):
    """파일 이름 앞 3글자 -> 슬롯(1~200). 대소문자 무시. 형식이 아니면 None."""
    return parse_slot(filename[:3])


def validate_name(name: str) -> list:
    errs = []
    n = models.active().name_len   # 장비 화면에 들어가는 이름 길이는 기종마다 다르다
    if len(name) > n:
        errs.append("이름이 %d자를 넘습니다." % n)
    bad = sorted({c for c in name if c not in ALLOWED_NAME_CHARS})
    if bad:
        errs.append("이름에 쓸 수 없는 문자가 있습니다: %s" % "".join(bad))
    return errs


def validate_mo(mo) -> list:
    """구조·모델 번호·노브 범위·이름을 검사해 오류 사유 목록을 돌려준다(없으면 빈 목록)."""
    errs = []
    if not isinstance(mo, dict):
        return ["파일 형식이 올바르지 않습니다."]
    info = mo.get("fileInfo")
    other = models.from_file_info(info)   # device 가 없거나 모르는 값이면 통과(옛 파일·다른 도구가 만든 파일)
    if other is not None and other is not models.active():
        # 모델 번호·바이트 배치가 달라 나머지 검사는 의미가 없다
        return ["다른 기종(%s)용 .mo 입니다. 현재 기종은 %s 입니다." % (other.label, models.active().label)]
    if not isinstance(info, dict) or not isinstance(info.get("preset_name"), str):
        errs.append("fileInfo.preset_name 이 없습니다.")
    else:
        errs += validate_name(info["preset_name"].rstrip())
    exp = mo.get("Exp")
    if not isinstance(exp, dict):
        errs.append("Exp 항목이 없습니다.")
    else:
        for k in codec.EXP_ORDER:
            v = exp.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 0xFFFF:
                errs.append("Exp.%s 값이 올바르지 않습니다." % k)
    em = mo.get("effectModule")
    if not isinstance(em, dict):
        return errs + ["effectModule 항목이 없습니다."]
    for i in range(codec.MODULE_COUNT):
        label = catalog.MODULES[i]
        m = em.get(str(i))
        if not isinstance(m, dict):
            errs.append("%s 모듈이 없습니다." % label)
            continue
        ty, sw, data = m.get("TYPE"), m.get("SWITCH"), m.get("Data")
        if sw not in (0, 1) or isinstance(sw, bool):
            errs.append("%s SWITCH 값이 0 또는 1이 아닙니다." % label)
        if isinstance(ty, bool) or not isinstance(ty, int) or catalog.model(i, ty) is None:
            errs.append("%s 모델 번호 %s 이(가) 범위를 벗어났습니다(0~%d)." % (label, ty, catalog.model_count(i) - 1))
            continue
        if not isinstance(data, dict):
            errs.append("%s Data 가 없습니다." % label)
            continue
        names = [p["name"] for p in catalog.params(i, ty)]
        for n in names:
            if n not in data:
                errs.append("%s 노브 %s 값이 없습니다." % (label, n))
            else:
                e = catalog.check_value(i, ty, n, data[n])
                if e:
                    errs.append("%s: %s" % (label, e))
        for n in data:
            if n not in names:
                errs.append("%s 모델(%s)에 없는 노브 %s 가 있습니다." % (label, catalog.model_name(i, ty), n))
    return errs


def sample_warnings(mo: dict, user_names=None) -> list:
    """사용자 샘플 칸 사용 경고. user_names(Pro 40개: 앞20=AMP, 뒤20=CAB IR 가정)가 있고 EMPTY 이면 강한 경고.
    GE150 은 이름 목록을 읽지 않으므로(칸 위치 None) 칸 사용 경고만 한다."""
    out = []
    for mod, kind, base, off in models.active().sample_slots:
        ty = mo["effectModule"][str(mod)]["TYPE"]
        if catalog.is_user_sample(mod, ty):
            idx = ty - base
            msg = "%s 사용자 샘플 칸 사용(%d번)" % (kind, idx + 1)
            if off is not None and user_names is not None and len(user_names) >= off + idx + 1 and user_names[off + idx].upper() == "EMPTY":
                msg += " — 장비 칸이 비어 있어 의도와 다른 소리가 납니다"
            out.append(msg)
    return out


def read_mo(path, user_names=None) -> MoEntry:
    """파일 1개를 읽고 검사한다. 읽기 실패·형식 오류는 errors 에 담고 예외를 던지지 않는다."""
    path = Path(path)
    e = MoEntry(path=path, slot=slot_from_filename(path.name))
    try:
        e.mo = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as ex:
        e.errors.append("JSON 형식 오류: %s" % ex)
        return e
    e.errors += validate_mo(e.mo)
    if isinstance(e.mo, dict) and isinstance(e.mo.get("fileInfo"), dict):
        e.name = str(e.mo["fileInfo"].get("preset_name", "")).rstrip()
    if not e.errors:
        e.warnings += sample_warnings(e.mo, user_names)
    return e


def mark_duplicates(entries):
    """같은 슬롯 파일이 2개 이상이면 모두 오류 처리한다(슬롯을 다시 지정한 뒤에도 다시 호출 가능)."""
    by_slot = {}
    for e in entries:
        e.errors = [x for x in e.errors if not x.startswith("같은 슬롯")]
        if e.slot:
            by_slot.setdefault(e.slot, []).append(e)
    for slot, group in by_slot.items():
        if len(group) > 1:
            for e in group:
                e.errors.append("같은 슬롯(%s) 파일이 %d개입니다." % (slot_label(slot), len(group)))


def scan_folder(folder, user_names=None) -> list:
    """폴더의 .mo 를 모두 읽는다(하위 폴더 제외)."""
    entries = [read_mo(p, user_names) for p in sorted(Path(folder).iterdir(), key=lambda x: x.name.lower())
               if p.is_file() and p.suffix.lower() == ".mo"]
    mark_duplicates(entries)
    return entries


def safe_filename(text: str) -> str:
    """D2: Windows 파일 이름에 못 쓰는 문자(\\ / : * ? " < > | 와 제어 문자)와 끝의 공백·마침표를 모두 '_' 로 바꾼다.

    (실장비 이름 '<>\\^`{|} Name 16' 의 '\\' 를 경로로 해석해 백업이 실패하고 쓰기 전체가 막힌 일이 있었다.)
    """
    t = _BAD_FILENAME.sub("_", text)
    return re.sub(r"[ .]+$", lambda m: "_" * len(m.group()), t)


def mo_filename(slot: int, name: str) -> str:
    """'<슬롯> <이름>.mo' (D2 규칙으로 정리). D3: 장비 이름에 슬롯 번호를 넣지 않으므로 슬롯 표기는 항상 앞에 붙는다."""
    return safe_filename("%s %s" % (slot_label(slot), name.rstrip())) + ".mo"


def unique_path(folder, filename: str) -> Path:
    """같은 파일 이름이 이미 있으면 ' (2)', ' (3)' … 번호를 붙인다."""
    p = Path(folder) / filename
    n = 2
    while p.exists():
        p = Path(folder) / ("%s (%d)%s" % (Path(filename).stem, n, Path(filename).suffix))
        n += 1
    return p


def dumps_mo(mo: dict) -> str:
    """에디터와 같은 형식: indent=4, 키 정렬, 한글 그대로, 끝에 줄바꿈."""
    return json.dumps(mo, indent=4, sort_keys=True, ensure_ascii=False) + "\n"


def write_mo(folder, slot: int, mo: dict, unique: bool = False) -> Path:
    """.mo 를 쓴다. unique=True 이면 같은 이름의 파일을 덮어쓰지 않고 번호를 붙인다."""
    name = mo_filename(slot, mo["fileInfo"]["preset_name"])
    path = unique_path(folder, name) if unique else Path(folder) / name
    path.write_text(dumps_mo(mo), encoding="utf-8", newline="\n")
    return path
