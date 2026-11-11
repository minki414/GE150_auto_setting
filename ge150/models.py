"""기종(GE150 Pro / GE150)별 차이를 한 곳에 모은 표.

바이트 배치·이름 길이·모델 목록 파일·쓰기 전 명령·.mo 머리말·사용자 샘플 칸이 기종마다 다르다.
다른 모듈은 인자로 기종을 받지 않으면 활성 기종(active)을 쓴다 — 앱이 장비를 읽은 뒤 set_active 로 바꾼다.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field

# 모듈 배치는 칸 수(module_words) 하나로 적고 바이트 위치는 거기서 계산한다 — 두 기종을 같은 코덱으로 처리하기 위해서다.


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    catalog_file: str
    name_len: int
    module_words: tuple            # 모듈별 u16 칸 수(SW, TYPE, P0…), 순서 FX…REVERB
    write_size: int
    read_size: int
    pre_write: bytes | None
    file_info: dict = field(compare=False)
    sample_slots: tuple = ()       # (모듈 번호, 이름, 첫 TYPE, 사용자 샘플 이름 목록 안의 시작 위치 또는 None)
    note: str = ""                 # 화면에 덧붙일 안내(실장비 미검증 기종)

    def __post_init__(self):
        if self.exp_offset + 12 != self.write_size:
            raise ValueError("%s: 배치 합계(%d)가 write_size(%d)와 다릅니다." % (self.key, self.exp_offset + 12, self.write_size))

    @property
    def module_offsets(self) -> tuple:
        """모듈별 시작 바이트 위치 = 이름 길이 + 2 x 앞 모듈 칸 수 합."""
        out, o = [], self.name_len
        for w in self.module_words:
            out.append(o)
            o += 2 * w
        return tuple(out)

    @property
    def exp_offset(self) -> int:
        return self.name_len + 2 * sum(self.module_words)

    @property
    def has_sample_names(self) -> bool:
        return any(s[3] is not None for s in self.sample_slots)


PRO = Profile(
    key="GE150PRO", label="GE150 Pro", catalog_file="catalog.json", name_len=16,
    module_words=(12,) * 9, write_size=244, read_size=244, pre_write=bytes.fromhex("b40500"),
    file_info={"app": "GE150PRO Edit", "app_version": "V1.0.1", "device": "GE150PRO",
               "device_version": "V1.0.0", "schema": "GE150PRO Preset"},
    sample_slots=((2, "AMP(NAM/GNR)", 55, 0), (3, "CAB(IR)", 26, 20)),
)

GE150 = Profile(
    key="GE150", label="GE150", catalog_file="catalog-ge150.json", name_len=14,
    module_words=(6, 5, 8, 7, 5, 11, 9, 9, 6), write_size=158, read_size=162, pre_write=None,
    file_info={"app": "GE150 Edit", "app_version": "V1.3.0", "device": "MOOER GE150",
               "device_version": "V1.3.0", "schema": "GE150 Preset"},
    sample_slots=((3, "CAB(IR)", 26, None),),
    note="실장비 시험 전 기종입니다. 처음에는 한 슬롯만 적용해 확인해 주십시오.",
)

PROFILES = (PRO, GE150)
_active = PRO

# 노브 이름 별칭: 같은 노브를 기종마다 다르게 부른다(GE150 FEEDBACK = Pro F.BACK, GE150 PRE DELAY = Pro P.DELAY).
PARAM_ALIASES = {"F.BACK": "FEEDBACK", "P.DELAY": "PRE DELAY"}


def param_alias(name: str):
    """노브 이름의 다른 기종 쪽 이름(양방향). 별칭이 없으면 None."""
    up = str(name).strip().upper()
    for a, b in PARAM_ALIASES.items():
        if up == a:
            return b
        if up == b:
            return a
    return None


def active() -> Profile:
    return _active


def set_active(p: Profile):
    global _active
    _active = p


@contextmanager
def using(p):
    """with 블록 안에서만 기종을 바꾼다(None 이면 그대로). 다른 기종 기준으로 잠깐 계산할 때 쓴다."""
    global _active
    prev = _active
    if p is not None:
        _active = p
    try:
        yield
    finally:
        _active = prev


def by_key(key):
    k = str(key).strip().upper() if isinstance(key, str) else ""
    return next((p for p in PROFILES if p.key == k), None)


def from_read_size(n):
    return next((p for p in PROFILES if p.read_size == n), None)


def from_file_info(info):
    """.mo fileInfo 의 device 로 기종을 알아낸다. 없거나 모르는 값이면 None."""
    dev = info.get("device") if isinstance(info, dict) else None
    return next((p for p in PROFILES if p.file_info["device"] == dev), None)
