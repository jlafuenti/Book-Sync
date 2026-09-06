"""
`<NAME>_FILE` indirection for every secret setting (issue #180).

Compose `environment:` is the wrong place for a secret: `docker inspect` prints
it, so does `/proc/1/environ`, and both are readable by anyone in the `docker`
group or with code execution in the container. The fix is the Docker secrets
convention — the value lives in a file, the environment carries only the path
(`JWT_SECRET_KEY_FILE=/run/secrets/jwt_secret_key`).

The rules pinned here:

* `<NAME>_FILE` wins over a plain `<NAME>` that is also set. Precedence, not a
  merge: whichever is more specific is the one the operator meant.
* A `<NAME>_FILE` that names a file which does not exist, cannot be read, or is
  empty is a **startup error naming the variable**. Never a silent fallback to
  the plain variable — a "working" server on the shipped default secret is the
  failure this whole mechanism exists to prevent.
* Exactly one trailing newline's worth of `\\r\\n` is stripped, because every
  way of writing a secret to a file adds one.
* `DATABASE_URL` gets the same treatment plus one extra path: with secrets, the
  password can no longer be interpolated into the URL by compose, so
  `POSTGRES_PASSWORD_FILE` (or a plain `POSTGRES_PASSWORD`) assembles the URL
  in exactly the shape the compose template used to build by hand.
"""

import pytest

import config
from config import (
    DEFAULT_DB_CREDENTIALS,
    SECRET_FILE_ENV_VARS,
    Settings,
    check_db_credentials,
    check_jwt_secret,
    read_secret_file,
)

