from timed_dataserver import __version__


def test_dataserver_package_is_importable() -> None:
    assert __version__ == "0.5.0.dev2"
