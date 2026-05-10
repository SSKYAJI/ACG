from app.routers import items


def test_list_items_returns_title() -> None:
    assert items.list_items() == ["tiny"]
