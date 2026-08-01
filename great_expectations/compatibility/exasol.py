from __future__ import annotations

from typing import Final

from great_expectations.compatibility.not_imported import NotImported

EXASOL_NOT_IMPORTED = NotImported(
    "exasol connection components are not installed, please 'pip install sqlalchemy-exasol pyexasol websocket-client'"  # noqa: E501 # FIXME CoP
)

try:
    import sqlalchemy_exasol
except ImportError:
    sqlalchemy_exasol = EXASOL_NOT_IMPORTED

try:
    import sqlalchemy_exasol.base as exasoldialect
except (ImportError, AttributeError):
    exasoldialect = EXASOL_NOT_IMPORTED

try:
    import pyexasol
except ImportError:
    pyexasol = EXASOL_NOT_IMPORTED

IS_EXASOL_INSTALLED: Final[bool] = sqlalchemy_exasol is not EXASOL_NOT_IMPORTED


class EXASOL_TYPES:
    """Namespace for Exasol dialect types.

    Exasol relies on the standard SQLAlchemy generic types, so there are no
    custom dialect types to surface here today. This namespace exists to mirror
    the other compatibility shims (e.g. ``SNOWFLAKE_TYPES``) and provides a
    home for Exasol-specific types if expectation-level type mapping is added
    in a follow-up.
    """
