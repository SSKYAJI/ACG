from ._internal import _shout
from .util import slugify


def greet(name: str) -> str:
    return _shout(f"hello {slugify(name)}")


class Greeter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def hi(self, name: str) -> str:
        return f"{self.prefix} {greet(name)}"
