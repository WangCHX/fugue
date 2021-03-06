from fugue.extensions.creator import Creator
from fugue.dataframe import DataFrame


class CreateData(Creator):
    def create(self) -> DataFrame:
        return self.execution_engine.to_df(
            self.params.get_or_throw("data", object),
            self.params.get_or_none("schema", object),
            self.params.get_or_none("metadata", object),
        )


class Load(Creator):
    def create(self) -> DataFrame:
        kwargs = self.params.get("params", dict())
        path = self.params.get_or_throw("path", str)
        format_hint = self.params.get("fmt", "")
        columns = self.params.get_or_none("columns", object)

        return self.execution_engine.load_df(
            path=path, format_hint=format_hint, columns=columns, **kwargs
        )
