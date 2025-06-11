"""
Refinery modeling and optimization using Pydantic v2 and Pyomo.

This module provides a framework for building, validating, and optimizing
refinery process models. It leverages Pydantic for robust data modeling and
validation, and Pyomo for mathematical optimization. Key features include
modeling of Pools, Blends (with component ratios), and customizable quality
constraints via a flexible function registration system.

Author: Gabriel Braun, 2025
"""

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from pyomo.environ import (
    ConcreteModel,
    Constraint,
    ConstraintList,
    Expression,
    NonNegativeReals,
    Objective,
    Set,
    SolverFactory,
    Var,
    maximize,
    value,
)

from repop.utils import ModelList
from repop.viz import flowchart

# Type alias for the blending function signature.
# (blend_name,
#  constraint_props, # dict of properties for this constraint
#  blend_qtys,       # map of blend_name -> blend quantity Var
#  pool_allocs,      # map of pool_name -> allocation Var for this blend
#  pool_properties)  # map of pool_name -> its properties dict
BlendFunc = Callable[
    [str, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, float]]],
    Any,
]


# ──────────────────────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────────────────────
class Metadata(BaseModel):
    """Represents the metadata for the refinery model."""

    description: str
    version: str
    last_updated: str
    author: str


class Pool(BaseModel):
    """Represents an intermediate product pool in the refinery."""

    name: str
    quantity: float = 0.0  # Result field populated after optimization
    level: int = 1  # Topological level, calculated by Refinery
    properties: Dict[str, float] = {}


class Crude(BaseModel):
    """Represents a crude oil stream available to the refinery."""

    name: str
    availability: float
    cost: float = 0.0
    quantity: float = 0.0  # Result field populated after optimization


class Unit(BaseModel):
    """Represents a processing unit in the refinery."""

    name: str
    capacity: float
    level: int = 1  # Topological level, calculated by Refinery
    cost: float = 0.0
    yields: Dict[str, Dict[str, float]]  # Maps feed name to {output_pool: yield}

    @computed_field
    @property
    def feeds(self) -> List[str]:
        """Lists all possible input feeds for this unit."""
        return list(self.yields.keys())


