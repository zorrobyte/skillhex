from skillhex.evidence import EvidenceMatrix


def test_record_and_read_cells(tmp_path):
    m = EvidenceMatrix(tmp_path / "e.db")
    m.record("v0", "t1", 1)
    m.record("v0", "t2", 0)
    assert m.get("v0", "t1") == 1
    assert m.get("v0", "t2") == 0
    assert m.get("v0", "t9") is None


def test_row_and_column_views(tmp_path):
    m = EvidenceMatrix(tmp_path / "e.db")
    m.record("v0", "t1", 1); m.record("v0", "t2", 0)
    m.record("v1", "t1", 1); m.record("v1", "t2", 1)
    assert m.row("v1") == {"t1": 1, "t2": 1}
    assert m.column("t2") == {"v0": 0, "v1": 1}
    assert m.nodes() == ["v0", "v1"]
    assert m.tests() == ["t1", "t2"]


def test_drop_tests_removes_columns(tmp_path):
    m = EvidenceMatrix(tmp_path / "e.db")
    m.record("v0", "t1", 1); m.record("v0", "t2", 0)
    m.drop_tests(["t2"])
    assert m.tests() == ["t1"]
    assert m.get("v0", "t2") is None


def test_missing_cells_lists_what_needs_replay(tmp_path):
    m = EvidenceMatrix(tmp_path / "e.db")
    m.record("v0", "t1", 1)
    missing = m.missing(nodes=["v0", "v1"], tests=["t1", "t2"])
    assert missing == [("v0", "t2"), ("v1", "t1"), ("v1", "t2")]


def test_render_table_is_readable(tmp_path):
    m = EvidenceMatrix(tmp_path / "e.db")
    m.record("v0", "t1", 1); m.record("v0", "t2", 0); m.record("v1", "t1", 1)
    text = m.render(node_labels={"v0": "v0 (root)"})
    assert "v0 (root)" in text and "t1" in text
    assert "✓" in text and "✗" in text
