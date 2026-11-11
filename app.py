"""GE150 / GE150 Pro 프리셋 적용기 — 창. 실행 인자 --fake 이면 가짜 Pro 장비(녹화 자료), --fake=ge150 이면 가짜 GE150 장비를 쓴다.

USB 작업(읽기·쓰기)은 모두 QThread 에서 해서 창이 멈추지 않게 한다.
"""
import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ge150 import backup, codec, device, excel, export, models, mofile, plan, tonepack
from ge150 import protocol as P

COLS = ["", "슬롯", "지금 장비", "바꿀 톤", "상태", "경고"]


class Worker(QThread):
    """함수를 별도 스레드에서 실행하고 진행(progress)·완료(done)·실패(failed)를 신호로 알린다."""
    progress = Signal(int, int, str, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as ex:  # noqa: 스레드 밖으로 예외를 내보내지 않고 화면에 사유를 보인다
            self.failed.emit(str(ex))


class MainWindow(QMainWindow):
    def __init__(self, fake=False, backup_root=None, fake_profile=None):
        super().__init__()
        self.fake = fake
        self.fake_profile = fake_profile or models.PRO   # 가짜 장비가 흉내 낼 기종
        self.backup_root = Path(backup_root) if backup_root else backup.default_backup_root()
        self.dev = None
        self.dev_data = {}
        self.user_names = None
        self.rows, self.unassigned, self.overrides = [], [], {}
        self.worker = None
        self.stop_flag = False
        self._closing = False
        self._in_finish = False        # 결과 요약 창이 열려 있는 동안 True
        self._worker_finished = False  # 작업 스레드가 끝났는지(닫는 중일 때)
        self.setAcceptDrops(True)  # 톤 JSON·폴더 끌어다 놓기
        self.setWindowTitle("GE150 / GE150 Pro 프리셋 적용기")
        self.resize(980, 680)
        self._build_ui()
        self.set_busy(False)

    # --- 화면 구성 ---------------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        top = QHBoxLayout()
        self.lbl_dev = QLabel("장비: 확인 전")
        self.btn_reread = QPushButton("장비 다시 읽기")
        top.addWidget(self.lbl_dev, 1)
        top.addWidget(self.btn_reread)
        v.addLayout(top)
        fl = QHBoxLayout()
        fl.addWidget(QLabel("톤 데이터 폴더:"))
        self.ed_folder = QLineEdit()
        self.ed_folder.setReadOnly(True)
        self.btn_folder = QPushButton("폴더 선택")
        self.btn_json = QPushButton("톤 JSON 불러오기")
        self.cb_source = QComboBox()
        self.cb_source.addItems(["장비 내용", "폴더 내용"])
        self.btn_excel = QPushButton("엑셀로 보기")
        for w in (self.ed_folder, self.btn_folder, self.btn_json, self.cb_source, self.btn_excel):
            fl.addWidget(w, 1 if w is self.ed_folder else 0)
        v.addLayout(fl)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, w in enumerate((34, 70, 190, 190, 80, 330)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._update_count)
        v.addWidget(self.table, 1)
        sel = QHBoxLayout()
        self.btn_sel_changed = QPushButton("변경된 것만 선택")
        self.btn_sel_all = QPushButton("전체 선택")
        self.btn_sel_none = QPushButton("전체 해제")
        self.lbl_count = QLabel("바꿀 슬롯: 0개")
        for w in (self.btn_sel_changed, self.btn_sel_all, self.btn_sel_none):
            sel.addWidget(w)
        sel.addStretch(1)
        sel.addWidget(self.lbl_count)
        v.addLayout(sel)
        act = QHBoxLayout()
        self.btn_apply = QPushButton("장비에 적용")
        self.btn_stop = QPushButton("중단")
        self.btn_restore = QPushButton("백업에서 되돌리기")
        self.btn_export = QPushButton("장비→.mo 저장")
        self.btn_export_json = QPushButton("장비→톤 JSON 저장")
        act.addWidget(self.btn_apply)
        act.addWidget(self.btn_stop)
        act.addStretch(1)
        act.addWidget(self.btn_restore)
        act.addWidget(self.btn_export)
        act.addWidget(self.btn_export_json)
        v.addLayout(act)
        pr = QHBoxLayout()
        self.bar = QProgressBar()
        self.lbl_prog = QLabel("대기")
        pr.addWidget(self.bar, 1)
        pr.addWidget(self.lbl_prog)
        v.addLayout(pr)
        self.btn_reread.clicked.connect(self.connect_and_read)
        self.btn_folder.clicked.connect(self.choose_folder)
        self.btn_json.clicked.connect(self.choose_json)
        self.btn_excel.clicked.connect(self.show_excel)
        self.btn_sel_changed.clicked.connect(lambda: self._select(lambda r: r.status in (plan.ST_CHANGE, plan.ST_RESTORE)))
        self.btn_sel_all.clicked.connect(lambda: self._select(lambda r: r.data is not None))
        self.btn_sel_none.clicked.connect(lambda: self._select(lambda r: False))
        self.btn_apply.clicked.connect(self.apply)
        self.btn_stop.clicked.connect(self._request_stop)
        self.btn_restore.clicked.connect(self.restore_from_backup)
        self.btn_export.clicked.connect(self.export_mo)
        self.btn_export_json.clicked.connect(self.export_json)

    # --- 대화상자(시험에서 바꿔 끼울 수 있게 메서드로 분리) -----------------------
    def ask_yes_no(self, text) -> bool:
        return QMessageBox.question(self, "확인", text) == QMessageBox.Yes

    def info(self, text):
        QMessageBox.information(self, "안내", text)

    def ask_model(self):
        """장비도 없고 톤 JSON 에도 기종이 없을 때 변환 대상을 묻는다. 고른 기종(Profile) 또는 취소 시 None."""
        box = QMessageBox(self)
        box.setWindowTitle("기종 선택")
        box.setText("연결된 장비가 없고 톤 JSON 에 기종(device)이 없습니다.\n어느 기종용으로 변환하시겠습니까?")
        btns = {box.addButton(p.label, QMessageBox.AcceptRole): p for p in models.PROFILES}
        box.addButton("취소", QMessageBox.RejectRole)
        box.exec()
        return btns.get(box.clickedButton())

    def pick_dir(self, start="") -> str:
        return QFileDialog.getExistingDirectory(self, "폴더 선택", start)

    def pick_save(self, default) -> str:
        return QFileDialog.getSaveFileName(self, "엑셀 파일 저장", default, "엑셀 (*.xlsx)")[0]

    def pick_save_json(self, default) -> str:
        return QFileDialog.getSaveFileName(self, "톤 JSON 저장", default, "톤 JSON (*.json)")[0]

    def pick_files(self) -> list:
        return QFileDialog.getOpenFileNames(self, "톤 JSON 선택(여러 개 가능)", self.ed_folder.text(), "톤 JSON (*.json)")[0]

    def show_issues(self, title, issues):
        """오류·경고 목록 창(슬롯·모듈·노브·사유). 시험에서는 이 메서드를 바꿔 끼운다."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(820, 420)
        v = QVBoxLayout(dlg)
        t = QTableWidget(len(issues), 5)
        t.setHorizontalHeaderLabels(["파일", "슬롯", "모듈", "노브", "사유"])
        for r, i in enumerate(issues):
            for c, text in enumerate((i.file, i.slot, i.module, i.knob, i.reason)):
                t.setItem(r, c, QTableWidgetItem(text))
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(t)
        ok = QPushButton("확인")
        ok.clicked.connect(dlg.accept)
        v.addWidget(ok)
        dlg.exec()

    def editor_reason(self):
        """A05: 공식 에디터가 실행 중이거나 확인 불가이면 안내 문구. 가짜 장비에서는 검사하지 않는다."""
        return None if self.fake else device.editor_blocked_reason()

    def open_file(self, path):
        os.startfile(str(path))  # noqa: Windows 기본 프로그램으로 연다

    # --- 상태 ----------------------------------------------------------------
    def set_busy(self, busy: bool):
        self.busy = busy
        for b in (self.btn_reread, self.btn_folder, self.btn_json, self.btn_excel, self.btn_apply, self.btn_restore, self.btn_export,
                  self.btn_export_json, self.btn_sel_changed, self.btn_sel_all, self.btn_sel_none):
            b.setEnabled(not busy)
        self.btn_stop.setEnabled(False)

    def _run(self, fn, on_done, label="작업 중"):
        self.set_busy(True)
        self.lbl_prog.setText(label)
        self.worker = Worker(fn)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(lambda r: self._finish(on_done, r))
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _finish(self, on_done, res):
        """B02: 닫는 중이어도 결과 요약 창은 반드시 띄운다(미확인·미시도·기록 실패를 사용자에게 전달). 닫기는 그 뒤에 한다."""
        self.set_busy(False)
        self._in_finish = True
        try:
            on_done(res)
        finally:
            self._in_finish = False
        if self._closing and self._worker_finished:
            self._do_close()

    def _on_progress(self, i, n, lab, msg):
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i)
        self.lbl_prog.setText("%d / %d  %s %s" % (i, n, lab, msg))

    def _reset_device(self, why=""):
        """A06: 연결 실패·끊김 뒤에는 장비 객체와 이전 장비 자료·표를 비워 다음 [장비 다시 읽기]가 새로 연결하게 한다."""
        dev, self.dev = self.dev, None
        if dev is not None:
            try:
                dev.close()
            except Exception:  # noqa
                pass
        self.dev_data, self.user_names = {}, None
        self.lbl_dev.setText("장비: ○ 연결 안 함%s" % (" — " + why if why else ""))
        self.refresh_plan()

    def _on_failed(self, msg):
        self.set_busy(False)
        self.lbl_prog.setText("실패")
        self._reset_device(msg)
        if not self._closing:
            self.info("실패: " + msg)

    def _request_stop(self):
        self.stop_flag = True
        self.btn_stop.setEnabled(False)

    # --- 장비 읽기 ---------------------------------------------------------------
    def connect_and_read(self):
        reason = self.editor_reason()
        if reason:
            self._reset_device(reason)
            self.info(reason)
            return

        def job(emit):
            if self.dev is None:
                kw = {} if self.fake_profile is models.PRO else {"profile": self.fake_profile}   # 기본(Pro)은 인자 없이 만든다
                dev = device.FakeDevice(**kw) if self.fake else device.Device()
                dev.connect()
                self.dev = dev
            emit(0, 1, "", "장비 읽는 중")
            data = self.dev.read_all()
            try:
                names = self.dev.user_samples()
            except Exception:  # noqa: 사용자 샘플 이름은 경고용이라 못 읽어도 계속
                names = None
            return data, names

        def done(res):
            self.dev_data, self.user_names = res
            prof = getattr(self.dev, "profile", None) or models.active()
            models.set_active(prof)   # 표·검사·변환이 연결된 장비의 기종 기준이 되게 한다
            self.lbl_dev.setText("장비: ● %s 연결됨%s%s" % (prof.label, " (가상 장비)" if self.fake else "",
                                                          (" — " + prof.note) if prof.note else ""))
            self.lbl_prog.setText("장비 읽기 완료")
            self.refresh_plan()

        self._run(job, done, "장비 읽는 중")

    # --- 계획 표 -----------------------------------------------------------------
    def refresh_plan(self):
        folder = self.ed_folder.text()
        if not self.dev_data:  # 장비 자료가 없으면(연결 실패 포함) 이전 표를 지운다
            self.rows, self.unassigned = [], []
        elif folder:
            try:
                self.rows, self.unassigned = plan.build_plan(self.dev_data, folder, self.overrides, self.user_names)
            except (ValueError, OSError) as ex:
                self.ed_folder.setText("")
                self.info("폴더를 열 수 없습니다: %s" % ex)
                self.rows = [plan.Row(slot=s, device_name=codec.decode_name(self.dev_data[s])) for s in range(1, 201)]
                self.unassigned = []
        else:  # 폴더를 고르기 전에는 장비 내용만 보여 준다
            self.rows = [plan.Row(slot=s, device_name=codec.decode_name(self.dev_data[s])) for s in range(1, 201)]
            self.unassigned = []
        self._fill_table()

    def _fill_table(self):
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(self.rows) + len(self.unassigned))
        for r, row in enumerate(self.rows):
            chk = QTableWidgetItem()
            writable = row.data is not None and not row.errors
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled if writable else Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if row.status in (plan.ST_CHANGE, plan.ST_RESTORE) else Qt.Unchecked)
            t.setItem(r, 0, chk)
            vals = [row.label, row.device_name, row.new_name or "—", row.status, " / ".join(row.errors + row.warnings)]
            for c, text in enumerate(vals, 1):
                t.setItem(r, c, QTableWidgetItem(text))
        for k, e in enumerate(self.unassigned):
            r = len(self.rows) + k
            t.setItem(r, 0, QTableWidgetItem(""))
            cb = QComboBox()
            cb.addItem("슬롯 미지정")
            cb.addItems([P.slot_label(s) for s in range(1, 201)])
            cb.currentIndexChanged.connect(lambda idx, path=e.path: self._assign(path, idx))
            t.setCellWidget(r, 1, cb)
            for c, text in enumerate(["", e.name or e.path.name, "슬롯 미지정", " / ".join(e.errors)], 2):
                t.setItem(r, c, QTableWidgetItem(text))
        t.blockSignals(False)
        self._update_count()

    def _assign(self, path, idx):
        if idx > 0:
            self.overrides[path] = idx
            self.refresh_plan()

    def _checked_slots(self):
        out = []
        for r, row in enumerate(self.rows):
            it = self.table.item(r, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append(row.slot)
        return out

    def _update_count(self, *_):
        self.lbl_count.setText("바꿀 슬롯: %d개" % len(self._checked_slots()))

    def _select(self, pred):
        self.table.blockSignals(True)
        for r, row in enumerate(self.rows):
            it = self.table.item(r, 0)
            if it is not None and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if pred(row) else Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_count()

    # --- 폴더·되돌리기 ---------------------------------------------------------------
    def set_folder(self, folder):
        if plan.PARTIAL_RE.search(Path(folder).name):  # C02: 변환 실패로 남은 불완전한 폴더는 톤 폴더로 열지 않는다
            self.info("불완전한 변환 폴더(%s)입니다. 톤 폴더로 열 수 없습니다. 톤 JSON 을 다시 변환해 주십시오." % Path(folder).name)
            return
        self.ed_folder.setText(str(folder))
        self.overrides = {}
        self.refresh_plan()

    def choose_folder(self):
        d = self.pick_dir(self.ed_folder.text())
        if d:
            self.set_folder(d)

    def restore_from_backup(self):
        # 백업 폴더를 톤 데이터 폴더로 고르는 것과 같다(같은 흐름으로 원본을 되돌린다)
        d = self.pick_dir(str(self.backup_root))
        if d:
            self.set_folder(d)

    # --- 적용 --------------------------------------------------------------------
    def apply(self):
        slots = self._checked_slots()
        if not self.dev_data or not slots:
            self.info("적용할 슬롯이 없습니다. 장비를 읽고 폴더를 고른 뒤 슬롯을 선택해 주십시오.")
            return
        reason = self.editor_reason()  # A05: 적용 직전 재검사
        if reason:
            self.info(reason)
            return
        note = models.active().note
        if not self.ask_yes_no("%d개 슬롯을 바꿉니다. 바꾸기 전 장비 전체를 백업합니다.\n"
                               "예상 시간: 약 %d초(슬롯마다 안전하게 저장되도록 간격을 두고, 저장을 확인한 뒤 필요하면 다시 씁니다). "
                               "진행하시겠습니까?%s" % (len(slots), plan.estimate_seconds(len(slots)), ("\n" + note) if note else "")):
            return
        self.stop_flag = False
        rows = self.rows

        def job(emit):
            return plan.apply_plan(self.dev, rows, slots, self.backup_root, progress=emit,
                                   should_stop=lambda: self.stop_flag, guard=self.editor_reason)

        def done(res):
            self.info("%s\n백업 폴더 위치를 확인해 주십시오." % res.text())
            if not self._closing:
                self.connect_and_read()  # 장비 내용으로 표를 다시 채운다

        self._run(job, done, "적용 중")
        self.btn_stop.setEnabled(True)

    # --- 내보내기·엑셀 --------------------------------------------------------------
    def export_mo(self):
        d = self.pick_dir()
        if not d or not self.dev_data:
            return
        for s in range(1, 201):
            mofile.write_mo(d, s, codec.decode(self.dev_data[s]), unique=True)
        self.info("장비 200개를 저장했습니다: %s" % d)

    def export_json(self):
        """장비 200개를 톤 JSON 한 파일로 저장한다(곡 목록 없이). 저장 위치 대화상자가 덮어쓰기 확인을 한다."""
        if not self.dev_data:
            self.info("먼저 장비를 읽어 주십시오.")
            return
        prof = getattr(self.dev, "profile", None) or models.active()
        path = self.pick_save_json(str(Path(self.ed_folder.text() or ".") / ("%s_톤.json" % prof.key)))
        if not path:
            return
        warns = []
        try:
            doc = export.device_to_doc(self.dev_data, prof, "%s 장비 내보내기" % prof.label, warnings=warns)
            export.write_doc(path, doc, overwrite=True)
        except OSError as ex:
            self.info("저장하지 못했습니다: %s" % ex)
            return
        msg = "장비 %d개를 톤 JSON 으로 저장했습니다: %s" % (len(doc["presets"]), path)
        if warns:   # 사용자 샘플 칸·PITCH 모델처럼 다시 가져올 때 오류가 되는 내용(저장은 그대로 했음)
            msg += "\n다시 가져올 때 오류가 되는 내용이 %d건 있습니다.\n%s%s" % (len(warns), "\n".join(warns[:10]), "\n..." if len(warns) > 10 else "")
        self.info(msg)

    def show_excel(self):
        if not self.dev_data:
            self.info("먼저 장비를 읽어 주십시오.")
            return
        source = "device" if self.cb_source.currentIndex() == 0 else "folder"
        path = self.pick_save(str(Path(self.ed_folder.text() or ".") / ("%s_프리셋.xlsx" % models.active().key)))
        if not path:
            return
        excel.export_xlsx(path, self.rows, self.dev_data, source, meta=tonepack.load_meta(self.ed_folder.text()))
        self.open_file(path)


    # --- 톤 JSON: 끌어다 놓기 / 불러오기 -----------------------------------------------
    def load_tone_json(self, paths):
        """톤 JSON 여러 개를 하나로 합쳐 .mo 로 변환하고, 성공하면 출력 폴더를 톤 데이터 폴더로 연다."""
        target = self.target_model(paths)
        if target is None:
            self.lbl_prog.setText("톤 JSON 변환 취소")
            return None
        try:
            res = tonepack.convert(paths, profile=target)
        except Exception as ex:  # noqa: 변환은 예외 없이 오류를 돌려주지만, 예기치 않은 예외도 화면에는 오류로 알린다(B03)
            res = tonepack.ConvertResult(errors=[tonepack.Issue("변환 중 예기치 않은 오류: %s" % ex)])
        if res.errors:
            self.lbl_prog.setText("톤 JSON 오류 %d건" % len(res.errors))
            self.show_issues("톤 JSON 오류 %d건 — .mo 를 만들지 않았습니다" % len(res.errors), res.errors)
            return res
        self.set_folder(res.out_dir)
        self.lbl_prog.setText("톤 JSON 변환 완료: .mo %d개" % len(res.files))
        if res.warnings:
            self.show_issues("톤 JSON 경고 %d건 — 변환은 마쳤습니다" % len(res.warnings), res.warnings)
        return res

    def target_model(self, paths):
        """변환 대상 기종: 연결된 장비 > 톤 JSON 첫 파일의 device > 물어봄(취소하면 None)."""
        if self.dev is not None and self.dev_data:
            return getattr(self.dev, "profile", None) or models.active()
        return tonepack.peek_device(paths) or self.ask_model()

    def choose_json(self):
        paths = self.pick_files()
        if paths:
            self.load_tone_json(paths)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.handle_dropped([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()

    def handle_dropped(self, paths):
        if self.busy:
            self.info("작업 중입니다. 끝난 뒤 다시 끌어다 놓아 주십시오.")
            return
        ps = [Path(p) for p in paths]
        if ps and all(p.is_file() and p.suffix.lower() == ".json" for p in ps):
            self.load_tone_json(ps)
        elif len(ps) == 1 and ps[0].is_dir():
            self.set_folder(ps[0])  # 폴더는 기존처럼 톤 데이터 폴더로 연다
        else:
            self.info(".json 파일(여러 개 가능) 또는 폴더 1개를 끌어다 놓아 주십시오.")

    # --- 창 닫기(A03) -------------------------------------------------------------
    def closeEvent(self, e):
        w = self.worker
        if w is not None and w.isRunning():
            if not self.ask_yes_no("적용 중입니다. 지금 슬롯까지 마치고 멈춘 뒤 닫습니다."):
                e.ignore()
                return
            # 정상 중단 경로와 같게: 중단 요청 -> 스레드 종료를 기다림(결과 저장은 apply_plan 이 함) -> 장비 닫기 -> 창 닫기
            self._closing = True
            self._worker_finished = False
            self.stop_flag = True
            self.btn_stop.setEnabled(False)
            self.lbl_prog.setText("지금 슬롯까지 마친 뒤 닫는 중")
            w.finished.connect(self._close_after_worker)
            e.ignore()
            return
        self._release_device()
        e.accept()

    def _close_after_worker(self):
        self._worker_finished = True
        if self._in_finish:   # 결과 요약 창이 열려 있으면 그 창을 닫은 뒤 _finish 가 마무리한다
            return
        self._do_close()

    def _do_close(self):
        if not self._closing:
            return
        if self.worker is not None:
            self.worker.wait()  # finished 신호 직후에도 isRunning 이 잠깐 참일 수 있어 완전히 끝날 때까지 기다린다
        self._release_device()
        self._closing = False
        self.close()

    def _release_device(self):
        dev, self.dev = self.dev, None
        if dev is not None:
            try:
                dev.close()
            except Exception:  # noqa
                pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    fakes = [a for a in argv if a == "--fake" or a.startswith("--fake=")]
    fake = bool(fakes)
    fake_profile = models.by_key(fakes[0].partition("=")[2]) if fake else None   # --fake = Pro, --fake=ge150 = GE150
    app = QApplication([sys.argv[0]])
    win = MainWindow(fake=fake, fake_profile=fake_profile)
    win.show()
    paths = [a for a in argv if not a.startswith("--")]
    if paths:  # exe 아이콘에 파일을 끌어다 놓거나 명령줄로 준 경로: 창에 끌어다 놓은 것과 같게 처리(포장 후 확인에도 사용)
        win.handle_dropped(paths)
    win.connect_and_read()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
