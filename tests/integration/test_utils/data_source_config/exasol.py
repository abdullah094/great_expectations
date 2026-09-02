from datetime import datetime
from typing import Mapping, Optional

import pandas as pd
import pytest

from great_expectations.compatibility.sqlalchemy import sqltypes
from great_expectations.compatibility.typing_extensions import override
from great_expectations.data_context import AbstractDataContext
from great_expectations.datasource.fluent.sql_datasource import TableAsset
from tests.integration.sql_session_manager import SessionSQLEngineManager
from tests.integration.test_utils.data_source_config.backend_spec import (
    BackendProvisioning,
    BackendTier,
    CiLaneRef,
    SqlBackendSpec,
)
from tests.integration.test_utils.data_source_config.base import BatchTestSetup
from tests.integration.test_utils.data_source_config.registry import register_sql_backend
from tests.integration.test_utils.data_source_config.sql import SQLBatchTestSetup
from tests.integration.test_utils.data_source_config.sql_config import SqlDatasourceTestConfig


@register_sql_backend
class ExasolDatasourceTestConfig(SqlDatasourceTestConfig):
    BACKEND_SPEC = SqlBackendSpec(
        label="exasol",
        marker="exasol",
        provisioning=BackendProvisioning.LOCAL_CONTAINER,
        ci_lane=CiLaneRef(workflow_job="marker-tests", marker_token="exasol"),
        # Unlike Oracle (where a schema is a user), SingleStore, and ClickHouse, Exasol has
        # first-class schemas: `CREATE SCHEMA` and `DROP SCHEMA` are plain supported DDL, so the
        # shared setup's schema isolation works here as it does for PostgreSQL. Teardown drops
        # every table before the schema, which is what keeps the bare `DROP SCHEMA` (no CASCADE)
        # valid. Exasol folds unquoted identifiers to upper case, and the harness's generated
        # schema and table names are all lower case with legal characters, so SQLAlchemy renders
        # them unquoted and both the DDL and the later queries fold to the same upper-case name.
        uses_schema=True,
        # No transaction_mode override: Exasol has real transactions and the driver commits
        # explicitly, so the shared default (explicit commit) already matches its behavior.
        column_type_overrides={
            # Exasol's VARCHAR requires a length -- it is declared with a `max length` create
            # parameter, and a bare one is rejected at parse time with
            # `syntax error, unexpected ')', expecting '('`. MySQL, SingleStore, and Oracle
            # declare the same override for the same reason.
            str: sqltypes.VARCHAR(255),
            # Exasol has no DATETIME type at all; it is absent from the server's own type list,
            # and naming it fails with `syntax error, unexpected IDENTIFIER_PART_`. TIMESTAMP is
            # the type this dialect actually has.
            datetime: sqltypes.TIMESTAMP,
            pd.Timestamp: sqltypes.TIMESTAMP,
            # The shared default maps `float` to an unqualified DECIMAL, which this dialect
            # resolves to a zero-scale decimal: the DDL succeeds but every fractional value
            # rounds to the nearest integer (10.5 comes back 11, -10.5 comes back -11), the same
            # failure mode Oracle declares. Oracle's remedy -- a DECIMAL carrying explicit
            # precision and scale -- is *not* usable here, for two independent reasons. Its
            # precision of 38 exceeds this dialect's maximum of 36 and is rejected outright with
            # `illegal precision value: 38`; and, more fundamentally, this driver returns every
            # DECIMAL with a non-zero scale as a Python `str` rather than a number, at any
            # precision (verified at DECIMAL(36,10), (18,10) and (15,2) alike), which would make
            # every fractional value arrive as a string. DOUBLE PRECISION is this dialect's
            # native binary float: it round-trips fractional values intact and the driver
            # returns it as a real Python `float`.
            float: sqltypes.DOUBLE_PRECISION,
            # `int` is mapped to the same binary float, and this one is a deliberate trade
            # rather than a dialect requirement -- INTEGER itself works fine for storage. The
            # problem is on the way back out: this driver returns an exact-numeric (DECIMAL)
            # result as a Python `str`, and `SUM()` over any exact-numeric column widens to a
            # DECIMAL wide enough to trigger that. So a summed INTEGER column arrives as `'90'`,
            # and core's `_validate_metric_value_between` compares it against a float and raises
            # `TypeError: '>=' not supported between instances of 'str' and 'float'`, failing
            # every aggregate case in the curated suite.
            #
            # Nothing else reaches it. Every exact-numeric type this dialect has behaves the
            # same way under SUM (INTEGER, SMALLINT, BIGINT, DECIMAL(9,0) and DECIMAL(18,0) all
            # verified returning `str`); only TINYINT, capped at 999 and so unusable for
            # arbitrary test data, and the binary floats come back numeric. pyexasol ships an
            # opt-in `fetch_mapper` that would convert these properly, but the dialect exposes
            # only ENCRYPTION, SSLCertificate, AUTOCOMMIT and FINGERPRINT as URL parameters, so
            # a connection string cannot request it -- and even engine-level `connect_args`
            # would not help, because the metrics run on the engine GX builds internally from
            # this connection string, not on the harness's own engine.
            #
            # The cost is that integer columns are stored as 64-bit binary floats, exact only
            # below 2^53. That is accepted here because this declaration's only consumer is
            # type inference for harness-created test tables, whose values are small. The
            # honest fix is upstream -- either the driver/dialect coercing exact numerics, or
            # core tolerating a numeric string -- and this override should be removed once
            # either lands.
            int: sqltypes.DOUBLE_PRECISION,
        },
        dev_requirements_file="reqs/requirements-dev-exasol.txt",
        task_runner_marker="exasol",
        container_service="exasol",
        tiers=frozenset({BackendTier.CURATED_SQL}),
        tier_case_exclusions={
            # Core's dialect-regex helper (`get_dialect_regex_expression`) has no branch for
            # Exasol -- it dispatches on PostgreSQL, Databricks, Redshift, MySQL, Snowflake,
            # BigQuery, Trino, ClickHouse, Dremio, Teradata, and SQLite, and falls through to
            # returning `None` for any unmatched dialect. Exasol does have regex matching, but
            # not in a shape any existing branch emits: `REGEXP_LIKE` here is an infix predicate
            # (`<expr> REGEXP_LIKE <pattern>`), not the scalar `regexp_like(column, pattern)`
            # call the Trino, ClickHouse, and Databricks branches build -- calling it as a
            # function fails with `syntax error, unexpected REGEXP_LIKE_`. So no regex-based
            # case can execute against this dialect.
            "regex_match": (
                "Exasol has no branch in core's dialect-regex helper "
                "(get_dialect_regex_expression), which falls through to returning None for any "
                "unmatched dialect; Exasol's REGEXP_LIKE is an infix predicate rather than the "
                "scalar regexp_like(column, pattern) call the existing branches emit, so no "
                "regex-based case can execute against this dialect."
            ),
        },
    )

    @override
    def create_batch_setup(
        self,
        request: pytest.FixtureRequest,
        data: pd.DataFrame,
        extra_data: Mapping[str, pd.DataFrame],
        context: AbstractDataContext,
        engine_manager: Optional[SessionSQLEngineManager] = None,
    ) -> BatchTestSetup:
        return ExasolBatchTestSetup(
            data=data,
            config=self,
            extra_data=extra_data,
            table_name=self.table_name,
            context=context,
            engine_manager=engine_manager,
        )


