import copy
import os
from datetime import datetime
from unittest import TestCase

import pandas as pd
import pytest
from fugue.collections.partition import PartitionSpec
from fugue.dataframe import ArrayDataFrame
from fugue.dataframe.pandas_dataframe import PandasDataFrame
from fugue.dataframe.utils import _df_eq as df_eq
from fugue.execution.execution_engine import ExecutionEngine
from pytest import raises
from triad.exceptions import InvalidOperationError
from fugue.dataframe.dataframes import DataFrames
import pickle


class ExecutionEngineTests(object):
    """ExecutionEngine level general test suite.
    Any new :class:`~fugue.execution.execution_engine.ExecutionEngine`
    should pass this test suite.
    """

    class Tests(TestCase):
        @classmethod
        def setUpClass(cls):
            cls._engine = cls.make_engine(cls)

        @property
        def engine(self) -> ExecutionEngine:
            return self._engine  # type: ignore

        @classmethod
        def tearDownClass(cls):
            cls._engine.stop()

        def make_engine(self) -> ExecutionEngine:  # pragma: no cover
            raise NotImplementedError

        def test_init(self):
            print(self.engine)
            assert self.engine.log is not None
            assert self.engine.fs is not None
            assert copy.copy(self.engine) is self.engine
            assert copy.deepcopy(self.engine) is self.engine

        def test_to_df_general(self):
            e = self.engine
            o = ArrayDataFrame(
                [[1.1, 2.2], [3.3, 4.4]],
                "a:double,b:double",
                dict(a=1),
            )
            # all engines should accept these types of inputs
            # should take fugue.DataFrame
            df_eq(o, e.to_df(o), throw=True)
            # should take array, shema and metadata
            df_eq(
                o,
                e.to_df([[1.1, 2.2], [3.3, 4.4]], "a:double,b:double", dict(a=1)),
                throw=True,
            )
            # should take pandas dataframe
            pdf = pd.DataFrame([[1.1, 2.2], [3.3, 4.4]], columns=["a", "b"])
            df_eq(o, e.to_df(pdf, metadata=dict(a=1)), throw=True)

            # should convert string to datetime in to_df
            df_eq(
                e.to_df([["2020-01-01"]], "a:datetime"),
                [[datetime(2020, 1, 1)]],
                "a:datetime",
                throw=True,
            )

        def test_map(self):
            def noop(cursor, data):
                return data

            def on_init(partition_no, data):
                # TODO: this test is not sufficient
                assert partition_no >= 0
                data.peek_array()

            e = self.engine
            o = ArrayDataFrame(
                [[1, 2], [None, 2], [None, 1], [3, 4], [None, 4]],
                "a:double,b:int",
                dict(a=1),
            )
            a = e.to_df(o)
            # no partition
            c = e.map(a, noop, a.schema, PartitionSpec(), dict(a=1))
            df_eq(c, o, throw=True)
            # with key partition
            c = e.map(
                a, noop, a.schema, PartitionSpec(by=["a"], presort="b"), dict(a=1)
            )
            df_eq(c, o, throw=True)
            # select top
            c = e.map(a, select_top, a.schema, PartitionSpec(by=["a"], presort="b"))
            df_eq(c, [[None, 1], [1, 2], [3, 4]], "a:double,b:int", throw=True)
            # select top with another order
            c = e.map(
                a,
                select_top,
                a.schema,
                PartitionSpec(partition_by=["a"], presort="b DESC"),
                metadata=dict(a=1),
            )
            df_eq(
                c,
                [[None, 4], [1, 2], [3, 4]],
                "a:double,b:int",
                metadata=dict(a=1),
                throw=True,
            )
            # add num_partitions, on_init should not matter
            c = e.map(
                a,
                select_top,
                a.schema,
                PartitionSpec(partition_by=["a"], presort="b DESC", num_partitions=3),
                on_init=on_init,
            )
            df_eq(c, [[None, 4], [1, 2], [3, 4]], "a:double,b:int", throw=True)

        def test_map_with_special_values(self):
            def with_nat(cursor, data):
                df = data.as_pandas()
                df["nat"] = pd.NaT
                schema = data.schema + "nat:datetime"
                return PandasDataFrame(df, schema)

            e = self.engine
            # test with multiple key with null values
            o = ArrayDataFrame(
                [[1, None, 1], [1, None, 0], [None, None, 1]],
                "a:double,b:double,c:int",
                dict(a=1),
            )
            c = e.map(
                o, select_top, o.schema, PartitionSpec(by=["a", "b"], presort="c")
            )
            df_eq(
                c,
                [[1, None, 0], [None, None, 1]],
                "a:double,b:double,c:int",
                throw=True,
            )
            # test datetime with nat
            dt = datetime.now()
            o = ArrayDataFrame(
                [
                    [dt, 2, 1],
                    [None, 2, None],
                    [None, 1, None],
                    [dt, 5, 1],
                    [None, 4, None],
                ],
                "a:datetime,b:int,c:double",
                dict(a=1),
            )
            c = e.map(
                o, select_top, o.schema, PartitionSpec(by=["a", "c"], presort="b DESC")
            )
            df_eq(
                c,
                [[None, 4, None], [dt, 5, 1]],
                "a:datetime,b:int,c:double",
                throw=True,
            )
            d = e.map(
                c, with_nat, "a:datetime,b:int,c:double,nat:datetime", PartitionSpec()
            )
            df_eq(
                d,
                [[None, 4, None, None], [dt, 5, 1, None]],
                "a:datetime,b:int,c:double,nat:datetime",
                throw=True,
            )
            # test list
            o = ArrayDataFrame([[dt, [1, 2]]], "a:datetime,b:[int]")
            c = e.map(o, select_top, o.schema, PartitionSpec(by=["a"]))
            df_eq(c, o, check_order=True, throw=True)

        def test_map_with_dict_col(self):
            e = self.engine
            dt = datetime.now()
            # test dict
            o = ArrayDataFrame([[dt, dict(a=1)]], "a:datetime,b:{a:int}")
            c = e.map(o, select_top, o.schema, PartitionSpec(by=["a"]))
            df_eq(c, o, no_pandas=True, check_order=True, throw=True)

        def test_map_with_binary(self):
            e = self.engine
            o = ArrayDataFrame(
                [[pickle.dumps(BinaryObject("a"))], [pickle.dumps(BinaryObject("b"))]],
                "a:bytes",
            )
            c = e.map(o, binary_map, o.schema, PartitionSpec())
            expected = ArrayDataFrame(
                [
                    [pickle.dumps(BinaryObject("ax"))],
                    [pickle.dumps(BinaryObject("bx"))],
                ],
                "a:bytes",
            )
            df_eq(expected, c, no_pandas=True, check_order=True, throw=True)

        def test__join_cross(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4]], "a:int,b:int")
            b = e.to_df([[6], [7]], "c:int")
            c = e.join(a, b, how="Cross", metadata=dict(a=1))
            df_eq(
                c,
                [[1, 2, 6], [1, 2, 7], [3, 4, 6], [3, 4, 7]],
                "a:int,b:int,c:int",
                dict(a=1),
            )

            b = e.to_df([], "c:int")
            c = e.join(a, b, how="Cross", metadata=dict(a=1))
            df_eq(c, [], "a:int,b:int,c:int", metadata=dict(a=1), throw=True)

            a = e.to_df([], "a:int,b:int")
            b = e.to_df([], "c:int")
            c = e.join(a, b, how="Cross")
            df_eq(c, [], "a:int,b:int,c:int", throw=True)

        def test__join_inner(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4]], "a:int,b:int")
            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            c = e.join(a, b, how="INNER", on=["a"], metadata=dict(a=1))
            df_eq(c, [[1, 2, 6]], "a:int,b:int,c:int", metadata=dict(a=1), throw=True)
            c = e.join(b, a, how="INNER", on=["a"])
            df_eq(c, [[6, 1, 2]], "c:int,a:int,b:int", throw=True)

            a = e.to_df([], "a:int,b:int")
            b = e.to_df([], "c:int,a:int")
            c = e.join(a, b, how="INNER", on=["a"])
            df_eq(c, [], "a:int,b:int,c:int", throw=True)

        def test__join_outer(self):
            e = self.engine

            a = e.to_df([], "a:int,b:int")
            b = e.to_df([], "c:str,a:int")
            c = e.join(a, b, how="left_outer", on=["a"], metadata=dict(a=1))
            df_eq(c, [], "a:int,b:int,c:str", metadata=dict(a=1), throw=True)

            a = e.to_df([], "a:int,b:str")
            b = e.to_df([], "c:int,a:int")
            c = e.join(a, b, how="right_outer", on=["a"])
            df_eq(c, [], "a:int,b:str,c:int", throw=True)

            a = e.to_df([], "a:int,b:str")
            b = e.to_df([], "c:str,a:int")
            c = e.join(a, b, how="full_outer", on=["a"])
            df_eq(c, [], "a:int,b:str,c:str", throw=True)

            a = e.to_df([[1, "2"], [3, "4"]], "a:int,b:str")
            b = e.to_df([["6", 1], ["2", 7]], "c:str,a:int")
            c = e.join(a, b, how="left_OUTER", on=["a"])
            df_eq(c, [[1, "2", "6"], [3, "4", None]], "a:int,b:str,c:str", throw=True)
            c = e.join(b, a, how="left_outer", on=["a"])
            df_eq(c, [["6", 1, "2"], ["2", 7, None]], "c:str,a:int,b:str", throw=True)

            a = e.to_df([[1, "2"], [3, "4"]], "a:int,b:str")
            b = e.to_df([[6, 1], [2, 7]], "c:double,a:int")
            c = e.join(a, b, how="left_OUTER", on=["a"])
            df_eq(
                c, [[1, "2", 6.0], [3, "4", None]], "a:int,b:str,c:double", throw=True
            )
            c = e.join(b, a, how="left_outer", on=["a"])
            # assert c.as_pandas().values.tolist()[1][2] is None
            df_eq(
                c, [[6.0, 1, "2"], [2.0, 7, None]], "c:double,a:int,b:str", throw=True
            )

            a = e.to_df([[1, "2"], [3, "4"]], "a:int,b:str")
            b = e.to_df([["6", 1], ["2", 7]], "c:str,a:int")
            c = e.join(a, b, how="right_outer", on=["a"])
            # assert c.as_pandas().values.tolist()[1][1] is None
            df_eq(c, [[1, "2", "6"], [7, None, "2"]], "a:int,b:str,c:str", throw=True)

            c = e.join(a, b, how="full_outer", on=["a"])
            df_eq(
                c,
                [[1, "2", "6"], [3, "4", None], [7, None, "2"]],
                "a:int,b:str,c:str",
                throw=True,
            )

        def test__join_outer_pandas_incompatible(self):
            e = self.engine

            a = e.to_df([[1, "2"], [3, "4"]], "a:int,b:str")
            b = e.to_df([["6", 1], ["2", 7]], "c:int,a:int")
            c = e.join(a, b, how="left_OUTER", on=["a"], metadata=dict(a=1))
            df_eq(
                c,
                [[1, "2", 6], [3, "4", None]],
                "a:int,b:str,c:int",
                metadata=dict(a=1),
                throw=True,
            )
            c = e.join(b, a, how="left_outer", on=["a"])
            df_eq(c, [[6, 1, "2"], [2, 7, None]], "c:int,a:int,b:str", throw=True)

            a = e.to_df([[1, "2"], [3, "4"]], "a:int,b:str")
            b = e.to_df([[True, 1], [False, 7]], "c:bool,a:int")
            c = e.join(a, b, how="left_OUTER", on=["a"])
            df_eq(c, [[1, "2", True], [3, "4", None]], "a:int,b:str,c:bool", throw=True)
            c = e.join(b, a, how="left_outer", on=["a"])
            df_eq(
                c, [[True, 1, "2"], [False, 7, None]], "c:bool,a:int,b:str", throw=True
            )

        def test__join_semi(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4]], "a:int,b:int")
            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            c = e.join(a, b, how="semi", on=["a"], metadata=dict(a=1))
            df_eq(c, [[1, 2]], "a:int,b:int", metadata=dict(a=1), throw=True)
            c = e.join(b, a, how="semi", on=["a"])
            df_eq(c, [[6, 1]], "c:int,a:int", throw=True)

            b = e.to_df([], "c:int,a:int")
            c = e.join(a, b, how="semi", on=["a"])
            df_eq(c, [], "a:int,b:int", throw=True)

            a = e.to_df([], "a:int,b:int")
            b = e.to_df([], "c:int,a:int")
            c = e.join(a, b, how="semi", on=["a"])
            df_eq(c, [], "a:int,b:int", throw=True)

        def test__join_anti(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4]], "a:int,b:int")
            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            c = e.join(a, b, how="anti", metadata=dict(a=1), on=["a"])
            df_eq(c, [[3, 4]], "a:int,b:int", metadata=dict(a=1), throw=True)
            c = e.join(b, a, how="anti", on=["a"])
            df_eq(c, [[2, 7]], "c:int,a:int", throw=True)

            b = e.to_df([], "c:int,a:int")
            c = e.join(a, b, how="anti", on=["a"])
            df_eq(c, [[1, 2], [3, 4]], "a:int,b:int", throw=True)

            a = e.to_df([], "a:int,b:int")
            b = e.to_df([], "c:int,a:int")
            c = e.join(a, b, how="anti", on=["a"])
            df_eq(c, [], "a:int,b:int", throw=True)

        def test__join_with_null_keys(self):
            # SQL will not match null values
            e = self.engine
            a = e.to_df([[1, 2, 3], [4, None, 6]], "a:double,b:double,c:int")
            b = e.to_df([[1, 2, 33], [4, None, 63]], "a:double,b:double,d:int")
            c = e.join(a, b, how="INNER")
            df_eq(c, [[1, 2, 3, 33]], "a:double,b:double,c:int,d:int", throw=True)

        def test_union(self):
            e = self.engine
            a = e.to_df([[1, 2, 3], [4, None, 6]], "a:double,b:double,c:int")
            b = e.to_df([[1, 2, 33], [4, None, 6]], "a:double,b:double,c:int")
            c = e.union(a, b, metadata=dict(a=1))
            df_eq(
                c,
                [[1, 2, 3], [4, None, 6], [1, 2, 33]],
                "a:double,b:double,c:int",
                metadata=dict(a=1),
                throw=True,
            )
            c = e.union(a, b, distinct=False)
            df_eq(
                c,
                [[1, 2, 3], [4, None, 6], [1, 2, 33], [4, None, 6]],
                "a:double,b:double,c:int",
                throw=True,
            )

        def test_subtract(self):
            e = self.engine
            a = e.to_df([[1, 2, 3], [1, 2, 3], [4, None, 6]], "a:double,b:double,c:int")
            b = e.to_df([[1, 2, 33], [4, None, 6]], "a:double,b:double,c:int")
            c = e.subtract(a, b, metadata=dict(a=1))
            df_eq(
                c,
                [[1, 2, 3]],
                "a:double,b:double,c:int",
                metadata=dict(a=1),
                throw=True,
            )
            # TODO: EXCEPT ALL is not implemented (QPD issue)
            # c = e.subtract(a, b, distinct=False)
            # df_eq(
            #     c,
            #     [[1, 2, 3], [1, 2, 3]],
            #     "a:double,b:double,c:int",
            #     throw=True,
            # )

        def test_intersect(self):
            e = self.engine
            a = e.to_df(
                [[1, 2, 3], [4, None, 6], [4, None, 6]], "a:double,b:double,c:int"
            )
            b = e.to_df(
                [[1, 2, 33], [4, None, 6], [4, None, 6], [4, None, 6]],
                "a:double,b:double,c:int",
            )
            c = e.intersect(a, b, metadata=dict(a=1))
            df_eq(
                c,
                [[4, None, 6]],
                "a:double,b:double,c:int",
                metadata=dict(a=1),
                throw=True,
            )
            # TODO: INTERSECT ALL is not implemented (QPD issue)
            # c = e.intersect(a, b, distinct=False)
            # df_eq(
            #     c,
            #     [[4, None, 6], [4, None, 6]],
            #     "a:double,b:double,c:int",
            #     throw=True,
            # )

        def test_distinct(self):
            e = self.engine
            a = e.to_df(
                [[4, None, 6], [1, 2, 3], [4, None, 6]], "a:double,b:double,c:int"
            )
            c = e.distinct(a, metadata=dict(a=1))
            df_eq(
                c,
                [[4, None, 6], [1, 2, 3]],
                "a:double,b:double,c:int",
                metadata=dict(a=1),
                throw=True,
            )

        def test_dropna(self):
            e = self.engine
            a = e.to_df(
                [[4, None, 6], [1, 2, 3], [4, None, None]], "a:double,b:double,c:double"
            )
            c = e.dropna(a, metadata=(dict(a=1)))  # default
            d = e.dropna(a, how="all")
            f = e.dropna(a, how="any", thresh=2)
            g = e.dropna(a, how="any", subset=["a", "c"])
            h = e.dropna(a, how="any", thresh=1, subset=["a", "c"])
            df_eq(
                c,
                [[1, 2, 3]],
                "a:double,b:double,c:double",
                metadata=dict(a=1),
                throw=True,
            )
            df_eq(
                d,
                [[4, None, 6], [1, 2, 3], [4, None, None]],
                "a:double,b:double,c:double",
                throw=True,
            )
            df_eq(
                f, [[4, None, 6], [1, 2, 3]], "a:double,b:double,c:double", throw=True
            )
            df_eq(
                g, [[4, None, 6], [1, 2, 3]], "a:double,b:double,c:double", throw=True
            )
            df_eq(
                h,
                [[4, None, 6], [1, 2, 3], [4, None, None]],
                "a:double,b:double,c:double",
                throw=True,
            )

        def test__serialize_by_partition(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4], [1, 5]], "a:int,b:int")
            s = e._serialize_by_partition(
                a, PartitionSpec(by=["a"], presort="b"), df_name="_0"
            )
            assert s.count() == 2
            s = e.persist(e._serialize_by_partition(a, PartitionSpec(), df_name="_0"))
            assert s.count() == 1
            s = e.persist(
                e._serialize_by_partition(a, PartitionSpec(by=["x"]), df_name="_0")
            )
            assert s.count() == 1

        def test_zip(self):
            ps = PartitionSpec(by=["a"], presort="b DESC,c DESC")
            e = self.engine
            a = e.to_df([[1, 2], [3, 4], [1, 5]], "a:int,b:int")
            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            sa = e._serialize_by_partition(a, ps, df_name="_0")
            sb = e._serialize_by_partition(b, ps, df_name="_1")
            # test zip with serialized dfs
            z1 = e.persist(e.zip(sa, sb, how="inner", partition_spec=ps))
            assert 1 == z1.count()
            assert not z1.metadata.get("serialized_has_name", False)
            z2 = e.persist(e.zip(sa, sb, how="left_outer", partition_spec=ps))
            assert 2 == z2.count()

            # can't have duplicated keys
            raises(ValueError, lambda: e.zip(sa, sa, how="inner", partition_spec=ps))
            # not support semi or anti
            raises(
                InvalidOperationError,
                lambda: e.zip(sa, sa, how="anti", partition_spec=ps),
            )
            raises(
                InvalidOperationError,
                lambda: e.zip(sa, sa, how="leftsemi", partition_spec=ps),
            )
            raises(
                InvalidOperationError,
                lambda: e.zip(sa, sa, how="LEFT SEMI", partition_spec=ps),
            )
            # can't specify keys for cross join
            raises(
                InvalidOperationError,
                lambda: e.zip(sa, sa, how="cross", partition_spec=ps),
            )

            # test zip with unserialized dfs
            z3 = e.persist(e.zip(a, b, partition_spec=ps))
            df_eq(z1, z3, throw=True, check_metadata=False)
            z3 = e.persist(e.zip(a, sb, partition_spec=ps))
            df_eq(z1, z3, throw=True, check_metadata=False)
            z3 = e.persist(e.zip(sa, b, partition_spec=ps))
            df_eq(z1, z3, throw=True, check_metadata=False)

            z4 = e.persist(e.zip(a, b, how="left_outer", partition_spec=ps))
            df_eq(z2, z4, throw=True, check_metadata=False)
            z4 = e.persist(e.zip(a, sb, how="left_outer", partition_spec=ps))
            df_eq(z2, z4, throw=True, check_metadata=False)
            z4 = e.persist(e.zip(sa, b, how="left_outer", partition_spec=ps))
            df_eq(z2, z4, throw=True, check_metadata=False)

            z5 = e.persist(e.zip(a, b, how="cross"))
            assert z5.count() == 1
            assert len(z5.schema) == 2
            z6 = e.persist(e.zip(sa, b, how="cross"))
            assert z6.count() == 2
            assert len(z6.schema) == 3

            z7 = e.zip(a, b, df1_name="x", df2_name="y")
            z7.show()
            assert z7.metadata.get("serialized_has_name", False)

        def test_zip_all(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4], [1, 5]], "a:int,b:int")
            z = e.persist(e.zip_all(DataFrames(a)))
            assert 1 == z.count()
            assert z.metadata.get("serialized", False)
            assert not z.metadata.get("serialized_has_name", False)
            z = e.persist(e.zip_all(DataFrames(x=a)))
            assert 1 == z.count()
            assert z.metadata.get("serialized", False)
            assert z.metadata.get("serialized_has_name", False)
            z = e.persist(
                e.zip_all(DataFrames(x=a), partition_spec=PartitionSpec(by=["a"]))
            )
            assert 2 == z.count()
            assert z.metadata.get("serialized", False)
            assert z.metadata.get("serialized_has_name", False)

            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            c = e.to_df([[6, 1], [2, 7]], "d:int,a:int")
            z = e.persist(e.zip_all(DataFrames(a, b, c)))
            assert 1 == z.count()
            assert not z.metadata.get("serialized_has_name", False)
            z = e.persist(e.zip_all(DataFrames(x=a, y=b, z=c)))
            assert 1 == z.count()
            assert z.metadata.get("serialized_has_name", False)

            z = e.persist(e.zip_all(DataFrames(b, b)))
            assert 2 == z.count()
            assert not z.metadata.get("serialized_has_name", False)
            assert ["a", "c"] in z.schema
            z = e.persist(e.zip_all(DataFrames(x=b, y=b)))
            assert 2 == z.count()
            assert z.metadata.get("serialized_has_name", False)
            assert ["a", "c"] in z.schema

            z = e.persist(
                e.zip_all(DataFrames(b, b), partition_spec=PartitionSpec(by=["a"]))
            )
            assert 2 == z.count()
            assert not z.metadata.get("serialized_has_name", False)
            assert "c" not in z.schema

        def test_comap(self):
            ps = PartitionSpec(presort="b,c")
            e = self.engine
            a = e.to_df([[1, 2], [3, 4], [1, 5]], "a:int,b:int")
            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            z1 = e.persist(e.zip(a, b))
            z2 = e.persist(e.zip(a, b, partition_spec=ps, how="left_outer"))
            z3 = e.persist(
                e._serialize_by_partition(a, partition_spec=ps, df_name="_x")
            )
            z4 = e.persist(e.zip(a, b, partition_spec=ps, how="cross"))

            def comap(cursor, dfs):
                assert not dfs.has_key
                v = ",".join([k + str(v.count()) for k, v in dfs.items()])
                keys = cursor.key_value_array
                if len(keys) == 0:
                    return ArrayDataFrame([[v]], "v:str")
                return ArrayDataFrame([keys + [v]], cursor.key_schema + "v:str")

            def on_init(partition_no, dfs):
                assert not dfs.has_key
                assert partition_no >= 0
                assert len(dfs) > 0

            res = e.comap(
                z1,
                comap,
                "a:int,v:str",
                PartitionSpec(),
                metadata=dict(a=1),
                on_init=on_init,
            )
            df_eq(res, [[1, "_02,_11"]], "a:int,v:str", metadata=dict(a=1), throw=True)

            # for outer joins, the NULL will be filled with empty dataframe
            res = e.comap(z2, comap, "a:int,v:str", PartitionSpec(), metadata=dict(a=1))
            df_eq(
                res,
                [[1, "_02,_11"], [3, "_01,_10"]],
                "a:int,v:str",
                metadata=dict(a=1),
                throw=True,
            )

            res = e.comap(z3, comap, "v:str", PartitionSpec(), metadata=dict(a=1))
            df_eq(res, [["_03"]], "v:str", metadata=dict(a=1), throw=True)

            res = e.comap(z4, comap, "v:str", PartitionSpec(), metadata=dict(a=1))
            df_eq(res, [["_03,_12"]], "v:str", metadata=dict(a=1), throw=True)

        def test_comap_with_key(self):
            e = self.engine
            a = e.to_df([[1, 2], [3, 4], [1, 5]], "a:int,b:int")
            b = e.to_df([[6, 1], [2, 7]], "c:int,a:int")
            c = e.to_df([[6, 1]], "c:int,a:int")
            z1 = e.persist(e.zip(a, b, df1_name="x", df2_name="y"))
            z2 = e.persist(e.zip_all(DataFrames(x=a, y=b, z=b)))
            z3 = e.persist(
                e.zip_all(DataFrames(z=c), partition_spec=PartitionSpec(by=["a"]))
            )

            def comap(cursor, dfs):
                assert dfs.has_key
                v = ",".join([k + str(v.count()) for k, v in dfs.items()])
                keys = cursor.key_value_array
                # if len(keys) == 0:
                #    return ArrayDataFrame([[v]], "v:str")
                return ArrayDataFrame([keys + [v]], cursor.key_schema + "v:str")

            def on_init(partition_no, dfs):
                assert dfs.has_key
                assert partition_no >= 0
                assert len(dfs) > 0

            res = e.comap(
                z1,
                comap,
                "a:int,v:str",
                PartitionSpec(),
                metadata=dict(a=1),
                on_init=on_init,
            )
            df_eq(res, [[1, "x2,y1"]], "a:int,v:str", metadata=dict(a=1), throw=True)

            res = e.comap(
                z2,
                comap,
                "a:int,v:str",
                PartitionSpec(),
                metadata=dict(a=1),
                on_init=on_init,
            )
            df_eq(res, [[1, "x2,y1,z1"]], "a:int,v:str", metadata=dict(a=1), throw=True)

            res = e.comap(
                z3,
                comap,
                "a:int,v:str",
                PartitionSpec(),
                metadata=dict(a=1),
                on_init=on_init,
            )
            df_eq(res, [[1, "z1"]], "a:int,v:str", metadata=dict(a=1), throw=True)

        @pytest.fixture(autouse=True)
        def init_tmpdir(self, tmpdir):
            self.tmpdir = tmpdir

        def test_io(self):
            e = self.engine
            b = ArrayDataFrame([[6, 1], [2, 7]], "c:int,a:long")
            path = os.path.join(self.tmpdir, "a")
            e.save_df(b, path, format_hint="parquet", force_single=True)
            assert e.fs.isfile(path)
            c = e.load_df(path, format_hint="parquet", columns=["a", "c"])
            df_eq(c, [[1, 6], [7, 2]], "a:long,c:int", throw=True)

            path = os.path.join(self.tmpdir, "b.csv")
            e.save_df(b, path, header=True)
            c = e.load_df(path, header=True, columns="c:int,a:long")
            df_eq(c, b, throw=True)


def select_top(cursor, data):
    return ArrayDataFrame([cursor.row], cursor.row_schema)


class BinaryObject(object):
    def __init__(self, data=None):
        self.data = data


def binary_map(cursor, df):
    arr = df.as_array(type_safe=True)
    for i in range(len(arr)):
        obj = pickle.loads(arr[i][0])
        obj.data += "x"
        arr[i][0] = pickle.dumps(obj)
    return ArrayDataFrame(arr, df.schema)
