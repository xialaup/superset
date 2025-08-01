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
# isort:skip_file
from datetime import date, datetime
import os
import re
from typing import Any, Optional
from unittest.mock import Mock, patch  # noqa: F401

from superset.commands.database.exceptions import DatabaseInvalidError
from tests.integration_tests.fixtures.birth_names_dashboard import (
    load_birth_names_dashboard_with_slices,  # noqa: F401
    load_birth_names_data,  # noqa: F401
)

from flask import current_app, Flask, g  # noqa: F401
import pandas as pd
import pytest
import marshmallow
from sqlalchemy.exc import ArgumentError  # noqa: F401

import tests.integration_tests.test_app  # noqa: F401
from superset import db, security_manager
from superset.constants import NO_TIME_RANGE
from superset.exceptions import CertificateException, SupersetException  # noqa: F401
from superset.models.core import Database, Log
from superset.models.dashboard import Dashboard  # noqa: F401
from superset.models.slice import Slice  # noqa: F401
from superset.utils.core import (
    cast_to_num,
    convert_legacy_filters_into_adhoc,
    create_ssl_cert_file,
    DTTM_ALIAS,
    extract_dataframe_dtypes,
    GenericDataType,
    get_form_data_token,
    as_list,
    recipients_string_to_list,
    merge_extra_filters,
    merge_extra_form_data,
    normalize_dttm_col,
    parse_ssl_cert,
    split,
    DateColumn,
)
from superset.utils import json
from superset.utils.database import get_or_create_db
from superset.utils import schema
from superset.utils.hashing import md5_sha_from_str
from superset.views.utils import build_extra_filters, get_form_data  # noqa: F401
from tests.integration_tests.base_tests import SupersetTestCase
from tests.integration_tests.constants import ADMIN_USERNAME
from tests.integration_tests.fixtures.world_bank_dashboard import (
    load_world_bank_dashboard_with_slices,  # noqa: F401
    load_world_bank_data,  # noqa: F401
)

from .fixtures.certificates import ssl_certificate


