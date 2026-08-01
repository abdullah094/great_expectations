from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from great_expectations.compatibility.sqlalchemy import sqlalchemy as sa
from great_expectations.datasource.fluent.config_str import ConfigStr
from great_expectations.datasource.fluent.exasol_datasource import (
    ExasolAuthError,
    ExasolConnectionDetails,
    ExasolDatasource,
    ExasolFingerprintError,
    ExasolNetworkError,
)
from great_expectations.datasource.fluent.interfaces import TestConnectionError
from great_expectations.datasource.fluent.sql_datasource import TableAsset
from great_expectations.execution_engine import SqlAlchemyExecutionEngine

if TYPE_CHECKING:
    from typing import Callable, Union

    from typing_extensions import TypeAlias

    from great_expectations.data_context import AbstractDataContext

ConnectionDetailsDict: TypeAlias = dict[str, Any]


@pytest.fixture
def connection_details_default() -> ConnectionDetailsDict:
    return {
        "host": "exasol.example.com",
        "port": 8563,
        "database": "GX_DEMO",
        "schema": "GX_DEMO",
        "username": "sys",
        "password": "exasol",
    }


@pytest.fixture
def connection_details_with_fingerprint() -> ConnectionDetailsDict:
    return {
        "host": "127.0.0.1",
        "port": 9563,
        "database": "GX_DEMO",
        "schema": "GX_DEMO",
        "username": "sys",
        "password": "exasol",
        "fingerprint": "ABC123DEF456",
    }


@pytest.fixture
def connection_details_special_chars() -> ConnectionDetailsDict:
    return {
        "host": "host",
        "port": 8563,
        "database": "db",
        "schema": "s",
        "username": "user",
        "password": "p@ss:w/rd",
    }


@pytest.mark.unit
class TestExasolConnectionDetails:
    def test_create_with_defaults(self) -> None:
        details = ExasolConnectionDetails(
            host="exasol.example.com",
            username="sys",
            password="exasol",
        )
        assert details.host == "exasol.example.com"
        assert details.port == 8563
        assert details.database is None
        assert details.schema_ is None
        assert details.fingerprint is None

    def test_password_accepts_config_str(self) -> None:
        details = ExasolConnectionDetails(
            host="host",
            username="sys",
            password="${MY_PASSWORD}",
        )
        assert isinstance(details.password, ConfigStr)
        assert str(details.password) == "${MY_PASSWORD}"

    def test_schema_alias(self) -> None:
        details = ExasolConnectionDetails(
            host="host",
            schema="GX_DEMO",
            username="sys",
            password="exasol",
        )
        assert details.schema_ == "GX_DEMO"


@pytest.mark.unit
class TestBuildConnectionString:
    def test_basic_connection_string(
        self, connection_details_default: ConnectionDetailsDict
    ) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        result = ds._build_connection_string()
        assert result == "exa+websocket://sys:exasol@exasol.example.com:8563/GX_DEMO"

    def test_default_port(self) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(host="host", username="u", password="p"),
        )
        result = ds._build_connection_string()
        assert result == "exa+websocket://u:p@host:8563"

    def test_fingerprint_included(
        self, connection_details_with_fingerprint: ConnectionDetailsDict
    ) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_with_fingerprint),
        )
        result = ds._build_connection_string()
        assert "FINGERPRINT=ABC123DEF456" in result
        # URL still parses (single query separator, host/port intact)
        assert result.startswith("exa+websocket://sys:exasol@127.0.0.1:9563/GX_DEMO?")

    def test_fingerprint_omitted(self, connection_details_default: ConnectionDetailsDict) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        result = ds._build_connection_string()
        assert "FINGERPRINT" not in result
        assert "?" not in result

    def test_special_chars_are_encoded(
        self, connection_details_special_chars: ConnectionDetailsDict
    ) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_special_chars),
        )
        result = ds._build_connection_string()
        assert "p%40ss%3Aw%2Frd" in result

    def test_raw_connection_string_passthrough(self) -> None:
        raw = "exa+websocket://sys:exasol@host:8563/SCHEMA"
        ds = ExasolDatasource(name="test_ds", connection_string=raw)
        assert ds._build_connection_string() == raw


