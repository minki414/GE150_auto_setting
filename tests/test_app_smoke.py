"""창을 화면 없이(offscreen) 띄워 기본 흐름을 가짜 장비로 끝까지 돌려 본다."""
import os
import shutil
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import MO_DIR  # noqa: E402


def wait(app, win, cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_window_flow(tmp_path):
    import app as appmod
    app = QApplication.instance() or QApplication([])
    win = appmod.MainWindow(fake=True, backup_root=tmp_path / "bk")
    msgs = []
    win.ask_yes_no = lambda t: (msgs.append(t), True)[1]
    win.info = lambda t: msgs.append(t)
    win.show()
    win.connect_and_read()
    assert wait(app, win, lambda: len(win.dev_data) == 200 and not win.busy)
    assert "연결됨" in win.lbl_dev.text() and win.table.rowCount() == 200

    tone = tmp_path / "tone"
    tone.mkdir()
    for p in list(MO_DIR.glob("50*.mo")) + list(MO_DIR.glob("Dual*.mo")):
        shutil.copy(p, tone)
    win.set_folder(tone)
    changed = [r.slot for r in win.rows if r.status == "변경"]
    assert changed == [197, 198, 199, 200] and win.table.rowCount() == 201   # 50A~50D 변경 + 슬롯 미지정 1행
    assert win.lbl_count.text() == "바꿀 슬롯: 4개"
    win.btn_sel_none.click()
    assert win.lbl_count.text() == "바꿀 슬롯: 0개"
    win.btn_sel_changed.click()
    assert win.lbl_count.text() == "바꿀 슬롯: 4개"

    win.apply()
    assert wait(app, win, lambda: msgs and len(msgs) >= 2 and not win.busy and len(win.dev_data) == 200)
    wait(app, win, lambda: all(r.status != "변경" for r in win.rows))
    assert "완료" in msgs[-1] and win.dev.written == [197, 198, 199, 200]
    assert [r.status for r in win.rows[196:200]] == ["동일"] * 4
    assert len(list((tmp_path / "bk").glob("*/device.bin"))) == 1

    # 슬롯 미지정 파일에 슬롯을 지정하면 표가 갱신된다
    cb = win.table.cellWidget(200, 1)
    cb.setCurrentIndex(5)
    assert win.rows[4].new_name == "Dual Lead"

    # 장비→.mo 저장, 엑셀로 보기, 백업에서 되돌리기(폴더 지정)
    win.pick_dir = lambda start="": str(tmp_path / "exp")
    (tmp_path / "exp").mkdir()
    win.export_mo()
    assert len(list((tmp_path / "exp").glob("*.mo"))) == 200
    opened = []
    win.pick_save = lambda d: str(tmp_path / "v.xlsx")
    win.open_file = opened.append
    win.show_excel()
    assert opened and (tmp_path / "v.xlsx").exists()
    win.pick_dir = lambda start="": str(next((tmp_path / "bk").iterdir()))
    win.restore_from_backup()
    assert win.ed_folder.text().startswith(str(tmp_path / "bk"))
    win.close()
