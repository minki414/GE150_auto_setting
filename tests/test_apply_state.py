"""E단계(감사 D 반영): 슬롯 최종 상태는 '마지막으로 성공한 전체 재읽기'로만 정한다.

D02(재시도 뒤 이미 확인한 선택 슬롯의 후속 변화를 놓침)·D01(중단·예외·재읽기 실패 때 오래된 자료로 단정)·
비선택 슬롯 예상 밖 변경·중단 뒤 읽기 1회·재시도 횟수 집계를 장비 없이 재현한다.
"""
import pytest

from ge150 import backup, codec, device, mofile, plan

OrigFake = device.FakeDevice


class Scripted(OrigFake):
    """슬롯별 쓰기 횟수에 따라 hook 이 저장 여부·부작용·예외를 정한다. read_all 횟수와 쓰기 기록을 센다."""

    def __init__(self, hook=None):
        super().__init__()
        self.hook, self.counts, self.reads, self.log = hook, {}, 0, []
        self.dead = False

    def read_all(self):
        self.reads += 1
        self.log.append("read")
        if self.dead:
            raise OSError("synthetic read failure")
        return super().read_all()

    def write_slot(self, slot, data):
        self.counts[slot] = self.counts.get(slot, 0) + 1
        self.log.append(("write", slot))
        store = self.hook(self, slot, self.counts[slot], bytes(data)) if self.hook else True
        if store:
            self.slots[slot] = bytes(data)
        return True


def two_slot_rows(tmp_path, slots=(1, 2), restore=False):
    """01A·01B 두 슬롯만 바꾸는 계획. restore=True 이면 백업 폴더(원본 바이트)에서 복원하는 계획."""
    dev = Scripted()
    if restore:
        folder = backup.make_backup(dev.read_all(), tmp_path / "orig")
        for s in range(1, 201):
            dev.slots[s] = dev.slots[3]
        dev.reads = 0
    else:
        folder = tmp_path / "tone"
        folder.mkdir()
        for s in slots:
            mo = codec.decode(dev.slots[s])
            mo["fileInfo"]["preset_name"] = "New%d" % s
            mofile.write_mo(folder, s, mo)
    rows, _ = plan.build_plan(dev.read_all(), folder)
    dev.reads = 0
    dev.log.clear()
    return dev, rows


def corrupt(dev, slot):
    dev.slots[slot] = dev.slots[slot][:3] + b"X" + dev.slots[slot][4:]   # 이름 한 글자만 바꿈(후속 변화)


# ---------------------------------------------------------------- D02
@pytest.mark.parametrize("restore", [False, True])
def test_d02_followup_change_of_confirmed_slot_is_retried(tmp_path, restore):
    """01B 다시 쓰기가 앞서 확인된 01A 를 바꿔 놓아도, 마지막 재읽기에서 01A 를 다시 검사해 재시도한다."""
    def hook(dev, slot, n, data):
        if slot == 2 and n == 1:
            return False                       # 응답만 주고 저장하지 않음
        if slot == 2 and n == 2:
            corrupt(dev, 1)                    # 저장하면서 01A 를 훼손(주입한 후속 변화)
        return True
    dev, rows = two_slot_rows(tmp_path, restore=restore)
    dev.hook = hook
    sel = [1, 2] if not restore else [r.slot for r in rows if r.status == plan.ST_RESTORE]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk")
    assert res.read_rounds == 3 and res.rewritten == {2: 1, 1: 1}          # 01A 도 다시 씀(회차 2)
    assert res.success, res.text()
    final = dev.read_all()
    for r in rows:
        if r.slot in sel:
            assert final[r.slot] == r.data if r.exact else codec.same_preset(final[r.slot], r.data)


