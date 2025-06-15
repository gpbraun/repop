"""
Console-side reporting for REPOP
--------------------------------
Pretty-prints a solved Refinery instance with Rich.

Usage
-----
>>> from repop.console_results import log_results
>>> ref.optimize()
>>> log_results(ref)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import pyomo.environ as pyo
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from repop.models import Refinery


# ── helpers ──────────────────────────────────────────────────────────
def _norm_zero(v: float, prec: int) -> float:
    return 0.0 if round(v, prec) == 0 else v


def _fmt_qty(v: float, prec: int = 2) -> str:
    v = _norm_zero(v, prec)
    txt = f"{v:,.{prec}f}"
    return f"[magenta]{txt}[/magenta]" if abs(v) > 1e-9 else f"[dim]{txt}[/dim]"


def _fmt_money(v: float, kind: str = "normal", symbol: str = "$") -> str:
    v = _norm_zero(v, 2)
    txt = f"{symbol}{v:,.2f}"
    if abs(v) < 1e-9:
        return f"[dim]{txt}[/dim]"
    colour = {"rev": "green", "cost": "red"}.get(kind, "magenta")
    return f"[{colour}]{txt}[/{colour}]"


def _style_name(name: str) -> str:
    return f"[bold cyan]{name}[/bold cyan]"


# ── public API ───────────────────────────────────────────────────────
def log_results(ref: Refinery, *, console: Console | None = None) -> None:
    """
    Pretty-print a solved Refinery.

    Parameters
    ----------
    ref : Refinery
        A **solved** refinery instance (`ref.optimize()` already called).
    console : rich.console.Console | None
        Optionally pass an existing Console (default: create a new one).
    """
    if ref._model is None:
        raise RuntimeError("Refinery has no Pyomo model – call optimise() first.")

    con = console or Console()
    m: pyo.ConcreteModel = ref._model  # shorthand
    hdr_style, WIDTH, PAD = "white", 120, (1, 2)

    # -----------------------------------------------------------------
    # small builders
    # -----------------------------------------------------------------
    def _tbl() -> Table:
        return Table(
            box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style=hdr_style
        )

    def _panel(tbl: Table, title: str, colour: str) -> Panel:
        return Panel(
            tbl,
            title=f"[bold]{title.upper()}[/bold]",
            title_align="center",
            border_style=colour,
            box=box.ROUNDED,
            width=WIDTH,
            padding=PAD,
        )

    # -----------------------------------------------------------------
    # overview (profit line)
    # -----------------------------------------------------------------
    sales = sum(bl.price * pyo.value(bl._feeds.sum()) for bl in ref.blends)
    op_cost = sum(u.cost * pyo.value(u._feeds.sum()) for u in ref.units)
    in_cost = sum(c.cost * pyo.value(c._alloc.sum()) for c in ref.crudes)
    profit = sales - op_cost - in_cost

    t = _tbl()
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    t.add_row("Sales", _fmt_money(sales, "rev"))
    t.add_row("Op cost", _fmt_money(op_cost, "cost"))
    t.add_row("In cost", _fmt_money(in_cost, "cost"))
    t.add_section()
    t.add_row("Profit", _fmt_money(profit, "rev"))
    con.print()
    con.print(_panel(t, "Overview", "green"))

    # -----------------------------------------------------------------
    # crude summary
    # -----------------------------------------------------------------
    t = _tbl()
    crude_names: List[str] = sorted(c.name for c in ref.crudes)
    t.add_column("Metric")
    for c in crude_names:
        t.add_column(_style_name(c), justify="right")

    qtys = [pyo.value(ref.crudes[c]._alloc.sum()) for c in crude_names]
    costs = [ref.crudes[c].cost * q for c, q in zip(crude_names, qtys)]

    t.add_section()
    t.add_row("Total", *(_fmt_qty(q) for q in qtys))
    t.add_row("Cost", *(_fmt_money(c, "cost") for c in costs))
    con.print(_panel(t, "Crudes", "yellow"))

    # -----------------------------------------------------------------
    # unit details (ordered by level)
    # -----------------------------------------------------------------
    # build quick group map for prefix-based display
    groups: Dict[str, List[str]] = defaultdict(list)
    for nm in sorted([*ref.crudes.names, *ref.pools.names]):
        groups[nm.split(".", 1)[0]].append(nm)

    for u in sorted(ref.units, key=lambda x: x.level):
        in_groups = sorted(
            g for g, ns in groups.items() if any(f in ns for f in u._feeds)
        )
        out_groups = sorted(
            g for g, ns in groups.items() if any(p in ns for p in u._exits)
        )

        # compute flows
        q_in = {
            g: pyo.value(sum(u._feeds[f] for f in groups[g] if f in u._feeds))
            for g in in_groups
        }
        cost_in = {g: q_in[g] * u.cost for g in in_groups}

        out_map: Dict[str, Dict[str, float]] = {g: {} for g in out_groups}
        for og in out_groups:
            for ig in in_groups:
                qty = sum(
                    u.yields[f].get(p, 0.0) * pyo.value(u._feeds[f])
                    for f in groups[ig]
                    if f in u._feeds
                    for p in groups[og]
                )
                out_map[og][ig] = qty

        # rich table
        t = _tbl()
        t.add_column("Yields")
        for ig in in_groups:
            t.add_column(_style_name(ig), justify="right")
        t.add_column("Total", justify="right")

        for og in out_groups:
            row = [_style_name(og)]
            row += [_fmt_qty(out_map[og][ig]) for ig in in_groups]
            row.append(_fmt_qty(sum(out_map[og].values())))
            t.add_row(*row)

        t.add_section()
        t.add_row(
            "Total",
            *(_fmt_qty(q_in[ig]) for ig in in_groups),
            _fmt_qty(sum(q_in.values())),
        )
        t.add_row(
            "Cost",
            *(_fmt_money(cost_in[ig], "cost") for ig in in_groups),
            _fmt_money(sum(cost_in.values()), "cost"),
        )

        con.print(_panel(t, f"Unit: {u.name}", "blue"))

    # -----------------------------------------------------------------
    # blending summary
    # -----------------------------------------------------------------
    blend_names = sorted(b.name for b in ref.blends)
    t = _tbl()
    t.add_column("Pool")
    for b in blend_names:
        t.add_column(_style_name(b), justify="right")

    # rows grouped by prefix
    for g, ns in groups.items():
        if not any(n in ref.pools.names for n in ns):
            continue
        row = [_style_name(g)]
        for b in blend_names:
            qty = pyo.value(sum(ref.blends[b]._feeds.get(n, 0) for n in ns))
            row.append(_fmt_qty(qty))
        t.add_row(*row)

    t.add_section()
    t.add_row(
        "Total", *(_fmt_qty(pyo.value(ref.blends[b]._feeds.sum())) for b in blend_names)
    )
    t.add_row(
        "Revenue",
        *(
            _fmt_money(
                ref.blends[b].price * pyo.value(ref.blends[b]._feeds.sum()), "rev"
            )
            for b in blend_names
        ),
    )

    con.print(_panel(t, "Blending", "magenta"))