class ExasolBatchTestSetup(SQLBatchTestSetup[ExasolDatasourceTestConfig]):
    # `exa+websocket` names the pure-Python WebSocket driver chain (sqlalchemy-exasol ->
    # pyexasol -> websocket-client), which is what this lane's requirements file installs; there
    # is no ODBC path. Port 8563 is Exasol's default and the port the compose file publishes.
    # `sys`/`exasol` are the image's built-in credentials, so no CI secret is needed.
    _BASE_CONNECTION_STRING = "exa+websocket://sys:exasol@127.0.0.1:8563"

    # Exasol requires TLS, and the container generates a self-signed certificate at start. Its
    # fingerprint is therefore not knowable when this constant is written, which rules out the
    # `FINGERPRINT` route a fixed connection string would otherwise use; disabling verification
    # is what a throwaway local container can express instead.
    _SSL_QUERY = "SSLCertificate=SSL_VERIFY_NONE"

    @override
    def build_connection_string(self, schema: str | None = None) -> str:
        # This dialect takes the schema to open as the URL's path segment, so a schema-targeting
        # string is the base string plus that path -- the shape this repo's earlier Exasol
        # connection helper also used.
        path = f"/{schema}" if schema else ""
        return f"{self._BASE_CONNECTION_STRING}{path}?{self._SSL_QUERY}"

    @override
    def make_asset(self) -> TableAsset:
        # No Exasol-specific fluent datasource exists, so this reaches its datasource through the
        # dialect-agnostic SQL datasource instead.
        return self.context.data_sources.add_sql(
            name=self._random_resource_name(),
            connection_string=self.build_connection_string(schema=self.schema),
        ).add_table_asset(
            name=self._random_resource_name(),
            table_name=self.table_name,
        )
