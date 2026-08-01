from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, Literal, Optional, Union
from urllib.parse import quote

from great_expectations._docs_decorators import public_api
from great_expectations.compatibility import pydantic
from great_expectations.compatibility.pydantic import Field
from great_expectations.compatibility.sqlalchemy import sqlalchemy as sa
from great_expectations.compatibility.typing_extensions import override
from great_expectations.datasource.fluent.config_str import ConfigStr
from great_expectations.datasource.fluent.interfaces import TestConnectionError
from great_expectations.datasource.fluent.sql_datasource import (
    FluentBaseModel,
    SQLDatasource,
)

if TYPE_CHECKING:
    from great_expectations.compatibility import sqlalchemy
    from great_expectations.execution_engine import SqlAlchemyExecutionEngine

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

DEFAULT_PORT: Final[int] = 8563

_MUTUALLY_EXCLUSIVE_MSG: Final[str] = (
    "Provide either a connection_string or individual keyword arguments, not both."
)


class ConfigStrError(ValueError):
    """Raised when a ConfigStr is provided but no config provider is available."""

    def __init__(self) -> None:
        super().__init__(
            "ConfigStr value provided, but no config provider is set on the datasource."
        )


def _resolve_config_str(value: Union[ConfigStr, str], config_provider: Any) -> str:
    """Resolve a ConfigStr to a plain string; pass through plain strings."""
    if isinstance(value, ConfigStr):
        if config_provider:
            return value.get_config_value(config_provider)
        raise ConfigStrError()
    return str(value)


class ExasolConnectionDetails(FluentBaseModel):
    """Information needed to connect to an Exasol database.

    Alternative to a raw ``exa+websocket://`` connection string.

    Exasol is reached through the pure-Python WebSocket driver chain
    (``sqlalchemy-exasol`` -> ``pyexasol`` -> ``websocket-client``); there is no
    ODBC path. When connecting to an instance with a self-signed certificate
    (e.g. the ``exasol/docker-db`` container), supply the ``fingerprint`` so the
    dialect trusts the certificate without standard validation.
    """

    host: str
    port: int = DEFAULT_PORT
    database: Optional[str] = None
    schema_: Optional[str] = Field(None, alias="schema")
    username: str
    password: Union[ConfigStr, str]
    fingerprint: Optional[str] = None

    class Config:
        # allow using the alias "schema" for the "schema_" field
        allow_population_by_field_name = True

    def build_connection_string(self, config_provider: Optional[Any] = None) -> str:
        """Assemble an ``exa+websocket://`` URL from the connection details.

        The fingerprint is passed as a ``FINGERPRINT`` query parameter, which the
        dialect folds into the ``host/FINGERPRINT:port`` form and uses to trust a
        self-signed certificate. Passing it as a query parameter (rather than
        inline in the host) avoids a URL-parser collision from the ``/`` in the
        fingerprint form.
        """
        password = _resolve_config_str(self.password, config_provider)
        username = quote(self.username, safe="")
        password_encoded = quote(password, safe="")
        url = f"exa+websocket://{username}:{password_encoded}@{self.host}:{self.port}"
        if self.database:
            url = f"{url}/{quote(self.database, safe='')}"
        if self.fingerprint:
            url = f"{url}?FINGERPRINT={quote(self.fingerprint, safe='')}"
        return url


_CONNECTION_DETAIL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",  # alias for schema_
        *ExasolConnectionDetails.__fields__.keys(),
    }
)


