from typing import Any
from fugue.exceptions import FugueError


class FugueSQLError(FugueError):
    """Fugue SQL error"""

    def __init__(self, *args: Any):
        super().__init__(*args)


class FugueSQLSyntaxError(FugueSQLError):
    """Fugue SQL syntax error"""

    def __init__(self, *args: Any):
        super().__init__(*args)
