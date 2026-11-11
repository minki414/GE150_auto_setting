"""모델·노브 목록(catalog*.json) 조회와 값 범위 검사. 활성 기종(models.active)의 파일을 읽는다.

catalog.json·catalog-ge150.json 은 tools/build_catalog.py 가 에디터의 모델 목록 XML 에서 만든 것입니다.
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

from . import models

# 모듈 순서 0~8 (.mo 의 effectModule "0"~"8")
MODULES = ["FX/COMP", "DS/OD", "AMP", "CAB", "NS GATE", "EQ", "MOD", "DELAY", "REVERB"]


def _data_path(filename: str):
    # PyInstaller 로 묶이면 임시 폴더(_MEIPASS) 아래 ge150/data 에 풀린다
    here = Path(getattr(sys, "_MEIPASS", "")) / "ge150" if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent
    return here / "data" / filename


@lru_cache(maxsize=None)
def _load_file(filename: str):
    return json.loads(_data_path(filename).read_text(encoding="utf-8"))["chains"]


def _load(profile=None):
    return _load_file((profile or models.active()).catalog_file)


def model_count(module: int, profile=None) -> int:
    return len(_load(profile)[module]["models"])


def model(module: int, type_: int, profile=None):
    """모델 사전(name, params, userSample). 범위 밖이면 None."""
    ms = _load(profile)[module]["models"]
    return ms[type_] if isinstance(type_, int) and 0 <= type_ < len(ms) else None


def model_name(module: int, type_: int) -> str:
    m = model(module, type_)
    return m["name"] if m else "알 수 없음(%s)" % type_


def params(module: int, type_: int, profile=None) -> list:
    """해당 모델의 노브 목록(순서 = 장비 P0.. 순서). paraPos 상속은 변환 때 이미 풀려 있다."""
    m = model(module, type_, profile)
    return m["params"] if m else []


def is_user_sample(module: int, type_: int) -> bool:
    """에디터 목록의 import=1 칸(Pro: AMP 55~74·CAB 26~45, GE150: CAB 26~35)."""
    m = model(module, type_)
    return bool(m and m["userSample"])


def raw_range(p: dict):
    """노브의 .mo 값(raw) 허용 범위 (최소, 최대).

    목록형은 0~개수-1. 숫자형은 최소값이 음수이면 0부터 (최대-최소)/단계까지(EQ -16~16 → 0~32),
    아니면 표시 범위 그대로(DELAY 40~2500). PITCH(-12~12, 0.1 단계 → 0~240) 는 실측 근거가 약해 장비 확인 필요.
    """
    if p["type"] == 1:
        return 0, len(p["options"]) - 1
    if p["min"] < 0:
        return 0, int(round((p["max"] - p["min"]) / p["step"]))
    return int(p["min"]), int(p["max"])


def display_range(p: dict):
    """숫자 노브의 '표시값' 범위(톤 JSON 에 적는 값). EQ 는 -16~16, 나머지는 raw 범위와 같다."""
    return int(p["min"]), int(p["max"])


def display_to_raw(p: dict, value: int) -> int:
    """표시값 -> .mo 값. 최소값이 음수인 노브(EQ)는 값 - 최소값(0dB -> 16), 아니면 그대로."""
    return int(value - p["min"]) if p["min"] < 0 else int(value)


def check_value(module: int, type_: int, name: str, value):
    """범위 검사. 문제가 없으면 None, 있으면 한국어 사유."""
    for p in params(module, type_):
        if p["name"] == name:
            if isinstance(value, bool) or not isinstance(value, int):
                return "%s 값이 정수가 아닙니다." % name
            lo, hi = raw_range(p)
            if not lo <= value <= hi:
                return "%s 값 %d 이(가) 범위(%d~%d)를 벗어났습니다." % (name, value, lo, hi)
            return None
    return "%s 노브가 이 모델에 없습니다." % name


def value_text(module: int, type_: int, name: str, value) -> str:
    """엑셀 표시용: 목록형은 글자, 음수 범위 숫자는 실제 표시값, 단위가 있으면 붙인다."""
    for p in params(module, type_):
        if p["name"] == name:
            if p["type"] == 1:
                opts = p["options"]
                return opts[value] if 0 <= value < len(opts) else str(value)
            v = value
            if p["min"] < 0:
                v = p["min"] + value * p["step"]
            txt = ("%.*f" % (p["decimals"], v)) if p["decimals"] else str(int(round(v)))
            return txt + (" " + p["suffix"] if p["suffix"] else "")
    return str(value)