@public_api
class ExasolDatasource(SQLDatasource):
    """Adds an Exasol datasource to the data context.

    Args:
        name: The name of this Exasol datasource.
        host: The hostname or IP address of the Exasol instance,
            for example "exasol.example.com" or "127.0.0.1".
        port: The port the Exasol instance listens on (default 8563).
        database: The name of the Exasol database to open, if any. Exasol
            connects to a single cluster, so this is often left unset and
            tables are referenced by schema-qualified name.
        schema: The default schema where the data you want to validate is
            stored. Note that Exasol folds unquoted identifiers to UPPERCASE,
            so reference the uppercase form (e.g. "GX_DEMO").
        username: The username used to access Exasol.
        password: The password used to access Exasol. Accepts a plain string or
            a config-reference string (e.g. "${MY_PASSWORD}").
        fingerprint: The hex certificate fingerprint used to trust a self-signed
            TLS certificate (for example with the ``exasol/docker-db`` container).
            Omit when connecting to an instance with a publicly trusted
            certificate.
        connection_string: A raw ``exa+websocket://`` SQLAlchemy connection
            string. Provide this OR the individual connection-detail keyword
            arguments above, not both.
        assets: An optional dictionary whose keys are TableAsset or QueryAsset
            names and whose values are TableAsset or QueryAsset objects.
    """

    type: Literal["exasol"] = "exasol"  # type: ignore[assignment] # FIXME CoP
    # Deviation from parent class: individual connection-detail args are supported
    # in addition to a raw connection string.
    connection_string: Union[ExasolConnectionDetails, ConfigStr, str]  # type: ignore[assignment] # FIXME CoP

    @property
    @override
    def schema_(self) -> Optional[str]:
        """The default schema, when configured via connection-detail kwargs."""
        if isinstance(self.connection_string, ExasolConnectionDetails):
            return self.connection_string.schema_
        return None

    @pydantic.root_validator(pre=True)
    def _convert_root_connection_detail_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Pack top-level connection-detail kwargs into ``connection_string``.

        Rejects mixing a raw ``connection_string`` with individual kwargs.
        """
        connection_string = values.get("connection_string")
        connection_details: dict[str, Any] = {}
        for field_name in list(values.keys()):
            if field_name in _CONNECTION_DETAIL_FIELDS:
                if connection_string is not None:
                    raise ValueError(_MUTUALLY_EXCLUSIVE_MSG)
                connection_details[field_name] = values.pop(field_name)
        if connection_details:
            values["connection_string"] = connection_details
        return values

    @override
    def _create_engine(self) -> sqlalchemy.Engine:
        url = self._build_connection_string()
        return sa.create_engine(url, **self.kwargs)

    def _build_connection_string(self) -> str:
        """Resolve the configured connection details to an ``exa+websocket://`` URL."""
        if isinstance(self.connection_string, ExasolConnectionDetails):
            return self.connection_string.build_connection_string(
                config_provider=self._config_provider
            )
        return _resolve_config_str(self.connection_string, self._config_provider)

    @override
    def get_execution_engine(self) -> SqlAlchemyExecutionEngine:
        # connection_string may be an ExasolConnectionDetails model rather than a
        # string, which the execution engine cannot consume directly, so we build
        # the engine here and hand it over (mirrors SQLServerDatasource).
        current_execution_engine_kwargs = self.dict(
            exclude=self._get_exec_engine_excludes(),
            config_provider=self._config_provider,
            exclude_unset=False,
        )
        if (
            current_execution_engine_kwargs != self._cached_execution_engine_kwargs
            or not self._execution_engine
        ):
            self._cached_execution_engine_kwargs = current_execution_engine_kwargs
            engine_kwargs = current_execution_engine_kwargs.pop("kwargs", {})
            current_execution_engine_kwargs.pop("connection_string", None)
            engine = self._create_engine()
            self._execution_engine = self._execution_engine_type()(
                engine=engine,
                **current_execution_engine_kwargs,
                **engine_kwargs,
            )
        return self._execution_engine

    @override
    def test_connection(self, test_assets: bool = True) -> None:
        """Test the connection for the ExasolDatasource.

        Maps common driver failures to friendly ``TestConnectionError`` subclasses
        (network, authentication, certificate/fingerprint) rather than leaking raw
        driver exceptions.

        Args:
            test_assets: If assets have been passed to the datasource, whether to
                test them as well.

        Raises:
            TestConnectionError: If the connection test fails.
        """
        try:
            super().test_connection(test_assets=test_assets)
        except TestConnectionError as e:
            cause_str = str(e.cause).lower()
            if any(
                token in cause_str
                for token in ("fingerprint", "certificate", "ssl", "self-signed", "self signed")
            ):
                raise ExasolFingerprintError(e.cause or e) from e
            if any(
                token in cause_str
                for token in ("auth", "credential", "password", "login", "user name", "username")
            ):
                raise ExasolAuthError(e.cause or e) from e
            if any(
                token in cause_str
                for token in (
                    "connect",
                    "refused",
                    "resolve",
                    "network",
                    "timed out",
                    "timeout",
                    "unreachable",
                    "getaddrinfo",
                )
            ):
                raise ExasolNetworkError(e.cause or e) from e
            raise


class ExasolNetworkError(TestConnectionError):
    """Raised when a connection test fails due to a network error."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(
            cause=cause,
            message=" ".join(
                [
                    "Unable to connect to the Exasol server.",
                    "Verify the host and port are correct and the server is accessible.",
                ]
            ),
        )


class ExasolAuthError(TestConnectionError):
    """Raised when a connection test fails due to an authentication error."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(
            cause=cause,
            message="Authentication failed. Verify your username and password.",
        )


class ExasolFingerprintError(TestConnectionError):
    """Raised when a connection test fails due to a certificate/fingerprint error."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(
            cause=cause,
            message=" ".join(
                [
                    "TLS certificate verification failed.",
                    "If the server uses a self-signed certificate, supply the correct",
                    "`fingerprint` so the certificate can be trusted.",
                ]
            ),
        )
