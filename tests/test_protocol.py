from ge150 import protocol as P


def test_crc_frames_all_match(frames):
    n = 0
    for h in frames["out"] + frames["in"]:
        b = bytes.fromhex(h)
        ln = int.from_bytes(b[2:4], "little")
        assert b[:2] == b"\xaa\x55" and len(b) == 6 + ln
        assert P.crc16(b[2:4 + ln]).to_bytes(2, "big") == b[4 + ln:], h[:24]
        n += 1
    assert n == len(frames["out"]) + len(frames["in"]) > 200


def test_built_frames_equal_recorded(frames):
    out = [bytes.fromhex(h) for h in frames["out"]]
    assert P.build_frame(bytes.fromhex("3101")) in out
    assert P.build_frame(bytes.fromhex("a001")) in out
    assert P.build_frame(bytes.fromhex("9658")) in out
    assert P.build_frame(P.CMD_PRE_WRITE) in out


def test_reports_join_and_split_equal_out_frames(frames):
    stream = b"".join(P.report_payload(bytes.fromhex(r)) for r in frames["reports_out"])
    parser = P.FrameParser()
    parser.feed(stream)
    got = parser.frames()
    assert all(ok for _, ok in got)
    assert [P.build_frame(b) for b, _ in got] == [bytes.fromhex(h) for h in frames["out"]]


def test_c3_write_reports_equal_recorded(frames):
    c3 = [bytes.fromhex(h) for h in frames["out"] if h.startswith("aa55f600c3")][-1]
    body = c3[4:-2]
    assert body[:2] == b"\xc3\xc8"
    ours = P.frame_to_reports(P.build_frame(P.write_body(200, body[2:])))
    # 녹화된 C3 보고서는 연속 4개(프레임 252B / 63). 5개가 아니라 4개로 비교한다(프레임 252바이트 / 63).
    rec = [bytes.fromhex(r) for r in frames["reports_out"]]
    i = next(k for k, r in enumerate(rec) if r[1:6].hex() == "aa55f600c3")
    recorded = rec[i:i + len(ours)]
    assert len(ours) == 4 and len(recorded) == 4
    for o, r in zip(ours, recorded):
        assert len(o) == 65 and o[0] == 0
        n = r[0]
        assert o[1] == n and o[1:2 + n] == r[:1 + n]  # 유효 길이 이후 바이트는 비교 제외


def test_parser_handles_split_and_garbage():
    f = P.build_frame(b"\x43\x05")
    p = P.FrameParser()
    p.feed(b"\x00\x01" + f[:3])
    assert p.frames() == []
    p.feed(f[3:] + P.build_frame(b"\x20\x01")[:4])
    assert p.frames() == [(b"\x43\x05", True)]
    bad = bytearray(f)
    bad[-1] ^= 1
    p.feed(bytes(bad))
    assert [ok for _, ok in p.frames()] == [False]


def test_slot_labels():
    assert P.slot_label(1) == "01A" and P.slot_label(49) == "13A" and P.slot_label(200) == "50D"
    assert P.parse_slot("13a") == 49 and P.parse_slot("50D") == 200
    assert P.parse_slot("51A") is None and P.parse_slot("00A") is None and P.parse_slot("13E") is None
