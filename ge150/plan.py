"""폴더 -> 계획 -> 백업 -> 쓰기 -> 다시 읽어 대조. 화면(app.py)과 명령줄(cli.py)이 같은 흐름을 쓴다."""
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import backup, codec, mofile, models
from . import protocol as P

ST_CHANGE, ST_SAME, ST_NONE, ST_ERROR, ST_RESTORE = "변경", "동일", "파일 없음", "오류", "백업 복원"
DEVICE_BIN = "device.bin"
PARTIAL_RE = re.compile(r"\.partial(-\d+)?$", re.IGNORECASE)

# D1: 실장비 시험(2026-11-10)에서 C3 를 간격 없이 보내면 43 응답은 오지만 저장되지 않는 슬롯이 생겼다(99개 중 77개 불일치).
# 응답 뒤 0.3초(99개 38.5초)·1.0초 모두 전부 저장됨 -> 응답 뒤에 쉬고, 대조에서 어긋난 슬롯은 다시 쓴다.
WRITE_GAP_S = 0.3     # 쓰기 응답을 받은 뒤 다음 슬롯을 보내기 전에 쉬는 시간(상수 1곳)
RETRY_GAP_S = 1.0     # 다시 쓰기 간격
MAX_REWRITE = 2       # 대조 불일치 슬롯을 다시 쓰는 최대 횟수
PER_SLOT_S = 0.4      # 슬롯 1개당 예상 시간(간격 0.3초 + 전송) — 확인 창의 예상 시간용(200개 약 80초)
_sleep = time.sleep   # 시험에서 가짜 sleep 로 바꿔 끼운다


def _cut(b: bytes) -> bytes:
    """비교는 앞 write_size 바이트만 한다(GE150 읽기 응답 뒤 4바이트는 에디터도 쓰지 않는 자리)."""
    return bytes(b[:models.active().write_size])


def estimate_seconds(n: int) -> int:
    return int(round(n * PER_SLOT_S))


@dataclass
class Row:
    slot: int
    device_name: str
    status: str = ST_NONE
    new_name: str = ""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    entry: mofile.MoEntry | None = None
    data: bytes | None = None   # 장비에 쓸 바이트(오류가 없을 때만)
    exact: bool = False         # True = 백업 원본 바이트라서 대조도 바이트 일치로 한다(앞 write_size 바이트)

    @property
    def label(self):
        return P.slot_label(self.slot)


def is_backup_folder(folder) -> bool:
    """B01: 우리 프로그램이 만든 증명 파일(backup.json)이 있는 폴더만 백업 폴더로 본다(device.bin 만 있으면 일반 톤 폴더)."""
    return backup.has_proof(folder)


def _restore_rows(device_data: dict, folder):
    """정상 백업(backup.json·device.bin 해시 일치)이면 슬롯별 원본 바이트를 그대로 쓴다(범위 검사 생략).

    손상·불일치면 BackupError 로 폴더 전체를 거부한다(쓰기 0).
    """
    raw = backup.verify_backup(folder)
    rows = []
    for s in range(1, P.SLOT_COUNT + 1):
        cur = device_data[s]
        new = raw[s]
        rows.append(Row(slot=s, device_name=codec.decode_name(cur), new_name=codec.decode_name(new),
                        status=ST_SAME if _cut(new) == _cut(cur) else ST_RESTORE, data=new, exact=True))
    return rows


def build_plan(device_data: dict, folder, overrides=None, user_names=None):
    """200개 행과 '슬롯 미지정' 파일 목록을 만든다. overrides = {파일경로: 슬롯}(사용자가 고른 슬롯)."""
    if PARTIAL_RE.search(Path(folder).name):  # C02: 변환 실패로 남은 불완전한 폴더는 입력으로 받지 않는다
        raise ValueError("불완전한 변환 폴더(%s)입니다. 톤 폴더로 열 수 없습니다. 톤 JSON 을 다시 변환해 주십시오." % Path(folder).name)
    if is_backup_folder(folder):
        return _restore_rows(device_data, folder), []
    entries = mofile.scan_folder(folder, user_names)
    overrides = {Path(k): v for k, v in (overrides or {}).items()}
    for e in entries:
        if e.slot is None and e.path in overrides:
            e.slot = overrides[e.path]
    mofile.mark_duplicates(entries)  # 슬롯을 지정한 뒤의 중복도 잡는다
    by_slot = {}
    for e in entries:
        if e.slot:
            by_slot.setdefault(e.slot, []).append(e)
    rows = []
    for s in range(1, P.SLOT_COUNT + 1):
        cur = device_data[s]
        row = Row(slot=s, device_name=codec.decode_name(cur))
        group = by_slot.get(s)
        if group:
            e = row.entry = group[0]
            row.new_name, row.errors, row.warnings = e.name, list(e.errors), list(e.warnings)
            if e.errors:
                row.status = ST_ERROR
            else:
                row.data = codec.encode(e.mo)
                row.status = ST_SAME if codec.same_preset(row.data, cur) else ST_CHANGE
        rows.append(row)
    unassigned = [e for e in entries if e.slot is None]
    return rows, unassigned


