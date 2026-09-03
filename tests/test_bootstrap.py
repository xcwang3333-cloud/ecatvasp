from ecatvasp import __version__
from ecatvasp.schema.version import SCHEMA_VERSION


def test_package_version_is_development_version() -> None:
    assert __version__ == "0.4.0.dev0"


def test_schema_version_is_two() -> None:
    assert SCHEMA_VERSION == 2
