from ge150 import catalog


def test_names_and_counts():
    assert catalog.model_name(2, 0) == "65 US DX"
    assert [catalog.model_count(i) for i in range(9)] == [12, 28, 75, 46, 3, 4, 25, 9, 8]


def test_para_inheritance():
    # DS/OD 02번(808)은 para 가 비어 있고 paraPos=1 -> 1번(TUBE DR)의 목록
    assert [p["name"] for p in catalog.params(1, 1)] == ["VOLUME", "TONE", "GAIN"]


def test_ranges_and_text():
    eq = catalog.params(5, 0)[0]
    assert catalog.raw_range(eq) == (0, 32)
    assert catalog.check_value(5, 0, "100Hz", 33)
    assert catalog.check_value(5, 0, "100Hz", 16) is None
    assert catalog.check_value(7, 0, "TIME", 39) and catalog.check_value(7, 0, "TIME", 40) is None
    assert catalog.value_text(3, 0, "MIC", 0) == "SM57"
    assert catalog.value_text(5, 0, "100Hz", 16) == "0"
    assert catalog.value_text(7, 0, "TIME", 310).endswith("ms")


def test_user_sample_ranges():
    assert [t for t in range(75) if catalog.is_user_sample(2, t)] == list(range(55, 75))
    assert [t for t in range(46) if catalog.is_user_sample(3, t)] == list(range(26, 46))