@pytest.mark.parametrize("restore", [False, True])
def test_d02_unrecoverable_followup_change_is_a_failure(tmp_path, restore):
    """후속 변화가 끝내 해결되지 않으면 이전에 확인됐던 01A 도 '저장 안 됨', success=False (거짓 성공 없음)."""
    def hook(dev, slot, n, data):
        if slot == 2 and n == 1:
            return False
        if slot == 2 and n == 2:
            corrupt(dev, 1)
        if slot == 1 and n >= 2:
            return False                       # 01A 다시 쓰기는 저장되지 않음
        return True
    dev, rows = two_slot_rows(tmp_path, restore=restore)
    dev.hook = hook
    sel = [1, 2] if not restore else [r.slot for r in rows if r.status == plan.ST_RESTORE]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk")
    assert not res.success and not res.write_ok
    assert res.not_saved == [1] and 1 not in res.ok and 2 in res.ok
    assert "저장 안 됨(응답은 왔으나 장비에 저장되지 않음) 1개: 01A" in res.text()
    assert "결과: 쓰기 실패" in res.text()


def test_d02_with_all_200_selected(tmp_path):
    """200개 모두 선택(원본 복원)해도 같은 판정: 마지막 재읽기에서 01A 가 어긋나 있으면 성공이 아니다."""
    def hook(dev, slot, n, data):
        if slot == 2 and n == 1:
            return False
        if slot == 2 and n == 2:
            corrupt(dev, 1)
        if slot == 1 and n >= 2:
            return False
        return True
    dev, rows = two_slot_rows(tmp_path, restore=True)
    dev.hook = hook
    sel = [r.slot for r in rows if r.status == plan.ST_RESTORE]
    assert len(sel) >= 199                    # 슬롯 3 은 이미 같아서 '동일' — 나머지 전부 선택
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk")
    assert not res.success and res.not_saved == [1]
    assert len(res.ok) == len(sel) - 1


# ---------------------------------------------------------------- D01
def drop_first(dev, slot, n, data):
    return not (slot in (1, 2) and n == 1)       # 01A·01B 최초 쓰기는 응답만 주고 저장하지 않음


def test_d01_stop_after_first_rewrite_reads_once_and_writes_nothing_more(tmp_path):
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = drop_first
    stop = {"v": False}
    orig = dev.write_slot

    def watch(slot, data):
        r = orig(slot, data)
        if slot == 1 and dev.counts[1] == 2:     # 01A 다시 쓴 직후 중단 요청
            stop["v"] = True
        return r
    dev.write_slot = watch
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk", should_stop=lambda: stop["v"])
    assert res.stopped and not res.write_ok
    # 중단 뒤: 쓰기 없이 읽기 1회. 순서: (장비 읽기)·쓰기 2·재읽기·01A 다시 쓰기·읽기 1회로 끝
    assert dev.log[-1] == "read" and dev.log[-2] == ("write", 1)
    assert dev.counts == {1: 2, 2: 1}            # 01B 는 다시 쓰지 않음
    # 마지막 성공 재읽기(중단 뒤 읽기)로 판정: 01A 저장됨 -> 완료, 01B 미저장 -> 저장 안 됨(그 뒤 쓰기 없음)
    assert res.ok == [1] and res.not_saved == [2] and res.unknown == [] and res.read_rounds == 2
    assert "다시 쓰기 중단" in res.text() or res.stopped


def test_d01_usb_exception_after_store_reads_once_and_reports_actual_state(tmp_path):
    def hook(dev, slot, n, data):
        if slot in (1, 2) and n == 1:
            return False
        if slot == 2 and n == 2:
            dev.slots[2] = data                  # 01B 는 저장한 직후 USB 예외
            raise OSError("synthetic USB disconnect")
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert dev.log[-1] == "read" and dev.counts == {1: 2, 2: 2}      # 예외 뒤 쓰기 없이 읽기 1회
    assert res.ok == [1, 2] and res.unknown == [] and res.not_saved == []   # 읽어 보니 둘 다 저장됨(실제 상태와 같음)
    assert res.rewritten == {1: 1, 2: 1} and res.rewrite_errors == {2: 1}     # 예외 쓰기도 시도로 세고 분리 표기
    assert not res.write_ok and "다시 쓰는 중 오류" in res.error
    assert "01B(1회, 예외 1회·저장 확인)" in res.text()