class BlendConstraint(BaseModel):
    """A generic quality constraint to be applied to a blend."""

    name: str = Field(alias="type")
    properties: Dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _assemble_properties(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Gathers all fields other than 'name' or 'type' into a 'properties' dict.
        This allows defining constraints with a flat structure in the input file.
        """
        data["properties"] = {
            k: v for k, v in data.items() if k not in ("name", "type")
        }
        return data


class Blend(BaseModel):
    """Represents a final product blend."""

    name: str
    price: float
    components: List[str]
    blend_ratios: Optional[List[Dict[str, float]]] = None
    constraints: Optional[List[BlendConstraint]] = None
    quantity: float = 0.0  # Result field populated after optimization

    @field_validator("blend_ratios", mode="before")
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


# ──────────────────────────────────────────────────────────────────────────────
# Refinery Orchestrator
# ──────────────────────────────────────────────────────────────────────────────
class Refinery(BaseModel):
    """
    The main class that orchestrates the refinery model. It holds the data,
    builds the optimization problem, solves it, and stores the results.
    """

    metadata: Metadata
    crudes: ModelList[Crude]
    units: ModelList[Unit]
    blends: ModelList[Blend]
    pools: ModelList[Pool]

    round_decimals: ClassVar[int] = 6
    blend_constraints: ClassVar[Dict[str, BlendFunc]] = {}

    class Config:
        extra = "ignore"

    @model_validator(mode="before")
    @classmethod
    def _prepare_input_data(cls, values: dict) -> dict:
        """
        Pre-processes the input dictionary to conform to the Pydantic model structure.
        It transforms dicts of objects into lists and auto-generates the pool list.
        """
        values["crudes"] = [
            {"name": n, **d} for n, d in values.get("crudes", {}).items()
        ]
        values["units"] = [{"name": n, **d} for n, d in values.get("units", {}).items()]

        raw_blending = values.get("blending", {})
        values["blends"] = [{"name": n, **d} for n, d in raw_blending.items()]

        # Automatically discover all pools from unit outputs and blend components
        pool_set: set[str] = set()
        for u in values["units"]:
            for feed_map in u.get("yields", {}).values():
                pool_set.update(feed_map.keys())
        for b in raw_blending.values():
            pool_set.update(b.get("components", []))

        # Create pool objects using the discovered names and provided properties
        props = values.pop("pool_properties", {})
        values["pools"] = [
            {"name": p, "properties": props.get(p, {})} for p in sorted(pool_set)
        ]
        return values

    @model_validator(mode="after")
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

        crude_set = set(self.crudes.names())
        levels: Dict[str, int] = {}

        # 2. Initialize levels for units that only consume crudes (base case)
        for unit in self.units:
            if all(feed in crude_set for feed in unit.feeds):
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
                for feed in unit.feeds:
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

    @classmethod
    def blend_constraint(cls, name: str | None = None):
        """
        A decorator to register a new blend quality constraint function.

        Args:
            name (str, optional): The name to register the constraint with.
                If None, the function's name is used.
        """

        def decorator(constraint_func: Callable):
            constraint_name = name or constraint_func.__name__
            key = constraint_name.strip().replace(" ", "_").lower()
            cls.blend_constraints[key] = constraint_func
            return constraint_func

        return decorator

    def optimize(self, solver_name: str = "glpk", tee: bool = False) -> ConcreteModel:
        """
        Builds and solves the Pyomo optimization model for the refinery.

        Args:
            solver_name (str): The name of the solver to use (e.g., 'gurobi', 'glpk').
            tee (bool): If True, streams the solver's output to the console.

        Returns:
            ConcreteModel: The solved Pyomo model instance.
        """
        mdl = ConcreteModel("RefineryProfitMaximization")

        # --- Sets ---
        crudes = self.crudes.names()
        units = self.units.names()
        blends = self.blends.names()
        pools = self.pools.names()

        mdl.Crudes = Set(initialize=sorted(crudes))
        mdl.Units = Set(initialize=sorted(units))
        mdl.Blends = Set(initialize=sorted(blends))
        mdl.Pools = Set(initialize=sorted(pools))

        # --- Variables ---
        # Feed quantity to each unit from a crude or pool
        feed_idx = [(u, f) for u in units for f in self.units[u].feeds]
        mdl.feed_qty = Var(Set(initialize=feed_idx), domain=NonNegativeReals)

        # Quantity of each pool allocated to a blend
        mdl.allocate = Var(mdl.Blends, mdl.Pools, domain=NonNegativeReals)

        # Total quantity of each final blend produced
        mdl.blend_qty = Var(mdl.Blends, domain=NonNegativeReals)

        # --- Expressions ---
        # Total production of each pool from all unit outputs
        mdl.Production = Expression(
            mdl.Pools,
            rule=lambda m, p: sum(
                self.units[u].yields[f].get(p, 0.0) * m.feed_qty[u, f]
                for u in units
                for f in self.units[u].feeds
                if p in self.units[u].yields[f]
            ),
        )

        # --- Constraints ---
        # 1. Unit Capacity: Total feed to a unit cannot exceed its capacity.
        mdl.UnitCapacity = Constraint(
            mdl.Units,
            rule=lambda m, u: sum(m.feed_qty[u, f] for f in self.units[u].feeds)
            <= self.units[u].capacity,
        )

        # 2. Crude Availability: Total crude used cannot exceed its availability.
        mdl.CrudeLimit = Constraint(
            mdl.Crudes,
            rule=lambda m, c: sum(
                m.feed_qty[u, c] for u in units if c in self.units[u].feeds
            )
            <= self.crudes[c].availability,
        )

        # 3. Blend Definition: The total quantity of a blend is the sum of its components.
        mdl.BlendDef = Constraint(
            mdl.Blends,
            rule=lambda m, b: m.blend_qty[b]
            == sum(m.allocate[b, p] for p in self.blends[b].components),
        )

        # 4. Pool Mass Balance: Total consumption of a pool (as feed to units or
        #    for blending) cannot exceed its total production.
        pool_to_blends = {
            p: [b for b in blends if p in self.blends[b].components] for p in pools
        }
        mdl.PoolBalance = Constraint(
            mdl.Pools,
            rule=lambda m, p: (
                sum(m.feed_qty[u, p] for u in units if p in self.units[u].feeds)
                + sum(m.allocate[b, p] for b in pool_to_blends.get(p, []))
                <= m.Production[p]
            ),
        )

        # 5. Custom Quality Constraints: Apply user-defined blending rules.
        mdl.Quality = ConstraintList()
        blend_qtys_map = {b: mdl.blend_qty[b] for b in blends}
        for b_obj in self.blends:
            b = b_obj.name
            pool_allocs = {p: mdl.allocate[b, p] for p in b_obj.components}
            pool_props = {p: self.pools[p].properties for p in b_obj.components}
            for cons in b_obj.constraints or []:
                key = cons.name.strip().replace(" ", "_").lower()
                fn = self.blend_constraints.get(key)
                if fn is None:
                    raise KeyError(f"Blend constraint '{cons.name}' is not registered.")
                mdl.Quality.add(
                    fn(b, cons.properties, blend_qtys_map, pool_allocs, pool_props)
                )

        # 6. Fixed Blend Ratios: Enforce fixed proportions for blend components.
        if any(b.blend_ratios for b in self.blends):
            ratio_map = {
                (b.name, k, p): r
                for b in self.blends
                for k, row in enumerate(b.blend_ratios or [])
                for p, r in row.items()
            }
            if ratio_map:
                ratio_keys = sorted({(b, k) for (b, k, _) in ratio_map})
                triplets = sorted(ratio_map)
                mdl.ratio_aux = Var(Set(initialize=ratio_keys), domain=NonNegativeReals)
                mdl.RatioFix = Constraint(
                    Set(initialize=triplets),
                    rule=lambda m, b, k, p: m.allocate[b, p]
                    == ratio_map[(b, k, p)] * m.ratio_aux[b, k],
                )

        # --- Objective Function ---
        # Maximize profit = (revenue from blends) - (unit operating costs) - (crude costs)
        mdl.Profit = Objective(
            rule=lambda m: (
                sum(self.blends[b].price * m.blend_qty[b] for b in blends)
                - sum(
                    self.units[u].cost
                    * sum(m.feed_qty[u, f] for f in self.units[u].feeds)
                    for u in units
                )
                - sum(
                    self.crudes[c].cost
                    * sum(m.feed_qty[u, c] for u in units if c in self.units[u].feeds)
                    for c in crudes
                )
            ),
            sense=maximize,
        )

        # Solve the model
        SolverFactory(solver_name).solve(mdl, tee=tee)

        # Populate results back into the Pydantic models
        prec = self.round_decimals
        for c in crudes:
            qty = sum(
                float(value(mdl.feed_qty[u, c]))
                for u in units
                if (u, c) in mdl.feed_qty.index_set()
            )
            self.crudes[c].quantity = round(qty, prec)

        for b in blends:
            self.blends[b].quantity = round(float(value(mdl.blend_qty[b])), prec)

        for p in pools:
            prod = float(value(mdl.Production[p]))
            self.pools[p].quantity = round(prod, prec)

        return mdl

    def generate_flowchart(
        self,
        *,
        file_name: str | Path = "flowchart.svg",
        file_format: str | None = None,
        theme: Dict[str, Dict[str, str]] | None = None,
    ) -> Path:
        """
        Build and render a left-to-right process flow diagram for the given refinery.
        """
        flowchart(self, file_name=file_name, file_format=file_format, theme=theme)