@dataclass
class ApplyResult:
    backup_dir: Path | None = None
    ok: list = field(default_factory=list)        # 완료: 마지막 성공 재읽기에서 목표와 일치
    failed: list = field(default_factory=list)    # 응답 없음(재시도까지 실패)
    unknown: list = field(default_factory=list)   # 미확인: 마지막 성공 재읽기 이후 쓰기를 보냈거나, 재읽기를 한 번도 성공하지 못함
    skipped: list = field(default_factory=list)   # 미시도
    mismatch: list = field(default_factory=list)  # 예상 밖 변경: 선택하지 않은 슬롯이 원본(백업)과 달라진 경우(다시 쓰지 않고 실패로 판정)
    not_saved: list = field(default_factory=list)  # D1: 마지막 성공 재읽기 이후 쓰기가 없었는데 목표와 다른 슬롯
    rewritten: dict = field(default_factory=dict)  # D1·E: 슬롯 -> 다시 쓰기 시도 횟수(예외가 난 쓰기도 센다)
    rewrite_errors: dict = field(default_factory=dict)  # E: 슬롯 -> 다시 쓰다 예외가 난 횟수(시도 횟수와 분리 표기)
    mismatch_info: dict = field(default_factory=dict)  # F: 예상 밖 변경 슬롯 -> (처음 관측한 재읽기 회차, 마지막에 원래대로 돌아왔는지)
    unknown_info: dict = field(default_factory=dict)  # E: 미확인 슬롯 -> 마지막 확인 시점(성공한 재읽기 회차, 0 = 확인 못 함)
    read_rounds: int = 0                          # E: 성공한 전체 재읽기 횟수
    notes: list = field(default_factory=list)
    error: str = ""
    stopped: bool = False
    result_file_error: str = ""
    fallback_path: str = ""       # 결과.txt 저장 실패 시 대체로 저장한 위치
    fallback_error: str = ""
    record_failed: bool = False   # 기록(결과.txt) 저장을 시도했으나 실패

    @property
    def write_ok(self):
        """B02: 장비 쓰기 결과 — 모든 대상 슬롯 완료 + 대조 일치(중단·미시도·미확인·저장 안 됨·오류 없음)."""
        return not (self.error or self.failed or self.unknown or self.mismatch or self.not_saved or self.stopped
                    or self.skipped)

    @property
    def record_ok(self):
        """B02: 기록 결과 — 결과.txt 저장 성공(저장을 시도하지 않았다면 실패가 아님)."""
        return not self.record_failed

    @property
    def success(self):
        return self.write_ok and self.record_ok

    def text(self, for_file=False):
        lab = lambda xs: " ".join(P.slot_label(x) for x in xs) or "없음"
        hard = self.error or self.failed or self.unknown or self.mismatch or self.not_saved
        state = "완료" if self.write_ok else ("중단" if self.stopped and not hard else "실패")
        rec = "이 파일에 저장" if for_file else ("저장 완료" if self.record_ok else "저장 실패")
        lines = ["결과: 쓰기 %s / 기록 %s" % (state, rec),
                 "백업 폴더: %s" % (self.backup_dir or "없음"),
                 "완료 %d개: %s" % (len(self.ok), lab(self.ok)),
                 "실패(응답 없음) %d개: %s" % (len(self.failed), lab(self.failed)),
                 "미확인(쓰기 뒤 확인 못 함) %d개: %s" % (len(self.unknown), " ".join(
                     "%s(마지막 확인 시점: %s)" % (P.slot_label(x), ("%d회차 재읽기" % self.unknown_info.get(x, 0))
                                                if self.unknown_info.get(x, 0) else "확인 못 함") for x in self.unknown) or "없음"),
                 "미시도 %d개: %s" % (len(self.skipped), lab(self.skipped)),
                 "저장 안 됨(응답은 왔으나 장비에 저장되지 않음) %d개: %s" % (len(self.not_saved), lab(self.not_saved)),
                 "예상 밖 변경(선택하지 않은 슬롯이 원본과 달라짐) %d개: %s" % (len(self.mismatch), " ".join(
                     "%s(관측 회차 %d%s)" % (P.slot_label(x), self.mismatch_info.get(x, (0, False))[0],
                                          ", 마지막 재읽기에서는 원래대로" if self.mismatch_info.get(x, (0, False))[1] else "")
                     for x in self.mismatch) or "없음"),
                 "성공한 전체 재읽기: %d회" % self.read_rounds]
        if self.rewritten:
            def fin(k):
                return "저장 확인" if k in self.ok else "저장 안 됨" if k in self.not_saved else "미확인"
            lines.append("다시 쓴 슬롯 %d개(최대 %d회): %s" % (
                len(self.rewritten), MAX_REWRITE,
                " ".join("%s(%d회%s·%s)" % (P.slot_label(k), v,
                                           (", 예외 %d회" % self.rewrite_errors[k]) if self.rewrite_errors.get(k) else "", fin(k))
                         for k, v in sorted(self.rewritten.items()))))
        lines += self.notes
        if self.error:
            lines.append("오류: " + self.error)
        if self.result_file_error:
            lines.append("결과.txt 저장 실패: %s (백업 폴더는 위 위치에 있습니다)" % self.result_file_error)
            if self.fallback_path:
                lines.append("같은 내용을 대체 위치에 저장했습니다: %s" % self.fallback_path)
            elif self.fallback_error:
                lines.append("대체 위치 저장도 실패했습니다: %s" % self.fallback_error)
        return "\n".join(lines) + "\n"


