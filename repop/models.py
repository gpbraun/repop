"""
REPOP - Refinery modelling and optimisation
Author: Gabriel Braun, 2025
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

import pydantic as pyd
import pyomo.environ as pyo

from repop.utils import ModelList, VarMap
from repop.viz import flowchart

# ----------------------------------------------------------------------
# TYPE ALIAS FOR CUSTOM BLEND CONSTRAINT FUNCTIONS
# ----------------------------------------------------------------------
ConsFunc = Callable[["Refinery", str, Dict[str, Any]], Any]
ObjFunc = Callable[["Refinery", pyo.ConcreteModel], Any]


# ======================================================================
# DATA MODELS (with runtime Pyomo handles as PrivateAttr)
# ======================================================================


class Crude(pyd.BaseModel):
    name: str
    level: int = 0
    properties: Dict[str, float] = {}

    cost: float = 0.0
    availability: float

    _alloc: VarMap = pyd.PrivateAttr(default_factory=VarMap)


class Pool(pyd.BaseModel):
    name: str
    level: int = 1
    properties: Dict[str, float] = {}

    _feeds: VarMap = pyd.PrivateAttr(default_factory=VarMap)
    _alloc: VarMap = pyd.PrivateAttr(default_factory=VarMap)


class Unit(pyd.BaseModel):
    name: str
    level: int = 1
    cost: float = 0.0
    capacity: float
    yields: Dict[str, Dict[str, float]]
    constraints: Optional[List[BlendConstraint]] = None

    _feeds: VarMap = pyd.PrivateAttr(default_factory=VarMap)
    _exits: VarMap = pyd.PrivateAttr(default_factory=VarMap)

    @pyd.computed_field
    @property
    def yields_T(self) -> Dict[str, Dict[str, float]]:
        """
        exit -> {feed: yield} mapping.
        """
        exit_yields = defaultdict(dict)
        for f_name, y_map in self.yields.items():
            for e_name, y in y_map.items():
                exit_yields[e_name][f_name] = y
        return dict(exit_yields)


class BlendConstraint(pyd.BaseModel):
    name: str = pyd.Field(alias="type")
    properties: Dict[str, Any]

    @pyd.model_validator(mode="before")
    @classmethod
    def _gather_props(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        data["properties"] = {
            k: v for k, v in data.items() if k not in ("name", "type")
        }
        return data


class Blend(pyd.BaseModel):
    name: str
    price: float
    components: List[str]
    blend_ratios: Optional[List[Dict[str, float]]] = None
    constraints: Optional[List[BlendConstraint]] = None

    _feeds: VarMap = pyd.PrivateAttr(default_factory=VarMap)

    @pyd.field_validator("blend_ratios", mode="before")
    @classmethod
    def _validate_ratios(cls, v: Any, info) -> Optional[List[Dict[str, float]]]:
        """
        Validates that blend_ratios is a list of {component: ratio} dicts.
        """
        if v is None:
            return None

        comps = info.data.get("components", [])
        if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
            raise ValueError("blend_ratios must be a list of {component: ratio} dicts")

        normalized: List[Dict[str, float]] = []
        for row in v:
            entry: Dict[str, float] = {}
            for comp, val in row.items():
                if comp not in comps:
                    raise ValueError(
                        f"Component '{comp}' not in blend components {comps}"
                    )
                if not isinstance(val, (int, float)) or val <= 0:
                    continue
                entry[comp] = float(val)
            if not entry:
                raise ValueError(
                    "Each dict in blend_ratios must have at least one ratio > 0"
                )
            normalized.append(entry)
        return normalized


# ----------------------------------------------------------------------
# REFINERY ORCHESTRATOR
# ----------------------------------------------------------------------
class Refinery(pyd.BaseModel):
    """
    Refinery
    """

    metadata: Any
    crudes: ModelList[Crude]
    pools: ModelList[Pool]
    units: ModelList[Unit]
    blends: ModelList[Blend]

    # private attrs
    _obj_name: str = pyd.PrivateAttr(default="max_profit")
    _model: pyo.ConcreteModel | None = pyd.PrivateAttr(default=None)

    # class vars
    round_decimals: ClassVar[int] = 6
    b_cons: ClassVar[Dict[str, ConsFunc]] = {}
    u_cons: ClassVar[Dict[str, ConsFunc]] = {}
    obj_fns: ClassVar[Dict[str, ObjFunc]] = {}

    @pyd.model_validator(mode="before")
    @classmethod
    def _prepare_input_data(cls, values: dict) -> dict:
        """
        Pre-processes the input dictionary to conform to the Pydantic model structure.
        It transforms dicts of objects into lists and auto-generates the pool list.
        """
        c_map = {c_name: dict(c) for c_name, c in values.pop("crudes", {}).items()}
        u_map = {u_name: dict(u) for u_name, u in values.pop("units", {}).items()}
        b_map = {b_name: dict(b) for b_name, b in values.pop("blends", {}).items()}
        p_props = values.pop("pool_properties", {})

        # Automatically discover all pools from unit outputs and blend components
        p_set = set()
        for u in u_map.values():
            for feed_map in u.get("yields", {}).values():
                p_set.update(feed_map.keys())
        for b in b_map.values():
            p_set.update(b.get("feeds", []))

        # Builds up `values` dict
        values["blends"] = [{"name": b_name, **b} for b_name, b in b_map.items()]
        values["units"] = [{"name": u_name, **u} for u_name, u in u_map.items()]
        values["crudes"] = [
            {
                "name": c_name,
                "properties": p_props.get(c_name, {}),
                **c,
            }
            for c_name, c in c_map.items()
        ]
        values["pools"] = [
            {
                "name": p_name,
                "properties": p_props.get(p_name, {}),
            }
            for p_name in p_set
        ]

        return values

    def _assign_topological_levels(self) -> "Refinery":
        """
        Assigns a topological `.level` to each Unit and Pool. This is useful for
        flowchart visualization and ensuring a logical processing order.

        - Units: Level 1 if they only consume crudes; otherwise, 1 + max level
          of the units producing their feeds.
        - Pools: Level 0 if it's a crude; otherwise, the max level of the units
          that produce it.
        """
        # 1. Map each pool to the units that produce it
        producers = defaultdict(list)
        for unit in self.units:
            for out_map in unit.yields.values():
                for pool in out_map:
                    producers[pool].append(unit.name)

        crude_set = set(self.crudes.names)
        levels: Dict[str, int] = {}

        # 2. Initialize levels for units that only consume crudes (base case)
        for unit in self.units:
            if all(feed in crude_set for feed in unit.yields.keys()):
                levels[unit.name] = 1

        # 3. Iteratively assign levels to remaining units based on their feeds
        total_units = len(self.units)
        while len(levels) < total_units:
            progress_made = False
            for unit in self.units:
                if unit.name in levels:
                    continue

                # Try to compute the unit's level from its feed producers
                feed_levels = []
                can_compute_level = True
                for feed in unit.yields.keys():
                    if feed in crude_set:
                        feed_levels.append(0)
                    else:  # Feed is an intermediate pool
                        parent_units = producers.get(feed, [])
                        if any(parent not in levels for parent in parent_units):
                            can_compute_level = False
                            break
                        feed_levels.append(
                            max(levels[parent] for parent in parent_units)
                        )

                if can_compute_level:
                    levels[unit.name] = 1 + (max(feed_levels) if feed_levels else 0)
                    progress_made = True

            if not progress_made:
                # Fallback for cyclic dependencies or un-fed units
                for unit in self.units:
                    levels.setdefault(unit.name, 1)
                break

        # 4. Apply the calculated levels to the Unit objects
        for unit in self.units:
            unit.level = levels[unit.name]

        # 5. Calculate and apply levels to Pool objects
        for pool in self.pools:
            if pool.name in crude_set:
                pool.level = 0
            else:
                parent_units = producers.get(pool.name, [])
                pool.level = (
                    max(levels.get(p_unit, 0) for p_unit in parent_units)
                    if parent_units
                    else 0
                )

        return self

    def _define_pyomo_vars(self) -> None:
        """
        Pyomo...
        """
        mdl = self._model

        # Sets
        mdl.C = pyo.Set(initialize=sorted(self.crudes.names))
        mdl.P = pyo.Set(initialize=sorted(self.pools.names))
        mdl.U = pyo.Set(initialize=sorted(self.units.names))
        mdl.B = pyo.Set(initialize=sorted(self.blends.names))

        b_feeds = {(b.name, f) for b in self.blends for f in b.components}
        u_feeds = {(u.name, f) for u in self.units for f in u.yields.keys()}
        u_exits = {
            (u.name, e) for u in self.units for y in u.yields.values() for e in y.keys()
        }

        mdl.Bfeeds = pyo.Set(dimen=2, initialize=sorted(b_feeds))
        mdl.Ufeeds = pyo.Set(dimen=2, initialize=sorted(u_feeds))
        mdl.Uexits = pyo.Set(dimen=2, initialize=sorted(u_exits))

        # Variables: feeds/allocation
        mdl.b_feeds = pyo.Var(mdl.Bfeeds, domain=pyo.NonNegativeReals)
        mdl.u_feeds = pyo.Var(mdl.Ufeeds, domain=pyo.NonNegativeReals)
        mdl.u_exits = pyo.Var(mdl.Uexits, domain=pyo.NonNegativeReals)

        # Attach handles
        for b_name, f_name in b_feeds:
            var = mdl.b_feeds[b_name, f_name]
            self.blends[b_name]._feeds[f_name] = var
            self.pools[f_name]._alloc[b_name] = var

        for u_name, f_name in u_feeds:
            var = mdl.u_feeds[u_name, f_name]
            if f_name in self.crudes:
                self.units[u_name]._feeds[f_name] = var
                self.crudes[f_name]._alloc[u_name] = var
            elif f_name in self.pools:
                self.units[u_name]._feeds[f_name] = var
                self.pools[f_name]._alloc[u_name] = var

        for u_name, e_name in u_exits:
            var = mdl.u_exits[u_name, e_name]
            self.units[u_name]._exits[e_name] = var
            self.pools[e_name]._feeds[u_name] = var

    def _define_pyomo_cons(self) -> None:
        """
        Pyomo...
        """
        mdl = self._model

        # Core Constraints
        @mdl.Constraint(mdl.Uexits)
        def unit_exits_def(_m, u_name, p_name):
            u = self.units[u_name]
            e = sum(u._feeds[f_name] * y for f_name, y in u.yields_T[p_name].items())
            return u._exits[p_name] == e

        @mdl.Constraint(mdl.P)
        def pools_balance(_m, p_name):
            p = self.pools[p_name]
            return p._alloc.sum() <= p._feeds.sum()

        @mdl.Constraint(mdl.C)
        def crude_avaiability(_m, c_name):
            c = self.crudes[c_name]
            return c._alloc.sum() <= c.availability

        @mdl.Constraint(mdl.U)
        def unit_capacity(_m, u_name):
            u = self.units[u_name]
            return u._feeds.sum() <= u.capacity

        # custom blend constraints
        mdl.custom_blend_cons = pyo.ConstraintList()
        for b in self.blends:
            for spec in b.constraints or []:
                fn = self.b_cons.get(spec.name.lower())
                if fn is None:
                    raise KeyError(f"Blend constraint '{spec.name}' not registered.")
                mdl.custom_blend_cons.add(fn(self, b.name, spec.properties))

        # custom unit constraints
        mdl.custom_unit_cons = pyo.ConstraintList()
        for u in self.units:
            for spec in u.constraints or []:
                fn = self.u_cons.get(spec.name.lower())
                if fn is None:
                    raise KeyError(f"Unit constraint '{spec.name}' not registered.")
                mdl.custom_unit_cons.add(fn(self, u.name, spec.properties))

        # blend ratio constraints
        ratio_triplets = [
            (b.name, k, p, coeff)
            for b in self.blends
            for k, row in enumerate(b.blend_ratios or [])
            for p, coeff in row.items()
        ]
        if ratio_triplets:
            aux_keys = {(b, k) for (b, k, _p, _c) in ratio_triplets}
            mdl.r_aux = pyo.Var(aux_keys, domain=pyo.NonNegativeReals)

            @mdl.Constraint(ratio_triplets)
            def fix_ratios(_m, b, k, p, coeff):
                return mdl.b_feeds[b, p] == coeff * mdl.r_aux[b, k]

    @pyd.model_validator(mode="after")
    def _create_model(self) -> "Refinery":
        """
        Pyomo...
        """
        self._assign_topological_levels()

        self._model = pyo.ConcreteModel("RefineryCore")
        self._define_pyomo_vars()
        self._define_pyomo_cons()

        # objective
        expr = self.obj_fns[self._obj_name](self, self._model)
        self._model.objective = pyo.Objective(expr=expr, sense=pyo.maximize)

        return self

    @classmethod
    def blend_constraint(cls, name: str):
        def decorator(fn: ConsFunc):
            cls.b_cons[name.lower()] = fn
            return fn

        return decorator

    @classmethod
    def unit_constraint(cls, name: str):
        def decorator(fn: ConsFunc):
            cls.u_cons[name.lower()] = fn
            return fn

        return decorator

    @classmethod
    def objective(cls, name: str):
        def decorator(fn: ObjFunc):
            cls.obj_fns[name] = fn
            return fn

        return decorator

    def optimize(
        self, *, solver_name: str = "glpk", tee: bool = False
    ) -> pyo.ConcreteModel:
        if self._model is None:
            raise RuntimeError("Pyomo model not initialised.")
        pyo.SolverFactory(solver_name).solve(self._model, tee=tee)
        return self._model

    # -------------------------------------------------------------- #
    # FLOW-CHART HELPER (unchanged)
    # -------------------------------------------------------------- #
    def generate_flowchart(
        self,
        *,
        file_name: str | Path = "flowchart.svg",
        file_format: str | None = None,
        theme: Dict[str, Dict[str, str]] | None = None,
    ) -> Path:
        return flowchart(
            self, file_name=file_name, file_format=file_format, theme=theme
        )


@Refinery.objective("max_profit")
def _default_profit(ref: "Refinery", m: pyo.ConcreteModel) -> pyo.Expr:
    revenue = sum(ref.blends[b]._feeds.sum() * ref.blends[b].price for b in m.B)
    unit_op = sum(ref.units[u]._feeds.sum() * ref.units[u].cost for u in m.U)
    crude_c = sum(ref.crudes[c]._alloc.sum() * ref.crudes[c].cost for c in m.C)

    return revenue - unit_op - crude_c
