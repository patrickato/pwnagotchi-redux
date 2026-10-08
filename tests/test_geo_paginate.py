from redux.geo.paginate import paginate


def test_page():
    items = list(range(10))
    p = paginate(items, offset=3, limit=4)
    assert list(p.items) == [3, 4, 5, 6]
    assert p.total == 10 and p.has_more
