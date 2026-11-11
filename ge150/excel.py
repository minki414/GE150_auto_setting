"""엑셀로 보기(읽기용). 시트 2개: 요약 / 톤 상세. 모델 번호는 모델 이름, 목록형 값은 글자로 바꿔 표시한다."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import catalog, codec
from . import protocol as P

# 사용자 의도 [요구]: 같은 표를 나중에 .mo 로 되돌리는 변환기를 붙일 수 있게 열 구성을 고정한다.
SUMMARY_COLS = ["슬롯", "지금 장비 이름", "바꿀 이름", "상태", "경고", "곡", "아티스트", "역할"]  # 곡·아티스트·역할은 톤 JSON 에서 온 경우만 값이 있다


UNREADABLE = "해석 불가(원본 바이트 그대로 복원)"


def _append(ws, row):
    """한 줄을 추가하되 글자는 항상 글자 칸으로 저장한다 — 장비 이름·곡명이 '=' 로 시작해도 수식이 되지 않게
    (openpyxl 은 '=' 로 시작하는 글자를 수식으로 저장한다)."""
    ws.append(row)
    for c in ws[ws.max_row]:
        if isinstance(c.value, str) and c.data_type == "f":
            c.data_type = "s"


def mo_to_cells(mo: dict) -> list:
    """톤 1개 -> [이름, (모듈마다) 상태·모델·노브, EXP] 칸 목록."""
    cells = [mo["fileInfo"]["preset_name"].rstrip()]
    for i in range(codec.MODULE_COUNT):
        m = mo["effectModule"][str(i)]
        knobs = ", ".join("%s=%s" % (n, catalog.value_text(i, m["TYPE"], n, v)) for n, v in m["Data"].items())
        cells += ["ON" if m["SWITCH"] else "OFF", catalog.model_name(i, m["TYPE"]), knobs]
    cells.append(", ".join("%s=%s" % (k, mo["Exp"][k]) for k in codec.EXP_ORDER))
    return cells


def detail_header() -> list:
    h = ["슬롯", "이름"]
    for label in catalog.MODULES:
        h += [label + " 상태", label + " 모델", label + " 노브"]
    return h + ["EXP"]


def export_xlsx(path, rows, device_data: dict, source: str = "device", meta=None):
    """rows = plan.Row 목록. source = 'device'(장비 내용) 또는 'folder'(폴더 내용).

    meta = {슬롯: {song, artist, role}} (tonepack.load_meta). .mo 에는 이 정보가 없어 변환 폴더의 tonepack-meta.json 에서 읽는다."""
    meta = meta or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    ws.append(SUMMARY_COLS)
    for r in rows:
        m = meta.get(r.slot, {}) if r.new_name else {}
        _append(ws, [r.label, r.device_name, r.new_name or "—", r.status, " / ".join(r.errors + r.warnings),
                   m.get("song", ""), m.get("artist", ""), m.get("role", "")])
    ws2 = wb.create_sheet("톤 상세")
    ws2.append(detail_header())
    for r in rows:
        raw = None
        if source == "device":
            raw = device_data[r.slot]
        elif r.exact and r.data is not None:   # B04: 백업 복원 행은 실제로 쓸 원본 바이트를 decode 해서 보여 준다
            raw = r.data
        if raw is not None:
            try:
                _append(ws2, [r.label] + mo_to_cells(codec.decode(raw)))
            except (codec.CodecError, ValueError, KeyError):
                _append(ws2, [r.label, UNREADABLE])   # 해석 안 되는 슬롯도 한 줄은 남긴다
            continue
        mo = r.entry.mo if source == "folder" and r.entry and not r.errors else None
        if mo is not None:
            _append(ws2, [r.label] + mo_to_cells(mo))
    fill = PatternFill("solid", fgColor="DDDDDD")
    for sheet in (ws, ws2):
        for c in sheet[1]:
            c.font, c.fill = Font(bold=True), fill
        sheet.freeze_panes = "B2"
        for col in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(col)].width = 14 if col != 2 else 22
    wb.save(path)
    return path
