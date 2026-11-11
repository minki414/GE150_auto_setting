"""프레임·CRC·HID 보고서 처리(장치 없이 시험 가능).

프레임·CRC·명령은 GE150 Pro 실장비에서 확인했고, GE150 도 에디터 분석상 같은 방식입니다.
"""
import re

from . import models

# 사용자 의도 [요구]: 쓰기는 C3(프리셋 슬롯)만, 펌웨어·초기화·샘플 관련 명령은 넣지 않는다.
# 개발 판단: 명령 바이트를 이 상수 몇 개로만 제한해 실수로 다른 명령이 나가는 길을 줄인다.
CMD_START = bytes.fromhex("3101")
CMD_READ_ALL = bytes.fromhex("a001")
CMD_USER_NAMES = bytes.fromhex("c101")
CMD_PRE_WRITE = models.PRO.pre_write   # GE150 은 쓰기 전 명령이 없다(models.GE150.pre_write = None)
CMD_WRITE = 0xC3
RSP_PRESET = 0x20
RSP_WRITE_DONE = 0x43
RSP_USER_NAMES = 0x41
SLOT_COUNT = 200
CHUNK = 63  # HID 보고서 1개에 실리는 데이터 최대 길이


def crc16(data: bytes) -> int:
    """CRC-16(poly 0x1021, init 0, 비반사, xorout 0xFFFF). 장비가 이 값으로 프레임을 검증한다."""
    c = 0
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c ^ 0xFFFF


def build_frame(body: bytes) -> bytes:
    """AA 55 | LEN u16 LE | BODY | CRC(LEN+BODY) 빅엔디언."""
    ln = len(body).to_bytes(2, "little")
    return b"\xaa\x55" + ln + body + crc16(ln + body).to_bytes(2, "big")


def frame_to_reports(frame: bytes) -> list:
    """프레임을 hidapi 쓰기용 65바이트(보고서 ID 0 + 길이 + 데이터 + 0 채움) 목록으로 나눈다."""
    out = []
    for i in range(0, len(frame), CHUNK):
        chunk = frame[i:i + CHUNK]
        out.append(b"\x00" + bytes([len(chunk)]) + chunk + b"\x00" * (CHUNK - len(chunk)))
    return out


def report_payload(report: bytes) -> bytes:
    """읽은 보고서 64바이트에서 유효 데이터만 꺼낸다(r[0]=유효길이)."""
    return bytes(report[1:1 + report[0]])


def write_body(slot: int, data: bytes, profile=None) -> bytes:
    """C3 쓰기 본문. 데이터 길이는 기종의 write_size 여야 한다(GE150 읽기 크기 162 는 호출 쪽에서 잘라 넘긴다)."""
    n = (profile or models.active()).write_size
    if not 1 <= slot <= SLOT_COUNT:
        raise ValueError("슬롯 번호는 1~200이어야 합니다.")
    if len(data) != n:
        raise ValueError("프리셋 데이터는 %d바이트여야 합니다." % n)
    return bytes([CMD_WRITE, slot]) + bytes(data)


class FrameParser:
    """보고서를 이어 붙인 흐름에서 프레임을 잘라 (본문, CRC 일치 여부) 로 돌려준다."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data

    def frames(self):
        out = []
        while True:
            i = self.buf.find(b"\xaa\x55")
            if i < 0:
                # 머리표를 못 찾은 앞부분은 버려 쌓이지 않게 한다(끝 1바이트는 머리표 일부일 수 있어 남김)
                del self.buf[:max(0, len(self.buf) - 1)]
                break
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 4:
                break
            ln = int.from_bytes(self.buf[2:4], "little")
            if len(self.buf) < 6 + ln:
                break
            body = bytes(self.buf[4:4 + ln])
            ck = bytes(self.buf[4 + ln:6 + ln])
            del self.buf[:6 + ln]
            out.append((body, ck == crc16(ln.to_bytes(2, "little") + body).to_bytes(2, "big")))
        return out


# --- 슬롯 표기 ---------------------------------------------------------------
_SLOT_RE = re.compile(r"^(\d{2})([A-Da-d])$")


def slot_label(slot: int) -> str:
    """slot 1~200 -> '01A'~'50D'. (뱅크-1)*4 + {A:1..D:4}."""
    if not 1 <= slot <= SLOT_COUNT:
        raise ValueError("슬롯 번호는 1~200이어야 합니다.")
    return "%02d%s" % ((slot - 1) // 4 + 1, "ABCD"[(slot - 1) % 4])


def parse_slot(text: str):
    """'13a' -> 49. 형식이 아니거나 범위 밖이면 None."""
    m = _SLOT_RE.match(text or "")
    if not m:
        return None
    bank = int(m.group(1))
    if not 1 <= bank <= 50:
        return None
    return (bank - 1) * 4 + "ABCD".index(m.group(2).upper()) + 1
