"""
Blend-level quality and production constraints for REPOP.

Every function is registered with the `@Refinery.blend_constraint("<name>")`
decorator.  The function signature is now:

    (refinery, blend_name, props_dict) -> Pyomo expression / Constraint
"""

import pyomo.environ as pyo

from .models import Blend, Refinery


# ---------------------------------------------------------------------------
# HELPER – weighted-property sum for a given blend
# ---------------------------------------------------------------------------
def _weighted_sum(ref: Refinery, b_name: str, prop_key: str):
    """Σ allocation * property_value for the specified property key."""
    alloc = ref.blends[b_name]._feeds  # VarMap
    return sum(alloc[p] * ref.pools[p].properties.get(prop_key, 0.0) for p in alloc)


# ---------------------------------------------------------------------------
#  MIN / MAX PROPERTY CONSTRAINTS
# ---------------------------------------------------------------------------
@Blend.constraint("min_ron")
def min_ron(ref: Refinery, b_name: str, props: dict):
    threshold = props["value"]
    alloc_sum = ref.blends[b_name]._feeds.sum()
    return _weighted_sum(ref, b_name, "RON") >= threshold * alloc_sum


@Blend.constraint("max_rvp")
def max_rvp(ref: Refinery, b_name: str, props: dict):
    threshold = props["value"]
    alloc_sum = ref.blends[b_name]._feeds.sum()
    return _weighted_sum(ref, b_name, "RVP") <= threshold * alloc_sum


@Blend.constraint("max_sulphur")
def max_sulphur(ref: Refinery, b_name: str, props: dict):
    threshold = props["value"]
    alloc_sum = ref.blends[b_name]._feeds.sum()
    return _weighted_sum(ref, b_name, "sulphur") <= threshold * alloc_sum


# ---------------------------------------------------------------------------
#  PRODUCTION RATIO CONSTRAINTS
# ---------------------------------------------------------------------------
@Blend.constraint("min_ratio")
def min_ratio(ref: Refinery, b_name: str, props: dict):
    factor = props["value"]
    ref_b = props["reference"]
    return ref.blends[b_name]._feeds.sum() >= factor * ref.blends[ref_b]._feeds.sum()


@Blend.constraint("max_ratio")
def max_ratio(ref: Refinery, b_name: str, props: dict):
    factor = props["value"]
    ref_b = props["reference"]
    return ref.blends[b_name]._feeds.sum() <= factor * ref.blends[ref_b]._feeds.sum()


# ---------------------------------------------------------------------------
#  ABSOLUTE MIN / MAX PRODUCTION
# ---------------------------------------------------------------------------
@Blend.constraint("min_production")
def min_production(ref: Refinery, b_name: str, props: dict):
    return ref.blends[b_name]._feeds.sum() >= props["value"]


@Blend.constraint("max_production")
def max_production(ref: Refinery, b_name: str, props: dict):
    return ref.blends[b_name]._feeds.sum() <= props["value"]


@Blend.constraint("fixed_ratio")
def fixed_ratio(ref: Refinery, b_name: str, props: dict):
    """
    Internally creates an auxiliary variable so the rule is linear.
    """
    m = ref._model

    # VarList for auxiliaries, created once
    if not hasattr(m, "ratio_aux"):
        m.ratio_aux = pyo.VarList(domain=pyo.NonNegativeReals)
    aux = m.ratio_aux.add()  # fresh aux variable for this row

    ratios = props["ratios"]

    # build & return simple expressions
    return [m.b_feeds[b_name, pool] == coeff * aux for pool, coeff in ratios.items()]
