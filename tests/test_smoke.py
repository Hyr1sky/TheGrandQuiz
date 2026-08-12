from importlib.metadata import version

from grandquiz import __version__


def test_version() -> None:
    assert __version__ == "0.5.0"
    assert version("grandquiz") == __version__
