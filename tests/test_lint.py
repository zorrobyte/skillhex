from skillhex.lint import lint_skill


GOOD = "---\nname: notes-cli\ndescription: Count notes.\n---\n# Notes\n\nRun `python3 notes.py list`."


def test_good_skill_has_no_errors():
    errors, warnings = lint_skill(GOOD, "notes-cli")
    assert errors == [] and warnings == []


def test_missing_frontmatter_or_name_is_an_error():
    errors, _ = lint_skill("# just markdown", "notes-cli")
    assert any("frontmatter" in e for e in errors)
    errors, _ = lint_skill("---\ndescription: x.\n---\n# b", "notes-cli")
    assert any("name" in e for e in errors)


def test_wrong_name_is_an_error():
    errors, _ = lint_skill("---\nname: other\ndescription: x.\n---\n# b", "notes-cli")
    assert any("name" in e for e in errors)


def test_truncation_markers_and_empty_body_are_errors():
    errors, _ = lint_skill("---\nname: notes-cli\ndescription: x.\n---\n", "notes-cli")
    assert any("body" in e for e in errors)
    errors, _ = lint_skill("---\nname: notes-cli\ndescription: x.\n---\n# a\n... (rest unchanged)\n", "notes-cli")
    assert any("truncat" in e for e in errors)


def test_long_description_and_incident_log_are_warnings():
    md = "---\nname: notes-cli\ndescription: " + "x" * 80 + "\n---\n# a\nOn 2026-09-21 we hit KeyError: 'weather' and PR #123 fixed it."
    errors, warnings = lint_skill(md, "notes-cli")
    assert errors == []
    assert any("description" in w for w in warnings)
    assert any("incident" in w for w in warnings)
