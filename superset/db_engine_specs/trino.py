# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, TYPE_CHECKING

import requests
from flask import copy_current_request_context, ctx, current_app as app, Flask, g
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import NoSuchTableError

from superset import db
from superset.constants import QUERY_CANCEL_KEY, QUERY_EARLY_CANCEL_KEY
from superset.db_engine_specs.base import BaseEngineSpec, convert_inspector_columns
from superset.db_engine_specs.exceptions import (
    SupersetDBAPIConnectionError,
    SupersetDBAPIDatabaseError,
    SupersetDBAPIOperationalError,
    SupersetDBAPIProgrammingError,
)
from superset.db_engine_specs.presto import PrestoBaseEngineSpec
from superset.models.sql_lab import Query
from superset.sql.parse import Table
from superset.superset_typing import ResultSetColumnType
from superset.utils import json
from superset.utils.core import create_ssl_cert_file, get_user_agent, QuerySource

if TYPE_CHECKING:
    from superset.models.core import Database

    with contextlib.suppress(ImportError):  # trino may not be installed
        from trino.dbapi import Cursor

logger = logging.getLogger(__name__)

try:
    # since trino is an optional dependency, we need to handle the ImportError
    from trino.exceptions import HttpError
except ImportError:
    HttpError = Exception


class CustomTrinoAuthErrorMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        logger.info("is this being called?")
        return isinstance(instance, HttpError) and "error 401" in str(instance)


class TrinoAuthError(HttpError, metaclass=CustomTrinoAuthErrorMeta):
    pass


