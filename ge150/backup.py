"""백업·결과 기록. 백업 폴더 = .mo 200개(해석되는 슬롯) + 원본 device.bin + 증명 파일 backup.json.

복원은 backup.json(형식·기종·device.bin 해시·슬롯별 해시)이 모두 맞을 때만 원본 바이트로 한다(B01).
"""
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import codec, mofile, models
from . import protocol as P

BACKUP_FORMAT, BACKUP_VERSION = "GE150PRO-BACKUP", 1
PROOF_NAME = "backup.json"


class BackupError(ValueError):
    """백업 폴더가 손상되었거나 증명 파일과 맞지 않음(복원을 거부한다)."""


def default_backup_root() -> Path:
    # exe 옆 '백업' 폴더. 개발 중에는 프로젝트 폴더의 백업 (경로를 코드에 박지 않는다)
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return base / "백업"


def device_bin(data: dict) -> bytes:
    return b"".join(data[s] for s in range(1, P.SLOT_COUNT + 1))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_backup(data: dict, root=None, now=None, failures=None) -> Path:
    """장비 200개를 백업 폴더에 저장하고 다시 읽어 확인한다. 실패하면 예외(=쓰기 중단).

    B01: 해석(decode)이 안 되는 슬롯이 있어도 막히지 않는다 — device.bin·backup.json 은 반드시 저장하고,
    .mo 는 해석되는 슬롯만 쓴다. 해석 못 한 슬롯은 failures 목록에 (슬롯, 사유)로 담는다.
    """
    prof = models.active()
    if sorted(data) != list(range(1, P.SLOT_COUNT + 1)) or any(len(v) != prof.read_size for v in data.values()):
        raise RuntimeError("백업할 프리셋이 200개(각 %d바이트)가 아닙니다." % prof.read_size)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    folder = Path(root or default_backup_root()) / stamp
    folder.mkdir(parents=True, exist_ok=False)
    raw = device_bin(data)
    (folder / "device.bin").write_bytes(raw)
    proof = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION,
             "device": prof.key, "preset_size": prof.read_size,   # 다른 기종 장비에 복원하지 못하게 기록
             "created": (now or datetime.now()).isoformat(timespec="seconds"),
             "device_bin_sha256": _sha(raw),
             "slots": {str(s): _sha(data[s]) for s in range(1, P.SLOT_COUNT + 1)}}
    (folder / PROOF_NAME).write_text(json.dumps(proof, indent=1) + "\n", encoding="utf-8")
    # D2: 백업의 성공 = device.bin + backup.json 저장·검증 성공. .mo 는 편의용이라 실패해도 경고로만 남긴다(쓰기를 막지 않음).
    try:
        verify_backup(folder)
    except BackupError as ex:
        raise RuntimeError("백업 확인에 실패했습니다: %s" % ex)
    for s in range(1, P.SLOT_COUNT + 1):
        try:
            mo = codec.decode(data[s])
        except (codec.CodecError, ValueError, KeyError) as ex:
            if failures is not None:
                failures.append((s, "해석 불가 — %s" % ex))
            continue
        try:
            mofile.write_mo(folder, s, mo, unique=True)
        except Exception as ex:  # noqa: 파일 이름·디스크 문제로 .mo 를 못 써도 백업은 성공으로 본다
            if failures is not None:
                failures.append((s, ".mo 저장 실패 — %s" % ex))
    return folder


def has_proof(folder) -> bool:
    return (Path(folder) / PROOF_NAME).is_file()


def verify_backup(folder) -> dict:
    """backup.json 과 device.bin 을 대조한다. 맞으면 {슬롯: 원본 바이트}, 아니면 BackupError(폴더 전체 거부).
    기종은 활성 기종과 같아야 한다(device 가 없는 옛 백업은 GE150PRO 로 본다)."""
    folder = Path(folder)
    try:
        proof = json.loads((folder / PROOF_NAME).read_text(encoding="utf-8"))
        if not isinstance(proof, dict) or proof.get("format") != BACKUP_FORMAT or proof.get("version") != BACKUP_VERSION:
            raise BackupError("백업 손상: backup.json 의 형식이 다릅니다.")
        prof = models.active()
        made_for = proof.get("device", models.PRO.key)
        if made_for != prof.key:
            other = models.by_key(made_for)
            raise BackupError("다른 기종(%s)의 백업입니다. 현재 장비는 %s 입니다." % (other.label if other else made_for, prof.label))
        n = prof.read_size
        raw = (folder / "device.bin").read_bytes()
        if len(raw) != P.SLOT_COUNT * n or proof.get("preset_size", n) != n:
            raise BackupError("백업 손상: device.bin 크기가 올바르지 않습니다(%d바이트)." % len(raw))
        if proof.get("device_bin_sha256") != _sha(raw):
            raise BackupError("백업 손상: device.bin 이 backup.json 의 해시와 다릅니다.")
        slots = proof.get("slots")
        out = {}
        for s in range(1, P.SLOT_COUNT + 1):
            chunk = raw[(s - 1) * n:s * n]
            if not isinstance(slots, dict) or slots.get(str(s)) != _sha(chunk):
                raise BackupError("백업 손상: 슬롯 %s 의 해시가 backup.json 과 다릅니다." % P.slot_label(s))
            out[s] = chunk
        return out
    except BackupError:
        raise
    except (OSError, ValueError, AttributeError) as ex:
        raise BackupError("백업 손상: %s" % ex)


def write_result(folder, text: str) -> Path:
    path = Path(folder) / "결과.txt"
    path.write_text(text, encoding="utf-8")
    return path


def fallback_dir() -> Path:
    """결과.txt 저장에 실패했을 때 한 번 더 저장해 보는 위치(%LOCALAPPDATA%\\GE150PresetLoader)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "GE150PresetLoader"


def write_fallback_result(text: str, now=None) -> Path:
    d = fallback_dir()
    d.mkdir(parents=True, exist_ok=True)
    base = "결과-%s" % (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    for n in range(1, 1000):   # C01: 배타적 생성('x') — 같은 초의 이전 기록을 덮어쓰지 않고 번호를 붙인다
        path = d / (base + (".txt" if n == 1 else "-%d.txt" % n))
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(text)
            return path
        except FileExistsError:
            continue
    raise OSError("대체 결과 파일 이름을 정하지 못했습니다.")