def test_d01_usb_exception_then_read_fails_is_unknown_with_last_checkpoint(tmp_path):
    def hook(dev, slot, n, data):
        if slot in (1, 2) and n == 1:
            return False
        if slot == 2 and n == 2:
            dev.slots[2] = data
            dev.dead = True                      # 예외와 함께 USB 가 끊겨 읽기도 못 함
            raise OSError("synthetic USB disconnect")
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.unknown == [1, 2] and res.ok == [] and res.not_saved == []        # 확인 못 했으므로 미확인
    assert res.unknown_info == {1: 1, 2: 1}                                       # 마지막 확인 시점 = 1회차 재읽기
    txt = res.text()
    assert "01A(마지막 확인 시점: 1회차 재읽기)" in txt and "01B(마지막 확인 시점: 1회차 재읽기)" in txt
    assert "다시 읽기 실패" in res.error and not res.write_ok


def test_d01_read_fails_after_both_rewritten_is_unknown_not_not_saved(tmp_path):
    def hook(dev, slot, n, data):
        if slot in (1, 2) and n == 1:
            return False
        if slot == 2 and n == 2:
            dev.dead = True                      # 둘 다 다시 쓴 뒤 재읽기 실패
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.unknown == [1, 2] and res.not_saved == [] and res.ok == []         # 이전(1회차) 불일치로 '저장 안 됨' 단정 금지
    assert res.unknown_info == {1: 1, 2: 1} and res.rewritten == {1: 1, 2: 1}


def test_d01_never_read_successfully_is_unknown_with_no_checkpoint(tmp_path):
    dev, rows = two_slot_rows(tmp_path)

    def hook(d, slot, n, data):
        d.dead = True                            # 첫 쓰기 직후부터 읽기 불가
        return True
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.unknown == [1, 2] and res.unknown_info == {1: 0, 2: 0} and res.ok == [] and res.read_rounds == 0
    assert "마지막 확인 시점: 확인 못 함" in res.text()
    assert "비선택 슬롯의 원본 유지 여부는 확인하지 못했습니다" in res.text()


# ---------------------------------------------------------------- 비선택 슬롯 예상 밖 변경
def test_unselected_slot_unexpected_change_fails_and_is_not_rewritten(tmp_path):
    def hook(dev, slot, n, data):
        if slot == 1 and n == 1:
            corrupt(dev, 50)                     # 선택하지 않은 50번이 원본과 달라짐
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.mismatch == [50] and res.ok == [1, 2] and not res.success and not res.write_ok
    assert 50 not in dev.counts                  # 다시 쓰지 않는다(쓰기 시도 자체가 없음)
    assert "예상 밖 변경(선택하지 않은 슬롯이 원본과 달라짐) 1개: 13B" in res.text()


def test_unselected_change_found_only_in_later_read_is_also_reported(tmp_path):
    """재시도 뒤의 마지막 재읽기에서야 드러난 비선택 슬롯 변화도 보고한다."""
    def hook(dev, slot, n, data):
        if slot == 1 and n == 1:
            return False
        if slot == 1 and n == 2:
            corrupt(dev, 120)
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.mismatch == [120] and res.ok == [1, 2] and not res.success and 120 not in dev.counts


# ---------------------------------------------------------------- success 조건·기존 의미 유지
def test_success_requires_all_ok_no_unexpected_change_and_record(tmp_path, monkeypatch):
    dev, rows = two_slot_rows(tmp_path)
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.success and res.ok == [1, 2] and res.read_rounds == 1 and res.rewritten == {}
    monkeypatch.setattr(backup, "write_result", lambda f, t: (_ for _ in ()).throw(OSError("x")))
    (tmp_path / "again").mkdir()
    dev2, rows2 = two_slot_rows(tmp_path / "again")
    res2 = plan.apply_plan(dev2, rows2, [1, 2], backup_root=tmp_path / "bk2")
    assert res2.write_ok and not res2.record_ok and not res2.success


def test_no_ack_failure_and_skipped_keep_their_meaning(tmp_path):
    class NoAck(Scripted):
        def write_slot(self, slot, data):
            if slot == 1:
                return False
            return super().write_slot(slot, data)
    dev, rows = two_slot_rows(tmp_path)
    dev2 = NoAck()
    res = plan.apply_plan(dev2, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.failed == [1] and res.skipped == [2] and res.ok == [] and not res.write_ok
    assert dev2.slots[1] == dev.slots[1]        # 쓰지 못한 슬롯은 원본 그대로(예상 밖 변경 아님)
    assert res.mismatch == []