class TrinoEngineSpec(PrestoBaseEngineSpec):
    engine = "trino"
    engine_name = "Trino"
    allows_alias_to_source_column = False

    # OAuth 2.0
    supports_oauth2 = True
    oauth2_exception = TrinoAuthError
    oauth2_token_request_type = "data"  # noqa: S105

    @classmethod
    def get_extra_table_metadata(
        cls,
        database: Database,
        table: Table,
    ) -> dict[str, Any]:
        metadata = {}

        if indexes := database.get_indexes(table):
            col_names, latest_parts = cls.latest_partition(
                database,
                table,
                show_first=True,
                indexes=indexes,
            )

            if not latest_parts:
                latest_parts = tuple([None] * len(col_names))

            metadata["partitions"] = {
                "cols": sorted(  # noqa: C414
                    list(
                        {
                            column_name
                            for index in indexes
                            if index.get("name") == "partition"
                            for column_name in index.get("column_names", [])
                        }
                    )
                ),
                "latest": dict(zip(col_names, latest_parts, strict=False)),
                "partitionQuery": cls._partition_query(
                    table=table,
                    indexes=indexes,
                    database=database,
                ),
            }

        if database.has_view(Table(table.table, table.schema)):
            with database.get_inspector(
                catalog=table.catalog,
                schema=table.schema,
            ) as inspector:
                metadata["view"] = inspector.get_view_definition(
                    table.table,
                    table.schema,
                )

        return metadata

    @classmethod
    def impersonate_user(
        cls,
        database: Database,
        username: str | None,
        user_token: str | None,
        url: URL,
        engine_kwargs: dict[str, Any],
    ) -> tuple[URL, dict[str, Any]]:
        if username is None:
            return url, engine_kwargs

        backend_name = url.get_backend_name()
        connect_args = engine_kwargs.setdefault("connect_args", {})
        if backend_name == "trino":
            connect_args["user"] = username
            if user_token is not None:
                http_session = requests.Session()
                http_session.headers.update({"Authorization": f"Bearer {user_token}"})
                connect_args["http_session"] = http_session

        return url, engine_kwargs

    @classmethod
    def get_allow_cost_estimate(cls, extra: dict[str, Any]) -> bool:
        return True

    @classmethod
    def get_tracking_url(cls, cursor: Cursor) -> str | None:
        try:
            return cursor.info_uri
        except AttributeError:
            with contextlib.suppress(AttributeError):
                conn = cursor.connection
                # pylint: disable=protected-access, line-too-long
                return f"{conn.http_scheme}://{conn.host}:{conn.port}/ui/query.html?{cursor._query.query_id}"
        return None

    @classmethod
    def handle_cursor(cls, cursor: Cursor, query: Query) -> None:
        """
        Handle a trino client cursor.

        WARNING: if you execute a query, it will block until complete and you
        will not be able to handle the cursor until complete. Use
        `execute_with_cursor` instead, to handle this asynchronously.
        """

        # Adds the executed query id to the extra payload so the query can be cancelled
        cancel_query_id = cursor.query_id
        logger.debug("Query %d: queryId %s found in cursor", query.id, cancel_query_id)
        query.set_extra_json_key(key=QUERY_CANCEL_KEY, value=cancel_query_id)

        if tracking_url := cls.get_tracking_url(cursor):
            query.tracking_url = tracking_url

        db.session.commit()  # pylint: disable=consider-using-transaction

        # if query cancelation was requested prior to the handle_cursor call, but
        # the query was still executed, trigger the actual query cancelation now
        if query.extra.get(QUERY_EARLY_CANCEL_KEY):
            cls.cancel_query(
                cursor=cursor,
                query=query,
                cancel_query_id=cancel_query_id,
            )

        super().handle_cursor(cursor=cursor, query=query)

    @classmethod
    def execute_with_cursor(
        cls,
        cursor: Cursor,
        sql: str,
        query: Query,
    ) -> None:
        """
        Trigger execution of a query and handle the resulting cursor.

        Trino's client blocks until the query is complete, so we need to run it
        in another thread and invoke `handle_cursor` to poll for the query ID
        to appear on the cursor in parallel.
        """
        # Fetch the query ID beforehand, since it might fail inside the thread due to
        # how the SQLAlchemy session is handled.
        query_id = query.id
        query_database = query.database

        execute_result: dict[str, Any] = {}
        execute_event = threading.Event()

        @copy_current_request_context
        def _execute(
            results: dict[str, Any],
            event: threading.Event,
            app: Flask,
            g_copy: ctx._AppCtxGlobals,
        ) -> None:
            logger.debug("Query %d: Running query: %s", query_id, sql)

            try:
                # Flask contexts are local to the thread that handles the request.
                # When you spawn a new thread, it does not inherit the contexts
                # from the parent thread,
                # meaning the g object and other context-bound variables are not
                # accessible
                with app.app_context():
                    for key, value in g_copy.__dict__.items():
                        setattr(g, key, value)
                    cls.execute(cursor, sql, query_database)
            except Exception as ex:  # pylint: disable=broad-except
                results["error"] = ex
            finally:
                event.set()

        execute_thread = threading.Thread(
            target=_execute,
            args=(
                execute_result,
                execute_event,
                app._get_current_object(),  # pylint: disable=protected-access
                g._get_current_object(),  # pylint: disable=protected-access
            ),
        )
        execute_thread.start()

        # Wait for the thread to start before continuing
        time.sleep(0.1)
        # Wait for a query ID to be available before handling the cursor, as
        # it's required by that method; it may never become available on error.
        while not cursor.query_id and not execute_event.is_set():
            time.sleep(0.1)

        logger.debug("Query %d: Handling cursor", query_id)
        cls.handle_cursor(cursor, query)

        # Block until the query completes; same behaviour as the client itself
        logger.debug("Query %d: Waiting for query to complete", query_id)
        execute_event.wait()

        # Unfortunately we'll mangle the stack trace due to the thread, but
        # throwing the original exception allows mapping database errors as normal
        if err := execute_result.get("error"):
            raise err

    @classmethod
    def prepare_cancel_query(cls, query: Query) -> None:
        if QUERY_CANCEL_KEY not in query.extra:
            query.set_extra_json_key(QUERY_EARLY_CANCEL_KEY, True)
            db.session.commit()  # pylint: disable=consider-using-transaction

    @classmethod
    def cancel_query(cls, cursor: Cursor, query: Query, cancel_query_id: str) -> bool:
        """
        Cancel query in the underlying database.

        :param cursor: New cursor instance to the db of the query
        :param query: Query instance
        :param cancel_query_id: Trino `queryId`
        :return: True if query cancelled successfully, False otherwise
        """
        try:
            cursor.execute(
                f"CALL system.runtime.kill_query(query_id => '{cancel_query_id}',"
                "message => 'Query cancelled by Superset')"
            )
            cursor.fetchall()  # needed to trigger the call
        except Exception:  # pylint: disable=broad-except
            return False

        return True

    @staticmethod
    def get_extra_params(
        database: Database, source: QuerySource | None = None
    ) -> dict[str, Any]:
        """
        Some databases require adding elements to connection parameters,
        like passing certificates to `extra`. This can be done here.

        :param database: database instance from which to extract extras
        :param source: in which context is the connection needed
        :raises CertificateException: If certificate is not valid/unparseable
        """
        extra: dict[str, Any] = BaseEngineSpec.get_extra_params(database, source)
        engine_params: dict[str, Any] = extra.setdefault("engine_params", {})
        connect_args: dict[str, Any] = engine_params.setdefault("connect_args", {})
        user_agent = get_user_agent(database, source)

        connect_args.setdefault("source", user_agent)

        if database.server_cert:
            connect_args["http_scheme"] = "https"
            connect_args["verify"] = create_ssl_cert_file(database.server_cert)

        return extra

    @staticmethod
    def update_params_from_encrypted_extra(
        database: Database,
        params: dict[str, Any],
    ) -> None:
        if not database.encrypted_extra:
            return
        try:
            encrypted_extra = json.loads(database.encrypted_extra)
            auth_method = encrypted_extra.pop("auth_method", None)
            auth_params = encrypted_extra.pop("auth_params", {})
            if not auth_method:
                return

            connect_args = params.setdefault("connect_args", {})
            connect_args["http_scheme"] = "https"
            # pylint: disable=import-outside-toplevel
            if auth_method == "basic":
                from trino.auth import BasicAuthentication as trino_auth  # noqa
            elif auth_method == "kerberos":
                from trino.auth import KerberosAuthentication as trino_auth  # noqa
            elif auth_method == "certificate":
                from trino.auth import CertificateAuthentication as trino_auth  # noqa
            elif auth_method == "jwt":
                from trino.auth import JWTAuthentication as trino_auth  # noqa
            else:
                allowed_extra_auths = app.config["ALLOWED_EXTRA_AUTHENTICATIONS"].get(
                    "trino", {}
                )
                if auth_method in allowed_extra_auths:
                    trino_auth = allowed_extra_auths.get(auth_method)
                else:
                    raise ValueError(
                        f"For security reason, custom authentication '{auth_method}' "
                        f"must be listed in 'ALLOWED_EXTRA_AUTHENTICATIONS' config"
                    )

            connect_args["auth"] = trino_auth(**auth_params)
        except json.JSONDecodeError as ex:
            logger.error(ex, exc_info=True)
            raise

    @classmethod
    def get_dbapi_exception_mapping(cls) -> dict[type[Exception], type[Exception]]:
        # pylint: disable=import-outside-toplevel
        from requests import exceptions as requests_exceptions
        from trino import exceptions as trino_exceptions

        static_mapping: dict[type[Exception], type[Exception]] = {
            requests_exceptions.ConnectionError: SupersetDBAPIConnectionError,
        }

        class _CustomMapping(dict[type[Exception], type[Exception]]):
            def get(  # type: ignore[override]
                self, item: type[Exception], default: type[Exception] | None = None
            ) -> type[Exception] | None:
                if static := static_mapping.get(item):
                    return static
                if issubclass(item, trino_exceptions.InternalError):
                    return SupersetDBAPIDatabaseError
                if issubclass(item, trino_exceptions.OperationalError):
                    return SupersetDBAPIOperationalError
                if issubclass(item, trino_exceptions.ProgrammingError):
                    return SupersetDBAPIProgrammingError
                return default

        return _CustomMapping()

    @classmethod
    def _expand_columns(cls, col: ResultSetColumnType) -> list[ResultSetColumnType]:
        """
        Expand the given column out to one or more columns by analysing their types,
        descending into ROWS and expanding out their inner fields recursively.

        We can only navigate named fields in ROWs in this way, so we can't expand out
        MAP or ARRAY types, nor fields in ROWs which have no name (in fact the trino
        library doesn't correctly parse unnamed fields in ROWs). We won't be able to
        expand ROWs which are nested underneath any of those types, either.

        Expanded columns are named foo.bar.baz and we provide a query_as property to
        instruct the base engine spec how to correctly query them: instead of quoting
        the whole string they have to be quoted like "foo"."bar"."baz" and we then
        alias them to the full dotted string for ease of reference.
        """
        # pylint: disable=import-outside-toplevel
        from trino.sqlalchemy import datatype

        cols = [col]
        col_type = col.get("type")

        if not isinstance(col_type, datatype.ROW):
            return cols

        for inner_name, inner_type in col_type.attr_types:
            outer_name = col["name"]
            name = ".".join([outer_name, inner_name])
            query_name = ".".join([f'"{piece}"' for piece in name.split(".")])
            column_spec = cls.get_column_spec(str(inner_type))
            is_dttm = column_spec.is_dttm if column_spec else False

            inner_col = ResultSetColumnType(
                name=name,
                column_name=name,
                type=inner_type,
                is_dttm=is_dttm,
                query_as=f'{query_name} AS "{name}"',
            )
            cols.extend(cls._expand_columns(inner_col))

        return cols

    @classmethod
    def get_columns(
        cls,
        inspector: Inspector,
        table: Table,
        options: dict[str, Any] | None = None,
    ) -> list[ResultSetColumnType]:
        """
        If the "expand_rows" feature is enabled on the database via
        "schema_options", expand the schema definition out to show all
        subfields of nested ROWs as their appropriate dotted paths.
        """
        # The Trino dialect raises `NoSuchTableError` on the inspection methods when the
        # table is empty. We can work around this by running a `SHOW COLUMNS FROM` query
        # when that happens, using the method from the Presto base engine spec.
        try:
            # `SELECT * FROM information_schema.columns WHERE ...`
            sqla_columns = inspector.get_columns(table.table, table.schema)
            base_cols = convert_inspector_columns(sqla_columns)
        except NoSuchTableError:
            # `SHOW COLUMNS FROM ...`
            base_cols = super().get_columns(inspector, table, options)

        if not (options or {}).get("expand_rows"):
            return base_cols

        return [col for base_col in base_cols for col in cls._expand_columns(base_col)]

    @classmethod
    def get_indexes(
        cls,
        database: Database,
        inspector: Inspector,
        table: Table,
    ) -> list[dict[str, Any]]:
        """
        Get the indexes associated with the specified schema/table.

        Trino dialect raises NoSuchTableError in get_indexes if table is empty.

        :param database: The database to inspect
        :param inspector: The SQLAlchemy inspector
        :param table: The table instance to inspect
        :returns: The indexes
        """
        try:
            return super().get_indexes(database, inspector, table)
        except NoSuchTableError:
            return []
