from timed_connector import __version__


def test_connector_package_is_importable() -> None:
    assert __version__ == "0.1.0"