class TestUtils(SupersetTestCase):
    def test_convert_legacy_filters_into_adhoc_where(self):
        form_data = {"where": "a = 1"}
        expected = {
            "adhoc_filters": [
                {
                    "clause": "WHERE",
                    "expressionType": "SQL",
                    "filterOptionName": "46fb6d7891e23596e42ae38da94a57e0",
                    "sqlExpression": "a = 1",
                }
            ]
        }
        convert_legacy_filters_into_adhoc(form_data)
        assert form_data == expected

    def test_convert_legacy_filters_into_adhoc_filters(self):
        form_data = {"filters": [{"col": "a", "op": "in", "val": "someval"}]}
        expected = {
            "adhoc_filters": [
                {
                    "clause": "WHERE",
                    "comparator": "someval",
                    "expressionType": "SIMPLE",
                    "filterOptionName": "135c7ee246666b840a3d7a9c3a30cf38",
                    "operator": "in",
                    "subject": "a",
                }
            ]
        }
        convert_legacy_filters_into_adhoc(form_data)
        assert form_data == expected

    def test_convert_legacy_filters_into_adhoc_present_and_empty(self):
        form_data = {"adhoc_filters": [], "where": "a = 1"}
        expected = {
            "adhoc_filters": [
                {
                    "clause": "WHERE",
                    "expressionType": "SQL",
                    "filterOptionName": "46fb6d7891e23596e42ae38da94a57e0",
                    "sqlExpression": "a = 1",
                }
            ]
        }
        convert_legacy_filters_into_adhoc(form_data)
        assert form_data == expected

    def test_convert_legacy_filters_into_adhoc_having(self):
        form_data = {"having": "COUNT(1) = 1"}
        expected = {
            "adhoc_filters": [
                {
                    "clause": "HAVING",
                    "expressionType": "SQL",
                    "filterOptionName": "683f1c26466ab912f75a00842e0f2f7b",
                    "sqlExpression": "COUNT(1) = 1",
                }
            ]
        }
        convert_legacy_filters_into_adhoc(form_data)
        assert form_data == expected

    def test_convert_legacy_filters_into_adhoc_present_and_nonempty(self):
        form_data = {
            "adhoc_filters": [
                {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": "a = 1"}
            ],
            "filters": [{"col": "a", "op": "in", "val": "someval"}],
            "having": "COUNT(1) = 1",
        }
        expected = {
            "adhoc_filters": [
                {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": "a = 1"}
            ]
        }
        convert_legacy_filters_into_adhoc(form_data)
        assert form_data == expected

    def test_split(self):
        assert list(split("a b")) == ["a", "b"]
        assert list(split("a,b", delimiter=",")) == ["a", "b"]
        assert list(split("a,(b,a)", delimiter=",")) == ["a", "(b,a)"]
        assert list(split('a,(b,a),"foo , bar"', delimiter=",")) == [
            "a",
            "(b,a)",
            '"foo , bar"',
        ]
        assert list(split("a,'b,c'", delimiter=",", quote="'")) == ["a", "'b,c'"]
        assert list(split('a "b c"')) == ["a", '"b c"']
        assert list(split('a "b \\" c"')) == ["a", '"b \\" c"']

    def test_get_or_create_db(self):
        get_or_create_db("test_db", "sqlite:///superset.db")
        database = db.session.query(Database).filter_by(database_name="test_db").one()
        assert database is not None
        assert database.sqlalchemy_uri == "sqlite:///superset.db"
        assert (
            security_manager.find_permission_view_menu("database_access", database.perm)
            is not None
        )
        # Test change URI
        get_or_create_db("test_db", "sqlite:///changed.db")
        database = db.session.query(Database).filter_by(database_name="test_db").one()
        assert database.sqlalchemy_uri == "sqlite:///changed.db"
        db.session.delete(database)
        db.session.commit()

    def test_get_or_create_db_invalid_uri(self):
        with self.assertRaises(DatabaseInvalidError):  # noqa: PT027
            get_or_create_db("test_db", "yoursql:superset.db/()")

    def test_get_or_create_db_existing_invalid_uri(self):
        database = get_or_create_db("test_db", "sqlite:///superset.db")
        database.sqlalchemy_uri = "None"
        db.session.commit()
        database = get_or_create_db("test_db", "sqlite:///superset.db")
        assert database.sqlalchemy_uri == "sqlite:///superset.db"

    def test_as_list(self):
        self.assertListEqual(as_list(123), [123])  # noqa: PT009
        self.assertListEqual(as_list([123]), [123])  # noqa: PT009
        self.assertListEqual(as_list("foo"), ["foo"])  # noqa: PT009

    def test_merge_extra_filters_with_no_extras(self):
        form_data = {
            "time_range": "Last 10 days",
        }
        merge_extra_form_data(form_data)
        assert form_data == {"time_range": "Last 10 days", "adhoc_filters": []}

    def test_merge_extra_filters_with_unset_legacy_time_range(self):
        """
        Make sure native filter is applied if filter time range is unset.
        """
        form_data = {
            "time_range": "Last 10 days",
            "extra_filters": [
                {"col": "__time_range", "op": "==", "val": NO_TIME_RANGE},
            ],
            "extra_form_data": {"time_range": "Last year"},
        }
        merge_extra_filters(form_data)
        assert form_data == {
            "time_range": "Last year",
            "applied_time_extras": {},
            "adhoc_filters": [],
        }

    def test_merge_extra_filters_with_extras(self):
        form_data = {
            "time_range": "Last 10 days",
            "extra_form_data": {
                "filters": [{"col": "foo", "op": "IN", "val": ["bar"]}],
                "adhoc_filters": [
                    {
                        "expressionType": "SQL",
                        "clause": "WHERE",
                        "sqlExpression": "1 = 0",
                    }
                ],
                "time_range": "Last 100 years",
                "time_grain_sqla": "PT1M",
                "relative_start": "now",
            },
        }
        merge_extra_form_data(form_data)
        adhoc_filters = form_data["adhoc_filters"]
        assert adhoc_filters[0] == {
            "clause": "WHERE",
            "expressionType": "SQL",
            "isExtra": True,
            "sqlExpression": "1 = 0",
        }
        converted_filter = adhoc_filters[1]
        del converted_filter["filterOptionName"]
        assert converted_filter == {
            "clause": "WHERE",
            "comparator": ["bar"],
            "expressionType": "SIMPLE",
            "isExtra": True,
            "operator": "IN",
            "subject": "foo",
        }
        assert form_data["time_range"] == "Last 100 years"
        assert form_data["time_grain_sqla"] == "PT1M"
        assert form_data["extras"]["relative_start"] == "now"

    def test_ssl_certificate_parse(self):
        parsed_certificate = parse_ssl_cert(ssl_certificate)
        assert parsed_certificate.serial_number == 12355228710836649848

    def test_ssl_certificate_file_creation(self):
        path = create_ssl_cert_file(ssl_certificate)
        expected_filename = md5_sha_from_str(ssl_certificate)
        assert expected_filename in path
        assert os.path.exists(path)

    def test_recipients_string_to_list(self):
        assert recipients_string_to_list("a@a") == ["a@a"]
        assert recipients_string_to_list(" a@a ") == ["a@a"]
        assert recipients_string_to_list("a@a\n") == ["a@a"]
        assert recipients_string_to_list(",a@a;") == ["a@a"]
        assert recipients_string_to_list(",a@a; b@b c@c a-c@c; d@d, f@f") == [
            "a@a",
            "b@b",
            "c@c",
            "a-c@c",
            "d@d",
            "f@f",
        ]

    def test_get_form_data_default(self) -> None:
        form_data, slc = get_form_data()
        assert slc is None

    def test_get_form_data_request_args(self) -> None:
        with current_app.test_request_context(
            query_string={"form_data": json.dumps({"foo": "bar"})}
        ):
            form_data, slc = get_form_data()
            assert form_data == {"foo": "bar"}
            assert slc is None

    def test_get_form_data_request_form(self) -> None:
        with current_app.test_request_context(
            data={"form_data": json.dumps({"foo": "bar"})}
        ):
            form_data, slc = get_form_data()
            assert form_data == {"foo": "bar"}
            assert slc is None

    def test_get_form_data_request_form_with_queries(self) -> None:
        # the CSV export uses for requests, even when sending requests to
        # /api/v1/chart/data
        with current_app.test_request_context(
            data={
                "form_data": json.dumps({"queries": [{"url_params": {"foo": "bar"}}]})
            }
        ):
            form_data, slc = get_form_data()
            assert form_data == {"url_params": {"foo": "bar"}}
            assert slc is None

    def test_get_form_data_request_args_and_form(self) -> None:
        with current_app.test_request_context(
            data={"form_data": json.dumps({"foo": "bar"})},
            query_string={"form_data": json.dumps({"baz": "bar"})},
        ):
            form_data, slc = get_form_data()
            assert form_data == {"baz": "bar", "foo": "bar"}
            assert slc is None

    def test_get_form_data_globals(self) -> None:
        with current_app.test_request_context():
            g.form_data = {"foo": "bar"}
            form_data, slc = get_form_data()
            delattr(g, "form_data")
            assert form_data == {"foo": "bar"}
            assert slc is None

    def test_get_form_data_corrupted_json(self) -> None:
        with current_app.test_request_context(
            data={"form_data": "{x: '2324'}"},
            query_string={"form_data": '{"baz": "bar"'},
        ):
            form_data, slc = get_form_data()
            assert form_data == {}
            assert slc is None

    @pytest.mark.usefixtures("load_world_bank_dashboard_with_slices")
    def test_log_this(self) -> None:
        # TODO: Add additional scenarios.
        self.login(ADMIN_USERNAME)
        slc = self.get_slice("Life Expectancy VS Rural %")
        dashboard_id = 1

        assert slc.viz is not None
        resp = self.get_json_resp(  # noqa: F841
            f"/superset/explore_json/{slc.datasource_type}/{slc.datasource_id}/"
            + f'?form_data={{"slice_id": {slc.id}}}&dashboard_id={dashboard_id}',
            {"form_data": json.dumps(slc.viz.form_data)},
        )

        record = (
            db.session.query(Log)
            .filter_by(action="explore_json", slice_id=slc.id)
            .order_by(Log.dttm.desc())
            .first()
        )

        assert record.dashboard_id == dashboard_id
        assert json.loads(record.json)["dashboard_id"] == str(dashboard_id)
        assert json.loads(record.json)["form_data"]["slice_id"] == slc.id

        assert (
            json.loads(record.json)["form_data"]["viz_type"]
            == slc.viz.form_data["viz_type"]
        )

    def test_schema_validate_json(self):
        valid = '{"a": 5, "b": [1, 5, ["g", "h"]]}'
        assert schema.validate_json(valid) is None
        invalid = '{"a": 5, "b": [1, 5, ["g", "h]]}'
        self.assertRaises(marshmallow.ValidationError, schema.validate_json, invalid)  # noqa: PT027

    def test_schema_one_of_case_insensitive(self):
        validator = schema.OneOfCaseInsensitive(choices=[1, 2, 3, "FoO", "BAR", "baz"])
        assert 1 == validator(1)
        assert 2 == validator(2)
        assert "FoO" == validator("FoO")
        assert "FOO" == validator("FOO")
        assert "bar" == validator("bar")
        assert "BaZ" == validator("BaZ")
        self.assertRaises(marshmallow.ValidationError, validator, "qwerty")  # noqa: PT027
        self.assertRaises(marshmallow.ValidationError, validator, 4)  # noqa: PT027

    def test_cast_to_num(self) -> None:
        assert cast_to_num("5") == 5
        assert cast_to_num("5.2") == 5.2
        assert cast_to_num(10) == 10
        assert cast_to_num(10.1) == 10.1
        assert cast_to_num(None) is None
        assert cast_to_num("this is not a string") is None

    def test_get_form_data_token(self):
        assert get_form_data_token({"token": "token_abcdefg1"}) == "token_abcdefg1"
        generated_token = get_form_data_token({})
        assert re.match(r"^token_[a-z0-9]{8}$", generated_token) is not None

    @pytest.mark.usefixtures("load_birth_names_dashboard_with_slices")
    def test_extract_dataframe_dtypes(self):
        slc = self.get_slice("Girls")
        cols: tuple[tuple[str, GenericDataType, list[Any]], ...] = (
            ("dt", GenericDataType.TEMPORAL, [date(2021, 2, 4), date(2021, 2, 4)]),
            (
                "dttm",
                GenericDataType.TEMPORAL,
                [datetime(2021, 2, 4, 1, 1, 1), datetime(2021, 2, 4, 1, 1, 1)],
            ),
            ("str", GenericDataType.STRING, ["foo", "foo"]),
            ("int", GenericDataType.NUMERIC, [1, 1]),
            ("float", GenericDataType.NUMERIC, [0.5, 0.5]),
            ("mixed-int-float", GenericDataType.NUMERIC, [0.5, 1.0]),
            ("bool", GenericDataType.BOOLEAN, [True, False]),
            ("mixed-str-int", GenericDataType.STRING, ["abc", 1.0]),
            ("obj", GenericDataType.STRING, [{"a": 1}, {"a": 1}]),
            ("dt_null", GenericDataType.TEMPORAL, [None, date(2021, 2, 4)]),
            (
                "dttm_null",
                GenericDataType.TEMPORAL,
                [None, datetime(2021, 2, 4, 1, 1, 1)],
            ),
            ("str_null", GenericDataType.STRING, [None, "foo"]),
            ("int_null", GenericDataType.NUMERIC, [None, 1]),
            ("float_null", GenericDataType.NUMERIC, [None, 0.5]),
            ("bool_null", GenericDataType.BOOLEAN, [None, False]),
            ("obj_null", GenericDataType.STRING, [None, {"a": 1}]),
            # Non-timestamp columns should be identified as temporal if
            # `is_dttm` is set to `True` in the underlying datasource
            ("ds", GenericDataType.TEMPORAL, [None, {"ds": "2017-01-01"}]),
        )

        df = pd.DataFrame(data={col[0]: col[2] for col in cols})
        assert extract_dataframe_dtypes(df, slc.datasource) == [col[1] for col in cols]

    def test_normalize_dttm_col(self):
        def normalize_col(
            df: pd.DataFrame,
            timestamp_format: Optional[str],
            offset: int,
            time_shift: Optional[str],
        ) -> pd.DataFrame:
            df = df.copy()
            normalize_dttm_col(
                df,
                tuple(  # noqa: C409
                    [
                        DateColumn.get_legacy_time_column(
                            timestamp_format=timestamp_format,
                            offset=offset,
                            time_shift=time_shift,
                        )
                    ]
                ),
            )
            return df

        ts = pd.Timestamp(2021, 2, 15, 19, 0, 0, 0)
        df = pd.DataFrame([{"__timestamp": ts, "a": 1}])

        # test regular (non-numeric) format
        assert normalize_col(df, None, 0, None)[DTTM_ALIAS][0] == ts
        assert normalize_col(df, "epoch_ms", 0, None)[DTTM_ALIAS][0] == ts
        assert normalize_col(df, "epoch_s", 0, None)[DTTM_ALIAS][0] == ts

        # test offset
        assert normalize_col(df, None, 1, None)[DTTM_ALIAS][0] == pd.Timestamp(
            2021, 2, 15, 20, 0, 0, 0
        )

        # test offset and timedelta
        assert normalize_col(df, None, 1, "30 minutes")[DTTM_ALIAS][0] == pd.Timestamp(
            2021, 2, 15, 20, 30, 0, 0
        )

        # test numeric epoch_s format
        df = pd.DataFrame([{"__timestamp": ts.timestamp(), "a": 1}])
        assert normalize_col(df, "epoch_s", 0, None)[DTTM_ALIAS][0] == ts

        # test numeric epoch_ms format
        df = pd.DataFrame([{"__timestamp": ts.timestamp() * 1000, "a": 1}])
        assert normalize_col(df, "epoch_ms", 0, None)[DTTM_ALIAS][0] == ts