# Every env name that participates, including the interpolation-only ones the
# compose file used to carry, so a leaked value from the developer's own shell
# can never make one of these tests pass or fail by accident.
_ENV_NAMES = tuple(SECRET_FILE_ENV_VARS) + tuple(
    f"{name}_FILE" for name in SECRET_FILE_ENV_VARS
) + ("POSTGRES_USER", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB")


@pytest.fixture
def clean_env(monkeypatch):
    """A process environment with none of the secret variables set.

    conftest.py sets DATABASE_URL to the SQLite test database before anything
    is imported, so without this every DATABASE_URL assertion below would be
    reading that instead of what the test set up.
    """
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _settings() -> Settings:
    """Settings built from the environment only — never a stray `.env`."""
    return Settings(_env_file=None)


def _secret_file(tmp_path, name: str, contents: str) -> str:
    path = tmp_path / name
    path.write_text(contents, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_file_variant_wins_over_the_plain_env_var(clean_env, tmp_path):
    clean_env.setenv("JWT_SECRET_KEY", "value-from-the-environment")
    clean_env.setenv(
        "JWT_SECRET_KEY_FILE", _secret_file(tmp_path, "jwt", "value-from-the-file")
    )
    assert _settings().jwt_secret_key == "value-from-the-file"


def test_plain_env_var_still_works_with_no_file_variant(clean_env):
    """The less-safe path stays supported — the template documents it as such."""
    clean_env.setenv("JWT_SECRET_KEY", "value-from-the-environment")
    assert _settings().jwt_secret_key == "value-from-the-environment"


def test_neither_set_leaves_the_code_default(clean_env):
    assert _settings().jwt_secret_key == "dev-secret-change-me"


def test_an_empty_file_variable_is_ignored_like_an_unset_one(clean_env):
    """Compose writes `JWT_SECRET_KEY_FILE=` as the empty string when the
    operator comments the secret out; that must mean "unset", not "read ''"."""
    clean_env.setenv("JWT_SECRET_KEY", "value-from-the-environment")
    clean_env.setenv("JWT_SECRET_KEY_FILE", "")
    assert _settings().jwt_secret_key == "value-from-the-environment"


@pytest.mark.parametrize("name", SECRET_FILE_ENV_VARS)
def test_every_declared_secret_has_a_working_file_variant(clean_env, tmp_path, name):
    """One test per secret, so adding a name to the tuple without wiring it up
    fails here rather than in production."""
    aliases = {
        field.alias: field_name
        for field_name, field in Settings.model_fields.items()
        if field.alias
    }
    assert name in aliases, (
        f"{name} is listed in SECRET_FILE_ENV_VARS but is not the alias of any "
        "field on config.Settings — nothing would consume the file."
    )
    value = f"secret-value-for-{name.lower()}"
    clean_env.setenv(f"{name}_FILE", _secret_file(tmp_path, name.lower(), value))
    assert getattr(_settings(), aliases[name]) == value


# Aliases that trip the "looks like a secret" word match below but hold a
# number, not a value worth hiding. One line of reason each; anything else in
# the list is a real secret that needs a `<NAME>_FILE`.
_NOT_ACTUALLY_SECRETS = {
    "PASSWORD_CHANGE_FAILURE_LIMIT": "a count of failed current-password checks",
    "PASSWORD_CHANGE_FAILURE_WINDOW_SECONDS": "the window for that count",
}


def test_every_secret_looking_setting_is_declared(clean_env):
    """A future `..._TOKEN` / `..._PASSWORD` / `..._KEY` setting that forgets to
    join SECRET_FILE_ENV_VARS is a secret with no file path — fail here."""
    secret_words = ("SECRET", "PASSWORD", "TOKEN", "KEY")
    looks_secret = {
        field.alias
        for field in Settings.model_fields.values()
        if field.alias
        and (
            any(word in field.alias for word in secret_words)
            or field.alias == "DATABASE_URL"
        )
    }
    # JWT_ALGORITHM-style companions have no alias, so they never reach here.
    missing = sorted(
        looks_secret - set(SECRET_FILE_ENV_VARS) - set(_NOT_ACTUALLY_SECRETS)
    )
    assert not missing, (
        f"{missing} look like secrets but have no `<NAME>_FILE` support. Add "
        "them to config.SECRET_FILE_ENV_VARS, or — if the name only *sounds* "
        "like a secret — to _NOT_ACTUALLY_SECRETS with a reason."
    )
    # The exemption list itself must stay honest: an entry for a name that is
    # no longer a setting is a hole nobody would notice.
    stale = sorted(set(_NOT_ACTUALLY_SECRETS) - looks_secret)
    assert not stale, f"_NOT_ACTUALLY_SECRETS lists {stale}, which are no longer settings"


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "\n", "\r\n"])
def test_the_trailing_newline_is_stripped(clean_env, tmp_path, suffix):
    clean_env.setenv(
        "JWT_SECRET_KEY_FILE", _secret_file(tmp_path, "jwt", "a-real-secret" + suffix)
    )
    assert _settings().jwt_secret_key == "a-real-secret"


def test_interior_and_leading_whitespace_is_left_alone(clean_env, tmp_path):
    """Only the trailing newline is noise; anything else could be the secret."""
    clean_env.setenv(
        "JWT_SECRET_KEY_FILE", _secret_file(tmp_path, "jwt", " lead and trail \n")
    )
    assert _settings().jwt_secret_key == " lead and trail "


def test_a_missing_file_is_a_startup_error_naming_the_variable(clean_env, tmp_path):
    clean_env.setenv("JWT_SECRET_KEY", "value-from-the-environment")
    clean_env.setenv("JWT_SECRET_KEY_FILE", str(tmp_path / "not-there"))
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY_FILE"):
        _settings()


def test_an_unreadable_file_is_a_startup_error_naming_the_variable(
    clean_env, tmp_path
):
    """A directory stands in for "cannot be read": `open()` refuses it on every
    platform, unlike a chmod that Windows ignores."""
    unreadable = tmp_path / "a-directory"
    unreadable.mkdir()
    clean_env.setenv("CREDENTIAL_ENC_KEYS_FILE", str(unreadable))
    with pytest.raises(RuntimeError, match="CREDENTIAL_ENC_KEYS_FILE"):
        _settings()


