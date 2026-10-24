import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.errors import DatabaseConfigurationError

SECRET = "fixture-password-sensitive"
URL = f"postgresql+asyncpg://reader:{SECRET}@localhost/personal_agent_test_unit?ssl=disable"


def test_disabled_does_not_access_secret_or_pool_values():
    class OnlyFlag(dict):
        def get(self, key, default=None):
            assert key == "H2_DATABASE_ENABLED"
            return "false"

        def __contains__(self, key):
            raise AssertionError("Disabled settings must not inspect other values")

    settings = DatabaseSettings.from_environment(OnlyFlag())
    assert not settings.enabled and settings.url is None


@pytest.mark.parametrize("flag", ["true", "1", "TRUE", " true "])
def test_enabled_flag(flag):
    settings = DatabaseSettings.from_environment(
        {"H2_DATABASE_ENABLED": flag, "H2_DATABASE_URL": URL}
    )
    assert settings.enabled


@pytest.mark.parametrize(
    "environment",
    [
        {"H2_DATABASE_ENABLED": "yes"},
        {"H2_DATABASE_ENABLED": "true"},
        {"H2_DATABASE_ENABLED": "true", "H2_DATABASE_URL": ""},
        {
            "H2_DATABASE_ENABLED": "true",
            "H2_DATABASE_URL": URL,
            "H2_DATABASE_POOL_SIZE": "0",
        },
        {
            "H2_DATABASE_ENABLED": "true",
            "H2_DATABASE_URL": URL,
            "H2_DATABASE_MAX_OVERFLOW": "-1",
        },
        {
            "H2_DATABASE_ENABLED": "true",
            "H2_DATABASE_URL": URL,
            "H2_DATABASE_HEALTH_TIMEOUT": "nan",
        },
        {
            "H2_DATABASE_ENABLED": "true",
            "H2_DATABASE_URL": URL,
            "H2_DATABASE_CONNECT_TIMEOUT": "61",
        },
    ],
)
def test_invalid_environment_has_safe_error(environment):
    with pytest.raises(DatabaseConfigurationError) as caught:
        DatabaseSettings.from_environment(environment)
    assert SECRET not in str(caught.value)
    assert "postgresql" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "invalid-connection-string-sensitive",
        "sqlite+aiosqlite:///private-file",
        "postgresql://reader:password@localhost/db",
        "postgresql+asyncpg://reader@localhost/db",
        "postgresql+asyncpg://reader:password@localhost/",
        "postgresql+asyncpg://reader:password@:5432/db",
        "postgresql+asyncpg://reader:password@localhost:0/db",
        "postgresql+asyncpg://reader:password@localhost:65536/db",
        "postgresql+asyncpg://reader:password@localhost:bad/db",
        URL + "&timeout=90",
        URL + "&ssl=require",
        URL.replace("ssl=disable", "ssl=prefer"),
    ],
)
def test_url_rejected_without_input_leak(url):
    settings = DatabaseSettings(enabled=True, url=url)
    with pytest.raises(DatabaseConfigurationError) as caught:
        settings.connection_options()
    assert url not in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("password", ["a@:/%汉字", "fixture-password"])
def test_url_round_trip_and_options(password):
    from sqlalchemy.engine import URL as SqlURL

    encoded = SqlURL.create(
        "postgresql+asyncpg",
        username="reader",
        password=password,
        host="localhost",
        database="personal_agent_test_unit",
    ).render_as_string(hide_password=False)
    settings = DatabaseSettings(enabled=True, url=encoded)
    parsed, options = settings.connection_options()
    assert parsed.password == password
    assert parsed.port == 5432 and not parsed.query
    assert options == {"ssl": "require", "timeout": 5, "command_timeout": 5}
    assert password not in repr(settings)
    assert password not in settings.model_dump_json()
