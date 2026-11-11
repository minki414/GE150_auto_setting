"""명령줄 시험 도구. 장비 쓰기는 write 에 --allow-write 를 줄 때만 한다.

사용:
  python cli.py [--fake] read
  python cli.py [--fake] backup <폴더>
  python cli.py [--fake] export <폴더>
  python cli.py [--fake] plan <톤폴더>
  python cli.py [--fake] write <톤폴더> --slots 50D [50C ...] --allow-write
  python cli.py convert <톤json...> [--out <폴더>] [--device GE150|GE150PRO]   (장비 연결 없이 톤 JSON -> .mo)
  python cli.py export-json <출력.json> [--from-backup <백업폴더>] [--songs <곡목록.txt>] [--title ...]   (장비 또는 백업 -> 톤 JSON)
  python cli.py to-ge150 <Pro톤.json> --out <GE150톤.json> [--notes <내역.txt>]   (Pro 톤 JSON -> GE150 톤 JSON, 장비 연결 없음)
장비 명령은 연결한 장비의 기종(GE150 Pro / GE150)을 읽기 응답 크기로 자동 판별합니다.
convert 의 기종 우선순위: --device > JSON 의 device > GE150PRO.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from ge150 import backup, codec, convert_model, device, export, models, mofile, plan, tonepack
from ge150 import protocol as P


def editor_guard(fake: bool):
    """A05: 가짜 장비가 아니면 에디터 실행 여부를 검사한다(실행 중·확인 불가 모두 막음)."""
    return None if fake else device.editor_blocked_reason()


def open_device(fake: bool):
    if fake:
        dev = device.FakeDevice()
    else:
        reason = device.editor_blocked_reason()
        if reason:
            sys.exit(reason)
        dev = device.Device()
    dev.connect()
    return dev


def _read(dev):
    """장비를 읽고, 판별한 기종을 활성 기종으로 삼는다(이후 해석·검사가 그 기종 기준이 되도록)."""
    data = dev.read_all()
    if getattr(dev, "profile", None) is not None:
        models.set_active(dev.profile)
    return data


def cmd_read(dev, a):
    data = _read(dev)
    for s in range(1, P.SLOT_COUNT + 1):
        print("%s %s" % (P.slot_label(s), codec.decode_name(data[s])))


def cmd_backup(dev, a):
    fails = []
    folder = backup.make_backup(_read(dev), a.folder, failures=fails)
    print("백업 폴더:", folder)
    lines = ["백업: 완료(device.bin·backup.json 저장·검증 성공)", "백업 폴더: %s" % folder,
             ".mo %d개" % len(list(Path(folder).glob("*.mo")))]
    for s, why in fails:
        lines.append("경고: 백업 .mo 생략 %s — %s (원본 바이트는 device.bin 에 보존)" % (P.slot_label(s), why))
    for ln in lines[3:]:
        print(ln)
    backup.write_result(folder, "\n".join(lines) + "\n")   # C03: 단독 백업도 경고를 결과.txt 에 남긴다


def cmd_export(dev, a):
    out = Path(a.folder)
    out.mkdir(parents=True, exist_ok=True)
    data = _read(dev)
    for s in range(1, P.SLOT_COUNT + 1):
        mofile.write_mo(out, s, codec.decode(data[s]), unique=True)
    print("저장:", out, "(200개)")


def _names(dev):
    try:
        return dev.user_samples()
    except Exception:  # noqa: 사용자 샘플 이름은 경고용일 뿐이라 못 읽어도 계속
        return None


def _plan(dev, folder):
    try:
        return plan.build_plan(_read(dev), folder, user_names=_names(dev))
    except ValueError as ex:
        sys.exit(str(ex))


def cmd_plan(dev, a):
    rows, unassigned = _plan(dev, a.folder)
    for r in rows:
        if r.status != plan.ST_NONE:
            print("%s  %-4s  %-16s -> %-16s  %s" % (r.label, r.status, r.device_name, r.new_name,
                                                    " / ".join(r.errors + r.warnings)))
    for e in unassigned:
        print("슬롯 미지정  %s  %s" % (e.path.name, " / ".join(e.errors)))
    cnt = {}
    for r in rows:
        cnt[r.status] = cnt.get(r.status, 0) + 1
    print("요약:", ", ".join("%s %d" % kv for kv in cnt.items()), "/ 슬롯 미지정 %d" % len(unassigned))


def cmd_write(dev, a):
    if not a.allow_write:
        sys.exit("장비에 쓰려면 --allow-write 를 함께 지정해야 합니다.")
    slots = []
    for t in a.slots:
        s = P.parse_slot(t)
        if s is None:
            sys.exit("슬롯 형식이 올바르지 않습니다: %s" % t)
        slots.append(s)
    rows, _ = _plan(dev, a.folder)
    print("예상 시간: 약 %d초(슬롯마다 안전하게 저장되도록 간격을 둡니다)" % plan.estimate_seconds(len(slots)))
    # A05: 쓰기 직전에 에디터 실행 여부를 다시 검사한다(apply_plan 이 백업보다 먼저 호출)
    res = plan.apply_plan(dev, rows, slots, a.backup_root, guard=lambda: editor_guard(a.fake),
                          progress=lambda i, n, lab, msg: print("  %d/%d %s %s" % (i, n, lab, msg)))
    print(res.text())
    sys.exit(0 if (res.write_ok and res.record_ok) else 1)   # B02: 쓰기·기록 중 하나라도 실패면 1


def cmd_convert(a):
    prof = models.by_key(a.device) if a.device else (tonepack.peek_device(a.json) or models.PRO)
    if prof is None:
        sys.exit("--device 는 GE150 또는 GE150PRO 여야 합니다.")
    res = tonepack.convert(a.json, a.out, profile=prof)  # 실패해도 예외 없이 오류 목록으로 돌아온다(B03)
    for iss in res.errors:
        print("오류:", iss.text())
    for iss in res.warnings:
        print("경고:", iss.text())
    if res.errors:
        sys.exit(1)
    print("변환 완료: .mo %d개 -> %s" % (len(res.files), res.out_dir))


def _refuse_existing(*paths):
    for p in paths:
        if p and Path(p).exists():
            sys.exit("파일이 이미 있어 덮어쓰지 않습니다: %s" % p)


def _backup_data(folder):
    """백업 폴더를 읽기만 한다(장비에 연결하지 않음). 기종은 backup.json 의 device 로 정하고 활성 기종으로 삼는다."""
    try:
        proof = json.loads((Path(folder) / backup.PROOF_NAME).read_text(encoding="utf-8"))
        prof = models.by_key(proof.get("device", models.PRO.key))
    except (OSError, ValueError, AttributeError) as ex:
        sys.exit("백업을 읽을 수 없습니다: %s" % ex)
    if prof is None:
        sys.exit("백업의 기종(device)을 알 수 없습니다: %r" % proof.get("device"))
    models.set_active(prof)
    try:
        return backup.verify_backup(folder), prof
    except backup.BackupError as ex:
        sys.exit(str(ex))


def cmd_export_json(a):
    _refuse_existing(a.out)
    try:
        songs = export.parse_song_list(Path(a.songs).read_text(encoding="utf-8-sig")) if a.songs else None
    except (OSError, UnicodeDecodeError) as ex:
        sys.exit("곡 목록을 읽을 수 없습니다: %s" % ex)
    if a.from_backup:
        data, prof = _backup_data(a.from_backup)
    else:
        dev = open_device(a.fake)
        try:
            data = _read(dev)
            prof = dev.profile or models.active()
        finally:
            dev.close()
    warns = []
    doc = export.device_to_doc(data, prof, a.title or "%s 장비 내보내기" % prof.label, songs, warns)
    export.write_doc(a.out, doc)
    for w in warns:
        print("경고:", w)
    print("톤 JSON 저장: %s (프리셋 %d개, 기종 %s, 경고 %d건)" % (a.out, len(doc["presets"]), prof.label, len(warns)))


def cmd_to_ge150(a):
    _refuse_existing(a.out, a.notes)
    try:
        doc = json.loads(Path(a.json).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as ex:
        sys.exit("톤 JSON 을 읽을 수 없습니다: %s" % ex)
    if not isinstance(doc, dict):
        sys.exit("톤 JSON 이 객체가 아닙니다.")
    stats = Counter()
    new, notes = convert_model.to_ge150(doc, stats)
    export.write_doc(a.out, new)
    _, errors, warnings = tonepack.load_tonepacks([a.out], profile=models.GE150)   # 결과가 GE150 검사를 통과하는지
    head = ["GE150 변환 내역: %s -> %s" % (Path(a.json).name, Path(a.out).name),
            "변환 %d건 / GE150 검사 결과: 오류 %d건, 경고 %d건" % (len(notes), len(errors), len(warnings)), "종류별 건수:"]
    head += ["  " + ln for ln in convert_model.summary(stats)]
    text = "\n".join(head + [""] + notes + [""] + ["검사 오류: " + i.text() for i in errors]) + "\n"
    print(text, end="")
    if a.notes:
        with open(a.notes, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    sys.exit(1 if errors else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="GE150 / GE150 Pro 프리셋 적용기 시험 도구")
    ap.add_argument("--fake", action="store_true", help="가짜 장비(녹화 자료) 사용")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("read")
    for name in ("backup", "export", "plan", "write"):
        p = sub.add_parser(name)
        p.add_argument("folder")
        if name == "write":
            p.add_argument("--slots", nargs="+", required=True)
            p.add_argument("--allow-write", action="store_true")
            p.add_argument("--backup-root", default=None)
    pc = sub.add_parser("convert")
    pc.add_argument("json", nargs="+")
    pc.add_argument("--out", default=None)
    pc.add_argument("--device", default=None, help="GE150 또는 GE150PRO(생략하면 JSON 의 device, 그것도 없으면 GE150PRO)")
    pj = sub.add_parser("export-json")
    pj.add_argument("out")
    pj.add_argument("--from-backup", default=None, help="장비 대신 이 백업 폴더를 읽습니다(장비에 연결하지 않음)")
    pj.add_argument("--songs", default=None, help="곡 목록 글 파일(한 줄에 'NN 아티스트 - 곡명')")
    pj.add_argument("--title", default=None)
    pg = sub.add_parser("to-ge150")
    pg.add_argument("json")
    pg.add_argument("--out", required=True)
    pg.add_argument("--notes", default=None, help="변환 내역을 이 파일에도 저장합니다")
    a = ap.parse_args(argv)
    if a.cmd in ("convert", "to-ge150", "export-json"):  # 장비를 직접 열지 않는다(export-json 은 --from-backup 이 없을 때만 안에서 연다)
        return {"convert": cmd_convert, "to-ge150": cmd_to_ge150, "export-json": cmd_export_json}[a.cmd](a)
    dev = open_device(a.fake)
    try:
        {"read": cmd_read, "backup": cmd_backup, "export": cmd_export, "plan": cmd_plan, "write": cmd_write}[a.cmd](dev, a)
    finally:
        dev.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