def test_an_empty_file_is_a_startup_error_naming_the_variable(clean_env, tmp_path):
    """`touch`ing the file and forgetting to fill it is the likeliest mistake,
    and an empty JWT secret passes the default-secret guard — so it is fatal."""
    clean_env.setenv("JWT_SECRET_KEY_FILE", _secret_file(tmp_path, "jwt", "\n"))
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY_FILE"):
        _settings()


def test_the_error_never_prints_the_secret_or_a_fallback_suggestion(
    clean_env, tmp_path
):
    """The message has to name the variable and the path; it must not quietly
    tell the operator that unsetting the file variable will make it work."""
    missing = tmp_path / "not-there"
    with pytest.raises(RuntimeError) as excinfo:
        read_secret_file("JWT_SECRET_KEY_FILE", str(missing))
    message = str(excinfo.value)
    assert "JWT_SECRET_KEY_FILE" in message
    assert "not-there" in message


# ---------------------------------------------------------------------------
# DATABASE_URL assembly (issue #180)
#
# The compose template built the URL by interpolating the password:
#   DATABASE_URL=postgresql+asyncpg://booksync:${POSTGRES_PASSWORD}@db:5432/booksync
# A secret cannot be interpolated, so the server assembles the same URL itself.
# ---------------------------------------------------------------------------


def test_database_url_is_assembled_from_the_postgres_password_file(
    clean_env, tmp_path
):
    clean_env.setenv(
        "POSTGRES_PASSWORD_FILE", _secret_file(tmp_path, "pg", "a-generated-password\n")
    )
    assert _settings().database_url == (
        "postgresql+asyncpg://booksync:a-generated-password@db:5432/booksync"
    )


def test_the_assembled_url_matches_the_shape_the_template_used_to_build(clean_env):
    """Same driver, role, host, port and database name as the interpolated line
    in docker-compose.example.yml — an install that switches to secrets must not
    start pointing somewhere else."""
    clean_env.setenv("POSTGRES_PASSWORD", "a-generated-password")
    assert _settings().database_url == (
        "postgresql+asyncpg://booksync:a-generated-password@db:5432/booksync"
    )


def test_the_assembled_url_follows_the_other_postgres_variables(clean_env):
    clean_env.setenv("POSTGRES_PASSWORD", "a-generated-password")
    clean_env.setenv("POSTGRES_USER", "tandem")
    clean_env.setenv("POSTGRES_HOST", "database")
    clean_env.setenv("POSTGRES_PORT", "6543")
    clean_env.setenv("POSTGRES_DB", "tandem_db")
    assert _settings().database_url == (
        "postgresql+asyncpg://tandem:a-generated-password@database:6543/tandem_db"
    )


def test_the_assembled_password_is_url_quoted(clean_env, tmp_path):
    """`token_urlsafe` output never needs this, but an operator-chosen password
    with an `@` or a `/` in it would silently point the server at another host
    or another database if it were pasted in raw."""
    clean_env.setenv(
        "POSTGRES_PASSWORD_FILE", _secret_file(tmp_path, "pg", "p@ss/word:1#2")
    )
    assert _settings().database_url == (
        "postgresql+asyncpg://booksync:p%40ss%2Fword%3A1%232@db:5432/booksync"
    )


def test_an_explicit_database_url_wins_over_the_postgres_password(clean_env, tmp_path):
    """A deployment that already sets DATABASE_URL — an external Postgres, a
    non-default sslmode — must not have it rebuilt out from under it."""
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@elsewhere:5432/other")
    clean_env.setenv("POSTGRES_PASSWORD_FILE", _secret_file(tmp_path, "pg", "ignored"))
    assert _settings().database_url == "postgresql+asyncpg://u:p@elsewhere:5432/other"


def test_a_database_url_file_wins_over_the_postgres_password(clean_env, tmp_path):
    clean_env.setenv(
        "DATABASE_URL_FILE",
        _secret_file(tmp_path, "url", "postgresql+asyncpg://u:p@elsewhere:5432/other"),
    )
    clean_env.setenv("POSTGRES_PASSWORD_FILE", _secret_file(tmp_path, "pg", "ignored"))
    assert _settings().database_url == "postgresql+asyncpg://u:p@elsewhere:5432/other"


