import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", (123,)),
        ("123, 456", (123, 456)),
        ("[123,456]", (123, 456)),
        (123, (123,)),
    ],
)
def test_admin_ids_accept_common_env_formats(raw, expected):
    assert Settings(admin_ids=raw).admin_ids == expected
