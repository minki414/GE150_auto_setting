"""에디터의 모델 목록 XML -> ge150/data/catalog*.json (빌드 시점 변환).

사용: python tools/build_catalog.py [xml 경로] [출력 경로]
인자 없이 실행하면 두 기종(Pro: catalog.json, GE150: catalog-ge150.json)을 모두 만듭니다.
XML 원본은 저장소에 없으므로(공개하지 않음) 없는 기종은 건너뛰고 안내만 합니다.
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "ge150" / "data"
REF = HERE.parent.parent / "01_분석" / "reference"   # 개발용: XML 원본이 있는 폴더(공개 저장소에는 없음 — 없으면 건너뜀)
DEFAULTS = (  # (XML, 출력)
    (REF / "model-catalog-en.xml", DATA / "catalog.json"),
    (REF / "model-catalog-ge150-en.xml", DATA / "catalog-ge150.json"),
)


def parse_para(el):
    """<para type=0>min_max_step_decimals_suffix / type=1>옵션_옵션… 를 사전으로 바꾼다."""
    name, typ, text = el.get("name"), int(el.get("type")), (el.text or "")
    if typ == 0:
        mn, mx, step, dec, suffix = (text.split("_") + [""] * 5)[:5]
        return {"name": name, "type": 0, "min": float(mn), "max": float(mx),
                "step": float(step), "decimals": int(dec), "suffix": suffix.strip()}
    if typ == 1:
        return {"name": name, "type": 1, "options": text.split("_")}
    raise ValueError("지원하지 않는 para type: %s" % typ)


def build(xml_path):
    chains = []
    for chain in ET.parse(xml_path).getroot():
        models, own = [], []
        for m in chain:
            paras = [parse_para(p) for p in m.findall("para")]
            own.append(paras)
            models.append({
                "name": re.sub(r"^\d+:\s*", "", m.get("name")),
                "paraPos": int(m.get("paraPos")),
                "userSample": m.get("import") == "1",
            })
        for i, model in enumerate(models):
            # para 가 비면 paraPos(1부터)번째 모델의 목록을 쓴다
            src = own[i] or own[model["paraPos"] - 1]
            if not src:
                raise ValueError("%s/%s: para 를 찾을 수 없음" % (chain.get("name"), model["name"]))
            names = [p["name"] for p in src]
            if len(set(names)) != len(names):
                raise ValueError("%s/%s: para 이름 중복" % (chain.get("name"), model["name"]))
            model["params"] = src
        chains.append({"name": chain.get("name"), "models": models})
    return {"chains": chains}


def write(xml, out):
    cat = build(xml)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    print("모듈 %d개, 모델 %d개 -> %s" % (len(cat["chains"]), sum(len(c["models"]) for c in cat["chains"]), out.name))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        write(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULTS[0][1])
    else:
        for xml, out in DEFAULTS:
            if xml.is_file():
                write(xml, out)
            else:
                print("건너뜀(XML 없음, 저장된 %s 를 그대로 씁니다): %s" % (out.name, xml.name))