@pytest.mark.unit
class TestExasolDatasource:
    def test_type_literal(self, connection_details_default: ConnectionDetailsDict) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        assert ds.type == "exasol"

    def test_schema_property(self, connection_details_default: ConnectionDetailsDict) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        assert ds.schema_ == "GX_DEMO"

    def test_schema_property_none_for_raw_connection_string(self) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string="exa+websocket://sys:exasol@host:8563/SCHEMA",
        )
        assert ds.schema_ is None

    @pytest.mark.usefixtures("create_engine_fake")
    def test_get_engine_calls_create_engine(
        self, connection_details_default: ConnectionDetailsDict
    ) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        engine = ds.get_engine()
        assert engine is not None

    @pytest.mark.usefixtures("create_engine_fake")
    def test_get_engine_caches_engine(
        self, connection_details_default: ConnectionDetailsDict
    ) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        assert ds.get_engine() is ds.get_engine()

    @pytest.mark.usefixtures("create_engine_fake")
    def test_get_execution_engine_type(
        self, connection_details_default: ConnectionDetailsDict
    ) -> None:
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        execution_engine = ds.get_execution_engine()
        assert isinstance(execution_engine, SqlAlchemyExecutionEngine)

    @pytest.mark.usefixtures("create_engine_fake")
    def test_add_table_asset_inherits_schema_from_datasource(
        self,
        connection_details_default: ConnectionDetailsDict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(TableAsset, "test_connection", lambda self: None)
        ds = ExasolDatasource(
            name="test_ds",
            connection_string=ExasolConnectionDetails(**connection_details_default),
        )
        asset = ds.add_table_asset(name="my_asset", table_name="my_table")
        # GX normalizes unquoted schema names to lowercase via
        # _to_lower_if_not_bracketed_by_quotes(). This is Exasol-compatible:
        # SQLAlchemy renders the lowercase identifier unquoted, and Exasol folds
        # unquoted identifiers back to UPPERCASE, resolving to the original schema.
        assert asset._effective_schema_name == ds.schema_.lower()


@pytest.mark.unit
@pytest.mark.usefixtures("mock_test_connection")
class TestAddExasolDatasourceAPI:
    def test_add_exasol_with_connection_details(
        self, empty_data_context: AbstractDataContext
    ) -> None:
        source = empty_data_context.data_sources.add_exasol(
            name="my_exasol",
            connection_string=ExasolConnectionDetails(
                host="exasol.example.com",
                database="GX_DEMO",
                schema="GX_DEMO",
                username="sys",
                password="exasol",
            ),
        )
        assert source.type == "exasol"
        assert isinstance(source.connection_string, ExasolConnectionDetails)
        assert source.connection_string.host == "exasol.example.com"

    def test_add_exasol_with_flat_kwargs(self, empty_data_context: AbstractDataContext) -> None:
        source = empty_data_context.data_sources.add_exasol(
            name="my_exasol_flat",
            host="exasol.example.com",
            database="GX_DEMO",
            schema="GX_DEMO",
            username="sys",
            password="exasol",
        )
        assert source.type == "exasol"
        assert isinstance(source.connection_string, ExasolConnectionDetails)
        assert source.connection_string.schema_ == "GX_DEMO"

    def test_add_exasol_with_raw_connection_string(
        self, empty_data_context: AbstractDataContext
    ) -> None:
        source = empty_data_context.data_sources.add_exasol(
            name="my_exasol_raw",
            connection_string="exa+websocket://sys:exasol@host:8563/GX_DEMO",
        )
        assert source.type == "exasol"
        assert isinstance(source.connection_string, str)

    def test_rejects_connection_string_and_kwargs(
        self, empty_data_context: AbstractDataContext
    ) -> None:
        with pytest.raises(ValueError, match="not both"):
            empty_data_context.data_sources.add_exasol(  # type: ignore[call-overload]
                name="bad",
                connection_string="exa+websocket://sys:exasol@host:8563/GX_DEMO",
                host="other_host",
            )


def with_mock_engine_raising(
    connect_exception: Union[Exception, Callable[[], Exception]],
) -> Callable:
    """Patch get_engine to return an engine whose connect() raises the given exception."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            exc = connect_exception() if callable(connect_exception) else connect_exception
            mock_engine = _make_mock_engine(exc)
            with patch.object(ExasolDatasource, "get_engine", return_value=mock_engine):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def _make_mock_engine(connect_exception: Exception):
    """Create a mock engine whose connect() raises the given exception."""

    class _MockEngine:
        def connect(self):
            raise connect_exception

    return _MockEngine()


@pytest.fixture
def exasol_datasource() -> ExasolDatasource:
    return ExasolDatasource(
        name="test_ds",
        connection_string=ExasolConnectionDetails(
            host="127.0.0.1",
            database="GX_DEMO",
            schema="GX_DEMO",
            username="sys",
            password="exasol",
            fingerprint="ABC123",
        ),
    )


@pytest.mark.unit
class TestExasolDatasourceTestConnectionErrors:
    """Tests for error handling in ExasolDatasource.test_connection."""

    @with_mock_engine_raising(
        sa.exc.OperationalError("Connection refused: unable to connect", None, Exception())
    )
    def test_network_error(self, exasol_datasource: ExasolDatasource) -> None:
        with pytest.raises(ExasolNetworkError):
            exasol_datasource.test_connection()

    @with_mock_engine_raising(
        sa.exc.OperationalError("authentication failed for user 'sys'", None, Exception())
    )
    def test_auth_error(self, exasol_datasource: ExasolDatasource) -> None:
        with pytest.raises(ExasolAuthError):
            exasol_datasource.test_connection()

    @with_mock_engine_raising(
        sa.exc.OperationalError("SSL certificate fingerprint mismatch", None, Exception())
    )
    def test_fingerprint_error(self, exasol_datasource: ExasolDatasource) -> None:
        with pytest.raises(ExasolFingerprintError):
            exasol_datasource.test_connection()

    @with_mock_engine_raising(ValueError("Something unexpected happened"))
    def test_unhandled_error_reraises_test_connection_error(
        self, exasol_datasource: ExasolDatasource
    ) -> None:
        with pytest.raises(TestConnectionError) as exc_info:
            exasol_datasource.test_connection()
        assert isinstance(exc_info.value.cause, ValueError)
        assert "Something unexpected" in str(exc_info.value.cause)


# ---------------------------------------------------------------------------
# Live integration tests (gated behind --exasol). These require a running
# Exasol instance (see assets/docker/exasol/docker-compose.yml).
# ---------------------------------------------------------------------------

EXASOL_SCHEMA = "GX_DEMO"
EXASOL_TABLE = "TAXI_RIDES"


def _live_exasol_datasource(context: AbstractDataContext) -> ExasolDatasource:
    return context.data_sources.add_exasol(
        name="exasol_integration",
        host=os.environ.get("EXASOL_HOST", "127.0.0.1"),
        port=int(os.environ.get("EXASOL_PORT", "9563")),
        schema=EXASOL_SCHEMA,
        username=os.environ.get("EXASOL_USERNAME", "sys"),
        password=os.environ.get("EXASOL_PASSWORD", "exasol"),
        fingerprint=os.environ.get("EXASOL_FINGERPRINT"),
    )


@pytest.mark.exasol
class TestExasolDatasourceLive:
    def test_connection_succeeds(self, empty_data_context: AbstractDataContext) -> None:
        ds = _live_exasol_datasource(empty_data_context)
        ds.test_connection(test_assets=False)

    def test_round_trip_validation_uppercase_identifier(
        self, empty_data_context: AbstractDataContext
    ) -> None:
        """Validate against the seeded TAXI_RIDES table, referencing the
        UPPERCASE folded column name (Exasol folds unquoted identifiers)."""
        import great_expectations as gx

        ds = _live_exasol_datasource(empty_data_context)
        asset = ds.add_table_asset(
            name="taxi_rides", table_name=EXASOL_TABLE, schema_name=EXASOL_SCHEMA
        )
        batch = asset.add_batch_definition_whole_table("FULL_TABLE").get_batch()
        result = batch.validate(
            gx.expectations.ExpectColumnValuesToBeBetween(
                column="PASSENGER_COUNT", min_value=1, max_value=6
            )
        )
        # The seeded data has rows that violate this expectation, mirroring the spike.
        assert result.result.get("unexpected_count") is not None
