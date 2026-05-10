from tinypkg import greet, slugify


def test_greet_uppercases() -> None:
    assert greet("World") == "HELLO WORLD"


def test_slugify_basic() -> None:
    assert slugify("Hello World!") == "hello-world"
