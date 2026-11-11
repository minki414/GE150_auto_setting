"""장비 접근: Device(실장비, hidapi)와 FakeDevice(녹화 자료 기반, 쓰기는 메모리에만).

두 클래스는 같은 인터페이스를 가진다:
connect() / read_all() -> {slot: bytes} / write_slot(slot, bytes) -> bool / user_samples() / close(), 읽은 뒤의 profile(기종)
"""
import re
import subprocess
import sys
import time
from pathlib import Path

from . import catalog, codec, models
from . import protocol as P

VID, PIDS = 0x0483, (0x5703, 0x5750)   # GE150 Pro 는 5703(HID 는 MI_05 하나), GE150 은 에디터 분석상 5703 또는 5750
# 공식 에디터 실행 파일: Pro = GE150PRO.exe, GE150 = GE150.exe(실행기) → MOOER_GE150.exe(본체)
EDITOR_PROCESSES = ("GE150PRO.exe", "GE150.exe", "MOOER_GE150.exe")
READ_TIMEOUT = 15.0   # 전체 읽기 제한(초)
WRITE_ACK_TIMEOUT = 3.0  # 슬롯 쓰기 응답 대기(초)


class DeviceError(RuntimeError):
    pass


EDITOR_NONE, EDITOR_RUNNING, EDITOR_UNKNOWN = "none", "running", "unknown"


def editor_state() -> str:
    """공식 에디터(GE150 Pro·GE150) 실행 상태: 없음 / 실행 중 / 확인 불가.

    A05: tasklist 오류·시간 초과는 '없음'이 아니라 '확인 불가'로 본다(USB 동시 사용 충돌 방지가 끊기지 않게).
    tasklist /FI 는 이름 하나만 거를 수 있어 전체 목록을 받아 이름을 대조한다.
    """
    if sys.platform != "win32":
        return EDITOR_NONE
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           errors="replace", timeout=10, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return EDITOR_UNKNOWN
    if r.returncode != 0:
        return EDITOR_UNKNOWN
    first = {ln.split(",")[0].strip('"').lower() for ln in r.stdout.splitlines() if ln.strip()}
    return EDITOR_RUNNING if first & {p.lower() for p in EDITOR_PROCESSES} else EDITOR_NONE


def editor_running() -> bool:
    return editor_state() == EDITOR_RUNNING


def editor_blocked_reason():
    """연결·적용을 막아야 하면 안내 문구, 아니면 None. 연결 때와 적용(쓰기) 직전에 모두 부른다."""
    st = editor_state()
    if st == EDITOR_RUNNING:
        return "공식 에디터를 닫아 주십시오. USB 를 동시에 쓰면 충돌합니다."
    if st == EDITOR_UNKNOWN:
        return "공식 에디터 실행 여부를 확인할 수 없어 연결하지 않습니다. 에디터를 닫고 다시 시도해 주십시오."
    return None