def apply_plan(dev, rows, slots, backup_root=None, progress=None, should_stop=None, guard=None) -> ApplyResult:
    """선택한 슬롯을 쓴다. 순서: 에디터 검사 -> 현재 상태 읽기 -> 백업(실패하면 쓰지 않음) -> 슬롯별 쓰기(응답 뒤 0.3초 쉼)
    -> 저장 확인(재읽기 대조) -> 어긋난 슬롯 다시 쓰기(간격 1.0초, 최대 2회).

    A02: 어떤 예외가 나도 ApplyResult 를 돌려준다(완료/미확인/미시도 + 백업 위치). 백업이 만들어졌다면 결과.txt 도 항상 시도한다.
    progress(done, total, label, message): 경과 알림. should_stop(): 참이면 지금 슬롯까지만 쓰고 멈춘다(다시 쓰기 단계에서도).
    guard(): 문구(str)를 돌려주면 그 사유로 쓰기를 막는다(에디터 실행 중·확인 불가 등) — A05 적용 직전 재검사.
    """
    res = ApplyResult()
    try:
        _apply(dev, rows, slots, backup_root, progress, should_stop, guard, res)
    except Exception as ex:  # noqa: 마지막 방어선 — 어떤 경우에도 호출자가 결과를 받는다
        res.error = (res.error + " / " if res.error else "") + "예기치 않은 오류: %s" % ex
    if res.backup_dir is not None:
        try:
            backup.write_result(res.backup_dir, res.text(for_file=True))
        except Exception as ex:  # noqa: 결과 파일 저장 실패도 결과에 남긴다(B02: 쓰기 성공과 구분)
            res.record_failed, res.result_file_error = True, str(ex)
            try:  # 같은 내용을 %LOCALAPPDATA%\GE150PresetLoader 에 한 번 더 저장해 본다
                res.fallback_path = str(backup.write_fallback_result(res.text()))
            except Exception as ex2:  # noqa
                res.fallback_error = str(ex2)
    return res


