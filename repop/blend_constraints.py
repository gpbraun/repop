"""
Blend-level quality and production constraints for REPOP.

Every function is registered with the `@Refinery.blend_constraint("<name>")`
decorator.  The function signature is now:

    (refinery, blend_name, props_dict) -> Pyomo expression / Constraint
"""

from __future__ import annotations

import pyomo.environ as pyo

from .models import Refinery


# ---------------------------------------------------------------------------
# HELPER – weighted-property sum for a given blend
# ---------------------------------------------------------------------------
def _weighted_sum(
    ref: Refinery,
    b_name: str,
    prop_key: str,
) -> pyo.Expr:
    """Σ allocation * property_value for the specified property key."""
    alloc = ref.blends[b_name]._feeds  # VarMap
    return sum(alloc[p] * ref.pools[p].properties.get(prop_key, 0.0) for p in alloc)


# ---------------------------------------------------------------------------
#  MIN / MAX PROPERTY CONSTRAINTS
# ---------------------------------------------------------------------------
@Refinery.blend_constraint("min_ron")
def min_ron(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    threshold = props["value"]
    alloc_sum = ref.blends[b_name]._feeds.sum()
    return _weighted_sum(ref, b_name, "RON") >= threshold * alloc_sum


@Refinery.blend_constraint("max_rvp")
def max_rvp(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    threshold = props["value"]
    alloc_sum = ref.blends[b_name]._feeds.sum()
    return _weighted_sum(ref, b_name, "RVP") <= threshold * alloc_sum


@Refinery.blend_constraint("max_sulphur")
def max_sulphur(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    threshold = props["value"]
    alloc_sum = ref.blends[b_name]._feeds.sum()
    return _weighted_sum(ref, b_name, "sulphur") <= threshold * alloc_sum


# ---------------------------------------------------------------------------
#  PRODUCTION RATIO CONSTRAINTS
# ---------------------------------------------------------------------------
@Refinery.blend_constraint("min_ratio")
def min_ratio(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    factor = props["value"]
    ref_b = props["reference"]
    return ref.blends[b_name]._feeds.sum() >= factor * ref.blends[ref_b]._feeds.sum()


@Refinery.blend_constraint("max_ratio")
def max_ratio(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    factor = props["value"]
    ref_b = props["reference"]
    return ref.blends[b_name]._feeds.sum() <= factor * ref.blends[ref_b]._feeds.sum()


# ---------------------------------------------------------------------------
#  ABSOLUTE MIN / MAX PRODUCTION
# ---------------------------------------------------------------------------
@Refinery.blend_constraint("min_production")
def min_production(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    return ref.blends[b_name]._feeds.sum() >= props["value"]


@Refinery.blend_constraint("max_production")
def max_production(ref: Refinery, b_name: str, props: dict) -> pyo.Expr:
    return ref.blends[b_name]._feeds.sum() <= props["value"]