def split_user_names(body: bytes) -> list:
    """C1 01 응답 본문(41 + 16B x 40) -> 이름 40개. 앞 20 = AMP 사용자 칸, 뒤 20 = CAB IR 칸(가정, 장비 확인 필요)."""
    raw = body[1:]
    return [raw[i * 16:(i + 1) * 16].split(b"\x00", 1)[0].decode("ascii", "replace") for i in range(len(raw) // 16)]


MULTI_DEVICE_MSG = "같은 종류의 장비가 여러 대 연결되어 있습니다. 한 대만 연결해 주십시오."
_COL_RE = re.compile(rb"&col\d+|&\d{4}(?=#\{)", re.IGNORECASE)   # 컬렉션 표기와 컬렉션마다 다른 끝 번호(&0000·&0001)


def _iface_key(d: dict) -> bytes:
    """HID 경로에서 컬렉션 표기(&Col01 등)와 컬렉션 끝 번호를 지워 'USB 인터페이스 1개'를 가리키는 열쇠로 만든다."""
    p = d.get("path") or b""
    return _COL_RE.sub(b"", p if isinstance(p, bytes) else str(p).encode()).lower()


def pick_interface(infos: list) -> dict:
    """쓸 HID 인터페이스 1개. 장비 1대는 HID 인터페이스를 1개만 내놓는다(Pro 실측: MI_05 하나뿐).
    감사 G(2회): 후보 인터페이스가 둘 이상이면 장비가 여러 대이거나 구분할 수 없는 상태이므로, 어느 것도 고르지 않고 거부한다
    (번호·usage_page 로 '그럴듯한 것'을 고르면 다른 장비에 쓸 수 있다)."""
    if not infos:
        raise DeviceError("장비를 찾을 수 없습니다. USB 연결을 확인해 주십시오.")
    groups = {}
    for d in infos:
        groups.setdefault(_iface_key(d), []).append(d)
    if len(groups) > 1:
        raise DeviceError(MULTI_DEVICE_MSG)
    cols = next(iter(groups.values()))
    # 한 인터페이스의 여러 컬렉션 중에서는 제조사 전용(usage_page ≥ 0xFF00)을 우선한다
    return next((d for d in cols if d.get("usage_page", 0) >= 0xFF00), cols[0])


class Device:
    """실장비. hid 모듈은 연결할 때만 불러와서 장비 없이도 import 가능하게 한다."""

    def __init__(self):
        self.h = None
        self.rx = P.FrameParser()
        self.profile = None   # read_all 이 응답 크기로 정한다(244 = Pro, 162 = GE150)

    def connect(self):
        import hid  # noqa: 지연 import (장비 없는 환경·시험 보호)
        self.profile = None   # 새 연결에서는 전체 읽기로 기종이 확정되기 전까지 쓰지 못한다
        found = pick_interface([d for pid in PIDS for d in hid.enumerate(VID, pid)])
        self.rx = P.FrameParser()
        self.h = hid.device()
        try:
            self.h.open_path(found["path"])
            self._send(P.CMD_START)
            time.sleep(0.1)
        except Exception:
            self.close()  # A06: 실패한 연결 핸들을 남기지 않는다
            raise

    def _send(self, body: bytes):
        for rep in P.frame_to_reports(P.build_frame(body)):
            self.h.write(rep)

    def _pump(self, timeout_ms=200) -> bool:
        r = self.h.read(64, timeout_ms)
        if r:
            self.rx.feed(P.report_payload(bytes(r)))
            return True
        return False

    def _drain(self):
        """수신 대기열·HID 버퍼·불완전 프레임을 모두 비운다(이전 요청의 응답이 새 응답으로 오인되지 않게).

        장비가 계속 보내는 경우를 대비해 시간 상한(1초)을 둔다.
        """
        t0 = time.time()
        while time.time() - t0 < 1.0 and self._pump(1):
            pass
        self.rx = P.FrameParser()

    def read_all(self) -> dict:
        self.profile = None   # 읽기가 끝까지 성공해야 기종이 정해진다(실패하면 이전 기종을 남기지 않는다)
        self._drain()
        self._send(P.CMD_READ_ALL)
        got, t0 = {}, time.time()
        sizes = {p.read_size for p in models.PROFILES}
        size = None   # 첫 프레임의 크기가 기준 — 기종 판별은 200개가 모두 같은 크기일 때만
        while time.time() - t0 < READ_TIMEOUT and len(got) < P.SLOT_COUNT:
            self._pump()
            for body, ok in self.rx.frames():
                if not ok or body[0] != P.RSP_PRESET:
                    continue
                slot, data = body[1], body[2:]
                if len(data) not in sizes:
                    raise DeviceError("알 수 없는 프리셋 크기(%d바이트)를 받았습니다." % len(data))
                if size is not None and len(data) != size:
                    raise DeviceError("프리셋 크기가 섞여 있어 기종을 판별할 수 없습니다(%d, %d바이트)." % (size, len(data)))
                if not 1 <= slot <= P.SLOT_COUNT:
                    raise DeviceError("슬롯 번호(%d)가 1~200 범위 밖입니다." % slot)
                if slot in got and got[slot] != data:
                    raise DeviceError("슬롯 %d 의 응답이 서로 다릅니다." % slot)
                size = len(data)
                got[slot] = data
        if len(got) != P.SLOT_COUNT or set(got) != set(range(1, P.SLOT_COUNT + 1)):
            raise DeviceError("프리셋 %d개만 읽었습니다(200개 필요)." % len(got))
        self.profile = models.from_read_size(size)
        return got

    def user_samples(self):
        if self.profile is not None and not self.profile.has_sample_names:
            return None   # GE150: 사용자 샘플 이름 목록을 읽지 않는다(에디터에 해당 명령 없음)
        self._drain()
        self._send(P.CMD_USER_NAMES)
        t0 = time.time()
        while time.time() - t0 < 3.0:
            self._pump()
            for body, ok in self.rx.frames():
                if ok and body[0] == P.RSP_USER_NAMES:
                    return split_user_names(body)
        raise DeviceError("사용자 샘플 이름을 읽지 못했습니다.")

    def _write_once(self, slot: int, data: bytes) -> bool:
        # A04: 과거의 같은 슬롯 ACK(43 <slot>)가 남아 있다가 새 쓰기의 성공으로 오인되지 않게 먼저 비운다(재시도도 동일)
        self._drain()
        prof = self.profile or models.active()
        if prof.pre_write:  # GE150 은 에디터에 이 명령이 없다
            self._send(prof.pre_write)  # 에디터와 같게 쓰기 전에 보냄(필요 여부는 장비 시험 항목)
        self._send(P.write_body(slot, data, prof))
        t0 = time.time()
        while time.time() - t0 < WRITE_ACK_TIMEOUT:
            self._pump()
            for body, ok in self.rx.frames():
                if ok and body == bytes([P.RSP_WRITE_DONE, slot]):
                    return True
        return False

    def write_slot(self, slot: int, data: bytes) -> bool:
        """C3 로 한 슬롯을 쓰고 43 응답(3초)을 기다린다. 실패하면 1회 재시도. 선택 이동(96)은 보내지 않는다."""
        prof = self.profile
        if prof is None:   # 기종이 확정되지 않았으면(연결 직후·읽기 실패·종료 뒤) 어떤 형식으로 쓸지 알 수 없다
            raise DeviceError("장비의 기종을 아직 확인하지 못해 쓸 수 없습니다. 먼저 장비를 읽어 주십시오.")
        if len(data) == prof.read_size:   # 백업 원본(읽은 크기)을 받으면 쓰는 부분만 보낸다
            data = bytes(data)[:prof.write_size]
        return self._write_once(slot, data) or self._write_once(slot, data)

    def close(self):
        h, self.h = self.h, None
        self.profile = None
        if h:
            try:
                h.close()
            except Exception:  # noqa: 이미 끊긴 장비의 close 실패는 무시
                pass


def default_fixture() -> Path:
    # 패키지 기준 상대 경로: 소스에서는 tests/fixtures, exe 에서는 묶어 넣은 사본
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "fixtures" / "device-read-20261110.bin"
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "device-read-20261110.bin"


def synthetic_ge150(profile) -> dict:
    """GE150 가짜 장비 자료: 모든 모듈이 첫 모델·OFF·기본값인 프리셋 200개(이름 'GE150 Test 001'~'200').
    읽기 크기(162) = 쓰기 부분 158 + 에디터도 쓰지 않는 뒤 4바이트(0)."""
    mods = {str(i): {"Data": {q["name"]: 0 for q in catalog.params(i, 0, profile)}, "SWITCH": 0, "TYPE": 0}
            for i in range(codec.MODULE_COUNT)}
    exp = {"MODULE_CTRL": 0, "PARA_CTRL": 6, "FUN_SWITCH": 0, "VOL_SWITCH": 1, "VOL_MIN": 0, "VOL_MAX": 100}
    out = {}
    for s in range(1, P.SLOT_COUNT + 1):
        mo = {"Exp": exp, "effectModule": mods, "fileInfo": {"preset_name": "GE150 Test %03d" % s}}
        out[s] = codec.encode(mo, profile) + b"\x00" * (profile.read_size - profile.write_size)
    return out


class FakeDevice:
    """녹화 자료(200개 원본 바이트)로 동작하는 가짜 장비. 쓰기는 메모리에만 반영한다."""

    def __init__(self, path=None, fail_slots=(), clock=None, drop_after=None, min_gap=0.3, never_save=(), profile=None):
        """clock/drop_after/min_gap: 실장비에서 관찰한 동작 재현 — 응답은 주지만 min_gap 보다 빨리 연속으로 오는 쓰기가
        drop_after 개를 넘으면 저장하지 않는다(간격이 min_gap 이상이면 회복). never_save: 응답만 주고 끝내 저장하지 않는 슬롯."""
        self.profile = profile or models.PRO
        if path is None and self.profile is not models.PRO:
            self.slots = synthetic_ge150(self.profile)
        else:
            raw = Path(path or default_fixture()).read_bytes()
            n = self.profile.read_size
            self.slots = {s: raw[(s - 1) * n:s * n] for s in range(1, P.SLOT_COUNT + 1)}
        self.fail_slots = set(fail_slots)  # 시험용: 이 슬롯은 쓰기 실패
        self.written = []                  # 시험용: 실제로 저장된 쓰기 기록
        self.write_calls = []              # 시험용: 쓰기 시도(응답을 준 것 포함) 기록
        self.clock, self.drop_after, self.min_gap = clock, drop_after, min_gap
        self.never_save = set(never_save)
        self._last, self._fast = None, 0
        self.connected = False

    def connect(self):
        self.connected = True

    def read_all(self) -> dict:
        return dict(self.slots)

    def user_samples(self):
        return ["EMPTY"] * 40 if self.profile.has_sample_names else None

    def write_slot(self, slot: int, data: bytes) -> bool:
        if slot in self.fail_slots:
            return False
        if len(data) not in (self.profile.write_size, self.profile.read_size) or not 1 <= slot <= P.SLOT_COUNT:
            raise ValueError("잘못된 슬롯 또는 데이터")
        # 실장비처럼 쓰는 부분만 바꾸고 뒤쪽 읽기 전용 바이트(GE150 4바이트)는 그대로 둔다
        data = bytes(data)[:self.profile.write_size] + self.slots[slot][self.profile.write_size:]
        self.write_calls.append(slot)
        now = (self.clock or time.monotonic)()
        fast = self._last is not None and now - self._last < self.min_gap - 1e-9
        self._fast = self._fast + 1 if fast else 0
        self._last = now
        if slot in self.never_save or (self.drop_after is not None and self._fast >= self.drop_after):
            return True                    # 응답은 오지만 저장되지 않음(실장비 시험에서 관찰)
        self.slots[slot] = bytes(data)
        self.written.append(slot)
        return True

    def close(self):
        self.connected = False
