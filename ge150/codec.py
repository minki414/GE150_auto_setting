"""장비 프리셋 바이트 <-> .mo JSON(dict) 변환. 배치는 기종(models.Profile)이 정한다.

Pro 244바이트 = 이름 16B + 모듈 9개 x 24B(SWITCH, TYPE, P0..P9 u16 LE) + EXP 6 x u16.
GE150 158바이트 = 이름 14B + 모듈별 가변 칸(SWITCH, TYPE, P0.. 칸 수 6·5·8·7·5·11·9·9·6) + EXP 6 x u16.
읽기 응답이 write_size 보다 길면(GE150 162) 앞 write_size 바이트만 의미가 있다.
"""
from . import catalog, models

MODULE_COUNT = 9

# EXP 6값 배치(잠정). 위치 0·1·5 는 확정, 2·3·4 는 장비 시험 뒤 확정 -> 바뀌면 여기 한 곳만 고친다.
EXP_ORDER = ["MODULE_CTRL", "PARA_CTRL", "FUN_SWITCH", "VOL_SWITCH", "VOL_MIN", "VOL_MAX"]


class CodecError(ValueError):
    pass


def _u16(b, o):
    return int.from_bytes(b[o:o + 2], "little")


def decode_name(data: bytes, profile=None) -> str:
    p = profile or models.active()
    return bytes(data[:p.name_len]).split(b"\x00", 1)[0].decode("ascii", "replace").rstrip()


def _nparams(p, i, ps):
    """모듈 i 의 P 칸 수 = 그 기종 칸 수 - 2(SWITCH, TYPE). 모델 노브가 더 많으면 목록과 배치가 어긋난 것이다."""
    room = p.module_words[i] - 2
    if len(ps) > room:
        raise CodecError("%s 모듈 모델의 노브 %d개가 칸 수(%d)를 넘습니다." % (catalog.MODULES[i], len(ps), room))
    return room


def decode(data: bytes, profile=None) -> dict:
    """장비 바이트 -> .mo dict. 모델이 쓰지 않는 P칸은 버린다(읽을 때 무시)."""
    p = profile or models.active()
    if len(data) not in (p.read_size, p.write_size):
        raise CodecError("프리셋 데이터는 %s바이트여야 합니다." % " 또는 ".join(str(n) for n in dict.fromkeys((p.write_size, p.read_size))))
    data = bytes(data[:p.write_size])
    mods = {}
    for i, o in enumerate(p.module_offsets):
        sw, ty = _u16(data, o), _u16(data, o + 2)
        ps = catalog.params(i, ty, p)
        if not ps:
            raise CodecError("%s 모듈의 모델 번호 %d 을(를) 알 수 없습니다." % (catalog.MODULES[i], ty))
        _nparams(p, i, ps)
        mods[str(i)] = {
            "Data": {q["name"]: _u16(data, o + 4 + 2 * k) for k, q in enumerate(ps)},
            "SWITCH": sw,
            "TYPE": ty,
        }
    exp = {k: _u16(data, p.exp_offset + 2 * j) for j, k in enumerate(EXP_ORDER)}
    return {
        "Exp": exp,
        "effectModule": mods,
        "fileInfo": dict(p.file_info, preset_name=decode_name(data, p)),
    }


def encode(mo: dict, profile=None) -> bytes:
    """.mo dict -> 장비 바이트(write_size). 모델이 쓰지 않는 P칸은 0 으로 쓴다(쓸 때)."""
    p = profile or models.active()
    name = mo["fileInfo"]["preset_name"].rstrip()
    try:
        raw = name.encode("ascii")
    except UnicodeEncodeError:
        raise CodecError("이름은 영문 ASCII 만 쓸 수 있습니다.")
    if len(raw) > p.name_len:
        raise CodecError("이름이 %d자를 넘습니다." % p.name_len)
    out = bytearray(raw.ljust(p.name_len, b"\x00"))
    for i in range(MODULE_COUNT):
        m = mo["effectModule"][str(i)]
        ps = catalog.params(i, m["TYPE"], p)
        if not ps:
            raise CodecError("%s 모듈의 모델 번호 %s 을(를) 알 수 없습니다." % (catalog.MODULES[i], m["TYPE"]))
        room = _nparams(p, i, ps)
        vals = [m["Data"][q["name"]] for q in ps] + [0] * (room - len(ps))
        for v in [m["SWITCH"], m["TYPE"]] + vals:
            out += int(v).to_bytes(2, "little")
    for k in EXP_ORDER:
        out += int(mo["Exp"][k]).to_bytes(2, "little")
    return bytes(out)


def normalize(data: bytes, profile=None) -> bytes:
    """쓰이지 않는 P칸을 0 으로 만든 사본 — 두 프리셋이 '실질적으로 같은지' 비교할 때 쓴다."""
    return encode(decode(data, profile), profile)


def same_preset(a: bytes, b: bytes, profile=None) -> bool:
    p = profile or models.active()
    try:
        return normalize(a, p) == normalize(b, p)
    except (CodecError, KeyError):
        return bytes(a[:p.write_size]) == bytes(b[:p.write_size])
