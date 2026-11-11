"""F단계(감사 E 반영): E01 읽기 오류 뒤 쓰기 중단, E02 성공한 모든 재읽기에서 비선택 슬롯 대조."""
import pytest

from ge150 import plan
from test_apply_state import corrupt, two_slot_rows

STOP_MSG = "읽기 오류가 있어 다시 쓰기를 멈췄습니다. 같은 폴더로 다시 적용하십시오."


def ack_only_first(dev, slot, n, data):
    return not (slot in (1, 2) and n == 1)          # 슬롯별 1번째 쓰기는 ACK 만 주고 저장하지 않음


class FlakyRead:
    """read_all 호출 번호가 fail_on 에 있으면 OSError(일시적 읽기 실패)."""

    def __init__(self, dev, fail_on):
        self.dev, self.fail_on, self.n = dev, set(fail_on), 0
        self.orig = dev.read_all
        dev.read_all = self

    def __call__(self):
        self.n += 1
        if self.n in self.fail_on:
            self.dev.log.append("read-fail")
            raise OSError("synthetic transient read failure")
        return self.orig()


# ---------------------------------------------------------------- E01
@pytest.mark.parametrize("restore", [False, True])
def test_e01_transient_read_failure_stops_all_further_writes(tmp_path, restore):
    """감사 E 재현: 최초 검증 읽기 1회 실패 → 확인용 읽기는 성공 → 예전에는 다시 써서 success=True 였다. 이제는 쓰기 0회·실패."""
    dev, rows = two_slot_rows(tmp_path, restore=restore)
    dev.hook = ack_only_first
    FlakyRead(dev, fail_on={2})                     # 1번=백업 읽기, 2번=최초 검증 읽기(실패), 3번=확인용 읽기(성공)
    sel = [1, 2] if not restore else [r.slot for r in rows if r.status == plan.ST_RESTORE]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk")
    writes = [e for e in dev.log if isinstance(e, tuple)]
    fail_at = dev.log.index("read-fail")
    assert not any(isinstance(e, tuple) for e in dev.log[fail_at:])      # 읽기 실패 이후 쓰기 0회
    assert len(writes) == len(sel) and res.rewritten == {}
    assert not res.success and not res.write_ok
    # 확인용 읽기 결과로 분류: 저장하지 않은 슬롯은 '저장 안 됨'(그 읽기 이후 쓰기 없음), 나머지는 완료
    assert set(res.not_saved) == {1, 2} and res.unknown == []
    assert res.read_rounds == 1
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert STOP_MSG in txt and "재읽기 1회 실패 후 다시 읽어 확인했습니다" in txt


def test_e01_read_failure_in_a_later_round_also_stops_rewrites(tmp_path):
    def hook(dev, slot, n, data):
        if slot == 1 and n == 1:
            return False
        if slot == 2 and n <= 2:
            return False                            # 01B 는 다시 써도(2번째) 저장되지 않음
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    # 읽기: 1 백업, 2 최초 검증(성공), 3 회차1 검증(실패), 4 확인용(성공) -> 01B 불일치가 남아도 회차2 다시 쓰기는 하지 않는다
    FlakyRead(dev, fail_on={3})
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert dev.counts == {1: 2, 2: 2}               # 01B 세 번째 쓰기(회차 2) 없음
    assert res.ok == [1] and res.not_saved == [2] and not res.success
    assert STOP_MSG in res.text() and res.read_rounds == 2


def test_e01_both_reads_fail_is_unknown_and_stops(tmp_path):
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = ack_only_first
    FlakyRead(dev, fail_on={2, 3})
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.unknown == [1, 2] and res.read_rounds == 0 and dev.counts == {1: 1, 2: 1}
    assert STOP_MSG in res.text() and not res.success


def test_e01_no_read_error_still_rewrites_as_before(tmp_path):
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = ack_only_first
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.success and res.rewritten == {1: 1, 2: 1} and STOP_MSG not in res.text()


# ---------------------------------------------------------------- E02
@pytest.mark.parametrize("restore", [False, True])
def test_e02_unselected_change_seen_in_middle_read_is_recorded_even_if_restored(tmp_path, restore):
    """감사 E 재현: 중간 읽기에서 비선택 50번이 달라졌다가 마지막에 원래대로 → 기록 + success=False, 50번에는 쓰지 않음."""
    state = {}

    def hook(dev, slot, n, data):
        if slot == 1 and n == 1:
            state["orig50"] = dev.slots[50]
            corrupt(dev, 50)                         # 01A 첫 쓰기: ACK 만 주고 비선택 50번을 바꿈
            return False
        if slot == 1 and n == 2:
            dev.slots[50] = state["orig50"]          # 01A 다시 쓰기: 저장하면서 50번을 원본으로 회복
        return True
    dev, rows = two_slot_rows(tmp_path, restore=restore)
    dev.hook = hook
    sel = [1, 2] if not restore else [r.slot for r in rows if r.status == plan.ST_RESTORE and r.slot in (1, 2)]
    res = plan.apply_plan(dev, rows, sel, backup_root=tmp_path / "bk")
    assert dev.read_all()[50] == state["orig50"]     # 최종 스냅샷은 원본과 같다
    assert res.mismatch == [50] and res.mismatch_info == {50: (1, True)}
    assert not res.success and not res.write_ok
    assert 50 not in dev.counts                      # 그 슬롯에 쓰지 않는다(복구 쓰기 없음)
    txt = (res.backup_dir / "결과.txt").read_text(encoding="utf-8")
    assert "13B(관측 회차 1, 마지막 재읽기에서는 원래대로)" in txt
    assert res.ok == sel                             # 선택 슬롯 자체는 모두 완료


def test_e02_first_observed_round_is_kept_and_final_diff_marked(tmp_path):
    """회차 2에서 처음 달라지고 마지막까지 달라져 있는 경우: 관측 회차 2, '원래대로' 표시 없음."""
    def hook(dev, slot, n, data):
        if slot == 1 and n == 1:
            return False
        if slot == 1 and n == 2:
            corrupt(dev, 120)                        # 다시 쓰면서 120번을 훼손(회차 2 읽기에서 처음 관측)
        return True
    dev, rows = two_slot_rows(tmp_path)
    dev.hook = hook
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.mismatch == [120] and res.mismatch_info == {120: (2, False)}
    assert "30D(관측 회차 2)" in res.text() and "원래대로" not in res.text().split("예상 밖 변경")[1].split("\n")[0]
    assert 120 not in dev.counts and not res.success


def test_e02_no_unexpected_change_means_no_record_and_success(tmp_path):
    dev, rows = two_slot_rows(tmp_path)
    res = plan.apply_plan(dev, rows, [1, 2], backup_root=tmp_path / "bk")
    assert res.success and res.mismatch == [] and res.mismatch_info == {}
    assert "예상 밖 변경(선택하지 않은 슬롯이 원본과 달라짐) 0개: 없음" in res.text()
