"""
Visualization utilities for REPOP.
Provides a Graphviz left-to-right process-flow diagram for a `Refinery` instance.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, Set, Tuple

if TYPE_CHECKING:
    # static-analysis-only import to avoid circularity
    from repop.models import Refinery

try:
    from graphviz import Digraph
except ImportError as exc:
    raise RuntimeError(
        "The `graphviz` package is required for flowchart visualization."
    ) from exc


# Color palette
CRUDE_COLOR = "#fb7185"
POOL_COLOR = "#fde68a"
BLEND_COLOR = "#a5b4fc"
UNIT_COLOR = "#cbd5e1"
EDGE_COLOR = "#1e293b"

_BASE_STYLES: Dict[str, Dict[str, str]] = {
    "Crude": {
        "shape": "box",
        "style": "filled,rounded",
        "fillcolor": CRUDE_COLOR,
        "fontname": "Helvetica",
        "fontsize": "10",
    },
    "Unit": {
        "shape": "box",
        "style": "filled,rounded",
        "fillcolor": UNIT_COLOR,
        "fontname": "Helvetica",
        "fontsize": "10",
        "height": "1.2",
    },
    "Pool": {
        "shape": "ellipse",
        "style": "filled,rounded",
        "fillcolor": POOL_COLOR,
        "fontname": "Helvetica",
        "fontsize": "10",
    },
    "Blend": {
        "shape": "box",
        "style": "filled,rounded",
        "fillcolor": BLEND_COLOR,
        "fontname": "Helvetica",
        "fontsize": "10",
    },
}


def flowchart(
    refinery: "Refinery",
    *,
    file_name: str | Path = "flowchart.svg",
    file_format: str | None = None,
    theme: Dict[str, Dict[str, str]] | None = None,
) -> Path:
    """
    Render a left-to-right process-flow diagram for `Refinery`.

    Parameters
    ---
    file_name
        Path (str or Path) to the output file. If it has an extension
        and `file_format` is not given, the extension determines the format.
    file_format
        Explicit format such as "pdf", "png", or "svg".  Overrides any
        mismatching extension in `file_name`.  If *file_name* has no
        extension, one is added automatically.
    theme
        Optional dict that overrides entries in the style palette.

    Returns
    ---
    Path
        Path to the rendered file.
    """
    path = Path(file_name)
    suffix = path.suffix.lstrip(".").lower()

    if file_format is None:
        if suffix:
            file_format = suffix
        else:
            raise ValueError(
                "file_format must be provided when file_name has no extension."
            )
    else:
        file_format = file_format.lstrip(".").lower()
        if not suffix:
            # add an extension that matches the chosen format
            path = path.with_suffix("." + file_format)

    # Graphviz wants the stem (no extension) when format is supplied separately
    stem_path = path.with_suffix("")

    # -----------------------------------------------------------------------
    # fast look-ups
    # -----------------------------------------------------------------------
    crude_names: Set[str] = set(refinery.crudes.names())
    blend_names: Set[str] = set(refinery.blends.names())
    unit_objs = tuple(refinery.units)
    pool_objs = tuple(refinery.pools)

    def _group(name: str) -> str:
        return name.split(".", 1)[0]

    pool_group = {p.name: _group(p.name) for p in pool_objs}

    # -----------------------------------------------------------------------
    # graph setup
    # -----------------------------------------------------------------------
    dot = Digraph(comment="Process Flowchart", format=file_format)
    dot.attr(rankdir="LR", splines="ortho", fontsize="10", fontname="Helvetica")

    styles = {**_BASE_STYLES, **(theme or {})}

    # -----------------------------------------------------------------------
    # node layers
    # -----------------------------------------------------------------------
    from collections import defaultdict

    layers: Dict[int, list[Tuple[str, str, str]]] = defaultdict(list)
    for c in sorted(crude_names):
        layers[0].append((f"Crude_{c}", c, "Crude"))

    seen_pool_nodes: Set[str] = set()
    for u in unit_objs:
        layers[2 * u.level - 1].append((f"Unit_{u.name}", u.name, "Unit"))

    for p in pool_objs:
        gname = pool_group[p.name]
        if gname not in seen_pool_nodes:
            layers[2 * p.level].append((f"Pool_{gname}", gname, "Pool"))
            seen_pool_nodes.add(gname)

    max_lvl = max((u.level for u in unit_objs), default=0)
    for b in sorted(blend_names):
        layers[2 * max_lvl + 2].append((f"Blend_{b}", b, "Blend"))

    for col, nodes in sorted(layers.items()):
        with dot.subgraph(name=f"col_{col}") as sg:
            sg.attr(rank="same", style="invis")
            for node_id, label, kind in nodes:
                sg.node(node_id, label, **styles[kind])

    # -----------------------------------------------------------------------
    # edges
    # -----------------------------------------------------------------------
    drawn: Set[Tuple[str, str]] = set()

    def _add(src: str, dst: str):
        if (src, dst) not in drawn:
            dot.edge(src, dst, arrowsize="0.5", color=EDGE_COLOR)
            drawn.add((src, dst))

    def _edge_unit_feeds() -> Iterable[Tuple[str, str]]:
        for u in unit_objs:
            for f in u.feeds:
                skind = "Crude" if f in crude_names else "Pool"
                sname = f if skind == "Crude" else pool_group[f]
                yield f"{skind}_{sname}", f"Unit_{u.name}"

    def _edge_unit_outputs() -> Iterable[Tuple[str, str]]:
        for u in unit_objs:
            for mapping in u.yields.values():
                for p in mapping:
                    yield f"Unit_{u.name}", f"Pool_{pool_group[p]}"

    def _edge_pool_blend() -> Iterable[Tuple[str, str]]:
        for b in refinery.blends:
            for p in b.components:
                yield f"Pool_{pool_group[p]}", f"Blend_{b.name}"

    for src, dst in (
        *_edge_unit_feeds(),
        *_edge_unit_outputs(),
        *_edge_pool_blend(),
    ):
        _add(src, dst)

    rendered = dot.render(str(stem_path), cleanup=True, view=False)

    return Path(rendered)
