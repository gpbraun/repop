from typing import TYPE_CHECKING

import pyomo.environ as pyo

if TYPE_CHECKING:
    from pyomo.core.expr.numvalue import NumericValue


class VarMap(dict[str, pyo.Var]):
    """
    Mapping str → Pyomo Var with a helper to add items safely.
    """

    def sum(self) -> "NumericValue":
        return sum(self.values())
