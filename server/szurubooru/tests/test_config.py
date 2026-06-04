import pytest

from szurubooru import config, errors


@pytest.mark.parametrize(
    "value, expected",
    [
        (100000000, 100000000),
        (1.0e8, 100000000),
        ("1E+8", 100000000),
        ("100_000_000", 100000000),
        (" 25000000 ", 25000000),
    ],
)
def test_coerce_max_dl_filesize_valid(value, expected):
    assert config._coerce_max_dl_filesize(value) == expected


@pytest.mark.parametrize("value", [True, "abc", None])
def test_coerce_max_dl_filesize_invalid(value):
    with pytest.raises(errors.ConfigError):
        config._coerce_max_dl_filesize(value)