def _apply(dev, rows, slots, backup_root, progress, should_stop, guard, res):
    """슬롯의 최종 상태는 '마지막으로 성공한 전체 재읽기'로만 정한다(E단계).

    - 재읽기마다 모든 선택 슬롯=목표, 모든 비선택 슬롯=원본(백업)을 다시 대조한다(이전 회차에 일치했던 슬롯도 다시 검사).
    - 재시도 대상 = 마지막 재읽기에서 목표와 다른 선택 슬롯 전부(최대 MAX_REWRITE 회차). 비선택 슬롯의 변경은 다시 쓰지 않고 실패로 판정.
    - 완료 = 마지막 성공 재읽기에서 목표와 일치 / 저장 안 됨 = 그 재읽기 이후 쓰기가 없었고 목표와 다름 /
      미확인 = 그 재읽기 이후 쓰기를 보냈거나(응답·예외와 무관) 재읽기를 한 번도 성공하지 못함.
    - 중단·USB 예외·재읽기 실패 뒤에는 추가 쓰기 없이 읽기를 한 번 시도한다.
    """
    by = {r.slot: r for r in rows}
    targets = sorted(set(slots))
    for s in targets:
        r = by.get(s)
        if r is None or r.data is None or r.errors:  # 안전 규칙 2: 오류 슬롯은 쓰지 않음
            res.error = "%s 은(는) 오류가 있어 쓸 수 없습니다." % P.slot_label(s)
            return
    notify = progress or (lambda *a: None)
    stop = should_stop or (lambda: False)
    reason = guard() if guard else None
    if reason:
        res.error = reason
        return
    try:
        notify(0, len(targets), "", "장비 읽는 중")
        before = dev.read_all()
        fails = []
        res.backup_dir = backup.make_backup(before, backup_root, failures=fails)  # 안전 규칙 1: 백업 실패 시 예외 -> 쓰지 않음
        for s, why in fails:  # B01·D2: .mo 만 빠지고 원본(device.bin)은 보존됨 — 경고로만 남긴다
            res.notes.append("경고: 백업 .mo 생략 %s — %s (원본 바이트는 device.bin 에 보존)" % (P.slot_label(s), why))
    except Exception as ex:  # noqa: 백업 단계의 어떤 실패도 쓰기를 막아야 한다
        res.error = "백업 실패로 쓰지 않았습니다: %s" % ex
        return

    def matches(slot, got):
        want = by[slot].data
        return _cut(got) == _cut(want) if by[slot].exact else codec.same_preset(got, want)   # 백업 원본이면 바이트 일치

    st = {"after": None, "sent": set(), "read_error": False}   # after: 마지막 성공 재읽기 / sent: 그 이후 쓰기를 보낸 슬롯

    def observe_unselected(after):
        """E02: 성공한 모든 재읽기에서 비선택 슬롯(과 쓰지 못한 선택 슬롯)을 원본과 대조한다. 한 번이라도 다르면 기록한다."""
        for sl in range(1, P.SLOT_COUNT + 1):
            if sl in targets and sl not in res.failed and sl not in res.skipped:
                continue
            if _cut(after[sl]) != _cut(before[sl]) and not (sl in targets and matches(sl, after[sl])):
                res.mismatch_info.setdefault(sl, (res.read_rounds, False))   # 처음 관측한 회차를 유지
        return None

    def read_verify(label):
        """전체 재읽기 1회(실패하면 한 번 더 시도). 성공하면 최신 자료로 바꾸고 이후-쓰기 기록을 비운다.

        E01: 읽기가 한 번이라도 실패하면(확인용 읽기가 성공해도) st["read_error"] 를 켜서 이 적용에서는 더 쓰지 않게 한다.
        """
        first_err = None
        for attempt in (1, 2):
            try:
                notify(len(res.ok), len(targets), "", label)
                got = dev.read_all()
            except Exception as ex:  # noqa: 재읽기 실패 = 확인 못 함(성공으로 보지 않는다)
                first_err = first_err or ex
                st["read_error"] = True
                continue
            st["after"] = got
            res.read_rounds += 1
            st["sent"].clear()
            observe_unselected(got)
            if first_err is not None:
                res.notes.append("재읽기 1회 실패 후 다시 읽어 확인했습니다(그 결과로만 판정하고 다시 쓰지 않습니다): %s" % first_err)
            return True
        res.error = (res.error + " / " if res.error else "") + "다시 읽기 실패(대조 못 함): %s" % first_err
        return False

    eligible = []        # 쓰기 응답을 받은 선택 슬롯(재시도 후보)
    aborted = False      # 예외·재읽기 실패 뒤에는 추가 쓰기를 하지 않는다
    for i, s in enumerate(targets):
        if stop():
            res.stopped = True
            res.skipped = targets[i:]
            break
        notify(i, len(targets), P.slot_label(s), "쓰는 중")
        st["sent"].add(s)
        try:
            done = dev.write_slot(s, by[s].data)
        except Exception as ex:  # noqa: A02 — USB 분리 등. 이후에는 읽기만 한 번 시도한다
            res.skipped = targets[i + 1:]
            res.error = "%s 쓰는 중 오류: %s" % (P.slot_label(s), ex)
            aborted = True
            break
        if done:
            eligible.append(s)
            _sleep(WRITE_GAP_S)   # D1: 응답 뒤 쉬어야 장비가 저장한다(응답 != 저장 보장)
        else:
            res.failed.append(s)
            res.skipped = targets[i + 1:]
            break
    ok_read = read_verify("저장 확인 중")
    attempt = 0
    while ok_read and not aborted and not st["read_error"] and attempt < MAX_REWRITE:
        pending = [s for s in eligible if not matches(s, st["after"][s])]   # 이전 회차에 일치했던 선택 슬롯도 다시 검사
        if not pending:
            break
        if stop():
            res.stopped = True
            res.notes.append("다시 쓰기 중단: 저장 확인되지 않은 슬롯이 남았습니다(이후 추가 쓰기 없음).")
            break
        attempt += 1
        _sleep(RETRY_GAP_S)
        for k, s in enumerate(pending):
            if stop():
                res.stopped = True
                res.notes.append("다시 쓰기 중단: 저장 확인되지 않은 슬롯이 남았습니다(이후 추가 쓰기 없음).")
                aborted = True
                break
            notify(k, len(pending), P.slot_label(s), "다시 쓰는 중(%d/%d)" % (attempt, MAX_REWRITE))
            st["sent"].add(s)
            res.rewritten[s] = res.rewritten.get(s, 0) + 1   # 예외가 난 쓰기도 시도로 센다
            try:
                dev.write_slot(s, by[s].data)
            except Exception as ex:  # noqa: 다시 쓰는 중 USB 오류
                res.rewrite_errors[s] = res.rewrite_errors.get(s, 0) + 1
                res.error = (res.error + " / " if res.error else "") + "%s 다시 쓰는 중 오류: %s" % (P.slot_label(s), ex)
                aborted = True
                break
            _sleep(RETRY_GAP_S)
        if st["sent"]:   # 쓴 뒤에는 (중단·예외여도 추가 쓰기 없이) 읽기를 한 번 시도한다
            if not read_verify("저장 확인 중") or st["read_error"]:
                aborted = True   # E01: 읽기 오류가 있었으면 확인용 읽기가 성공해도 더 쓰지 않는다
        if aborted:
            break
    # ---- 최종 상태: 마지막 성공 재읽기 기준
    after, rounds = st["after"], res.read_rounds
    for s in targets:
        if s in res.failed or s in res.skipped:
            continue
        if after is None or s in st["sent"]:
            res.unknown.append(s)
            res.unknown_info[s] = 0 if after is None else rounds
        elif matches(s, after[s]):
            res.ok.append(s)
        else:
            res.not_saved.append(s)
    if after is None:
        res.notes.append("비선택 슬롯의 원본 유지 여부는 확인하지 못했습니다(재읽기 실패).")
        res.notes.append("읽기 오류가 있어 다시 쓰기를 멈췄습니다. 같은 폴더로 다시 적용하십시오.")   # E01
        notify(len(res.ok), len(targets), "", "실패")
        return
    for sl, (rnd, _) in sorted(res.mismatch_info.items()):   # E02: 마지막에 원래대로 돌아왔어도 관측된 변경은 실패로 남긴다
        res.mismatch.append(sl)
        res.mismatch_info[sl] = (rnd, _cut(after[sl]) == _cut(before[sl]))
    if st["read_error"] and (res.unknown or res.not_saved):
        res.notes.append("읽기 오류가 있어 다시 쓰기를 멈췄습니다. 같은 폴더로 다시 적용하십시오.")   # E01
    notify(len(res.ok), len(targets), "", "완료" if res.success else "실패")
