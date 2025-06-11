from .models import Refinery


@Refinery.blend_constraint()
def min_ron(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    threshold = constraint_props["value"]
    return (
        sum(pool_allocs[p] * pool_properties[p].get("RON", 0.0) for p in pool_allocs)
        >= threshold * blend_qtys[blend_name]
    )


@Refinery.blend_constraint()
def max_rvp(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    threshold = constraint_props["value"]
    return (
        sum(pool_allocs[p] * pool_properties[p].get("RVP", 0.0) for p in pool_allocs)
        <= threshold * blend_qtys[blend_name]
    )


@Refinery.blend_constraint()
def max_sulphur(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    threshold = constraint_props["value"]
    return (
        sum(
            pool_allocs[p] * pool_properties[p].get("sulphur", 0.0) for p in pool_allocs
        )
        <= threshold * blend_qtys[blend_name]
    )


@Refinery.blend_constraint()
def min_ratio(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    factor = constraint_props["value"]
    ref = constraint_props["reference"]
    return blend_qtys[blend_name] >= factor * blend_qtys[ref]


@Refinery.blend_constraint()
def max_ratio(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    factor = constraint_props["value"]
    ref = constraint_props["reference"]
    return blend_qtys[blend_name] <= factor * blend_qtys[ref]


@Refinery.blend_constraint()
def min_production(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    return blend_qtys[blend_name] >= constraint_props["value"]


@Refinery.blend_constraint()
def max_production(
    blend_name,
    constraint_props,
    blend_qtys,
    pool_allocs,
    pool_properties,
):
    return blend_qtys[blend_name] <= constraint_props["value"]
