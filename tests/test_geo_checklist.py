from redux.geo.checklist import checklist_items, checklist_text


def test_checklist():
    items = checklist_items()
    assert len(items) >= 5
    text = checklist_text()
    assert "provenance" in text.lower()
    assert "Geo integration" in text