def test_no_postgres_password_leaves_the_code_default_url(clean_env):
    assert _settings().database_url == Settings.model_fields["database_url"].default


def test_the_postgres_password_is_not_a_second_source_of_truth(clean_env, tmp_path):
    """It only ever *assembles* the URL. Nothing else in the app may read it —
    otherwise an install using a plain DATABASE_URL has one of the two blank."""
    clean_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@elsewhere:5432/other")
    settings = _settings()
    assert settings.postgres_password == ""
    assert settings.database_url == "postgresql+asyncpg://u:p@elsewhere:5432/other"


# ---------------------------------------------------------------------------
# The startup guards keep working through the file path (issue #180, item 3)
# ---------------------------------------------------------------------------


def test_check_jwt_secret_rejects_a_default_read_from_a_file(clean_env, tmp_path):
    clean_env.setenv("APP_ENV", "prod")
    clean_env.setenv(
        "JWT_SECRET_KEY_FILE", _secret_file(tmp_path, "jwt", "dev-secret-change-me\n")
    )
    settings = _settings()
    assert settings.jwt_secret_key == "dev-secret-change-me"
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        check_jwt_secret(settings)


def test_check_db_credentials_rejects_a_default_password_read_from_a_file(
    clean_env, tmp_path
):
    """The guard matches on `booksync:booksync` inside the URL, so it has to
    still fire when the password reached the URL through assembly."""
    clean_env.setenv("APP_ENV", "prod")
    clean_env.setenv("POSTGRES_PASSWORD_FILE", _secret_file(tmp_path, "pg", "booksync"))
    settings = _settings()
    assert DEFAULT_DB_CREDENTIALS in settings.database_url
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        check_db_credentials(settings)


def test_a_real_secret_from_a_file_passes_both_guards(clean_env, tmp_path):
    clean_env.setenv("APP_ENV", "prod")
    clean_env.setenv(
        "JWT_SECRET_KEY_FILE",
        _secret_file(tmp_path, "jwt", "a-real-randomly-generated-secret"),
    )
    clean_env.setenv(
        "POSTGRES_PASSWORD_FILE",
        _secret_file(tmp_path, "pg", "a-real-generated-password"),
    )
    settings = _settings()
    check_jwt_secret(settings)
    check_db_credentials(settings)


def test_the_module_level_settings_object_went_through_the_same_path():
    """`config.settings` is what the app actually uses; a mechanism wired only
    into a factory the app never calls would pass every test above."""
    assert isinstance(config.settings, Settings)
    sources = Settings.settings_customise_sources(
        Settings,
        init_settings=None,
        env_settings=None,
        dotenv_settings=None,
        file_secret_settings=None,
    )
    assert any(
        isinstance(source, config.SecretFileSettingsSource) for source in sources
    )


def test_the_file_source_outranks_the_environment_source():
    """Order is the precedence rule: earlier sources win in pydantic-settings,
    so the file source has to sit ahead of `env_settings`."""
    sources = Settings.settings_customise_sources(
        Settings,
        init_settings="init",
        env_settings="env",
        dotenv_settings="dotenv",
        file_secret_settings="docker",
    )
    positions = [
        i
        for i, source in enumerate(sources)
        if isinstance(source, config.SecretFileSettingsSource)
    ]
    assert positions, "no SecretFileSettingsSource in the source chain"
    assert positions[0] < sources.index("env")


def test_read_secret_file_is_used_by_the_source(clean_env, tmp_path, monkeypatch):
    """Guard against the source growing its own private `open()` — the error
    handling above lives in one place on purpose."""
    calls = []
    real = config.read_secret_file

    def _spy(var_name, path):
        calls.append(var_name)
        return real(var_name, path)

    monkeypatch.setattr(config, "read_secret_file", _spy)
    clean_env.setenv("JWT_SECRET_KEY_FILE", _secret_file(tmp_path, "jwt", "value"))
    assert _settings().jwt_secret_key == "value"
    assert calls == ["JWT_SECRET_KEY_FILE"]
