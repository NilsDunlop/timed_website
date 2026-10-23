from timed_dataserver import __version__


def test_dataserver_package_is_importable() -> None:
    assert __version__ == "0.4.0.dev0"
