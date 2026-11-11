from ge150 import catalog, codec


def _identical(dev_bytes, j):
    d = codec.decode(dev_bytes)
    return d["effectModule"] == j["effectModule"] and d["fileInfo"]["preset_name"] == j["fileInfo"]["preset_name"].rstrip()


def test_decode_matches_mo_exactly_101(dev_data, mo_files):
    assert sum(_identical(dev_data[s], mo_files[s][1]) for s in range(1, 201)) == 101


def test_encode_equals_device_bytes_for_101(dev_data, mo_files):
    n = 0
    for s in range(1, 201):
        j = mo_files[s][1]
        if _identical(dev_data[s], j):
            enc = codec.encode(j)
            assert len(enc) == 244
            # 모델이 쓰지 않는 P칸은 비교 제외: 장비 바이트도 같은 방식으로 정리해서 비교
            assert enc == codec.normalize(dev_data[s]), s
            n += 1
    assert n == 101


def test_decode_encode_decode_all_200(dev_data):
    for s in range(1, 201):
        d1 = codec.decode(dev_data[s])
        assert codec.decode(codec.encode(d1)) == d1, s


def test_exp_layout_matches_mo_where_content_identical(dev_data, mo_files):
    """잠정 EXP 배치가 일치하는 101개의 .mo Exp 와 같은지 — 배치가 틀렸으면 여기서 드러난다."""
    for s in range(1, 201):
        j = mo_files[s][1]
        if _identical(dev_data[s], j):
            assert codec.decode(dev_data[s])["Exp"] == j["Exp"], s


def test_unused_param_cells_written_as_zero(dev_data):
    d = codec.decode(dev_data[1])
    enc = codec.encode(d)
    for i in range(9):
        o = 16 + i * 24 + 4
        used = len(catalog.params(i, d["effectModule"][str(i)]["TYPE"]))
        assert enc[o + 2 * used:o + 20] == b"\x00" * (20 - 2 * used)


def test_name_padding(dev_data):
    d = codec.decode(dev_data[1])
    d["fileInfo"]["preset_name"] = "ABC   "
    assert codec.encode(d)[:16] == b"ABC" + b"\x00" * 13
