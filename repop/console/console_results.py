"""
Console reporting for a solved REPOP Refinery
============================================

Call ``log_results(ref)`` after ``ref.optimize()`` and enjoy colourful
Rich tables in your terminal.
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


# ────────────────────────────────────────────────────────────────────
# basic formatting helpers
# ────────────────────────────────────────────────────────────────────
def _zero(v: float, prec: int) -> float:
    return 0.0 if round(v, prec) == 0 else v


def _qty(v: float, prec: int = 2) -> str:
    v = _zero(v, prec)
    txt = f"{v:,.{prec}f}"
    return f"[magenta]{txt}[/magenta]" if abs(v) > 1e-9 else f"[dim]{txt}[/dim]"


def _money(v: float, colour: str, sym: str = "$") -> str:
    v = _zero(v, 2)
    txt = f"{sym}{v:,.2f}"
    return f"[{colour}]{txt}[/{colour}]" if abs(v) > 1e-9 else f"[dim]{txt}[/dim]"


def _cyan(name: str) -> str:
    return f"[bold cyan]{name}[/bold cyan]"


# ────────────────────────────────────────────────────────────────────
# utilities
# ────────────────────────────────────────────────────────────────────
def group_by_prefix(names: List[str]) -> Dict[str, List[str]]:
    g: Dict[str, List[str]] = defaultdict(list)
    for n in sorted(names):
        g[n.split(".", 1)[0]].append(n)
    return g


def sum_map(varmap) -> float:
    """Shortcut for pyo.value(varmap.sum())."""
    return pyo.value(varmap.sum())


def panel(tbl: Table, title: str, colour: str) -> Panel:
    return Panel(
        tbl,
        title=f"[bold]{title.upper()}[/bold]",
        title_align="center",
        border_style=colour,
        box=box.ROUNDED,
        width=120,
        padding=(1, 2),
    )


def new_table() -> Table:
    return Table(
        box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style="white"
    )


# ────────────────────────────────────────────────────────────────────
# PUBLIC
# ────────────────────────────────────────────────────────────────────
def log_results(ref: Refinery, *, console: Console | None = None) -> None:
    """Pretty-print a solved refinery to the terminal."""
    if ref._model is None:
        raise RuntimeError("Refinery not solved; call `optimize()` first.")

    con = console or Console()
    m = ref._model

    groups = group_by_prefix([*ref.crudes.names, *ref.pools.names])

    # ─── overview ───────────────────────────────────────────────────
    sales = sum(bl.price * sum_map(bl._feeds) for bl in ref.blends)
    op_cost = sum(u.cost * sum_map(u._feeds) for u in ref.units)
    in_cost = sum(c.cost * sum_map(c._alloc) for c in ref.crudes)
    profit = sales - op_cost - in_cost

    t = new_table()
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    t.add_row("Sales", _money(sales, "green"))
    t.add_row("Op cost", _money(op_cost, "red"))
    t.add_row("In cost", _money(in_cost, "red"))
    t.add_section()
    t.add_row("Profit", _money(profit, "green"))
    con.print("\n", panel(t, "Overview", "green"))

    # ─── crudes ────────────────────────────────────────────────────
    t = new_table()
    t.add_column("Metric")
    for c in ref.crudes.names:
        t.add_column(_cyan(c), justify="right")

    crude_qty = [sum_map(ref.crudes[c]._alloc) for c in ref.crudes.names]
    crude_cost = [ref.crudes[c].cost * q for c, q in zip(ref.crudes.names, crude_qty)]
    t.add_section()
    t.add_row("Total", *(_qty(q) for q in crude_qty))
    t.add_row("Cost", *(_money(c, "red") for c in crude_cost))
    con.print(panel(t, "Crudes", "yellow"))

    # ─── units (ordered by level) ──────────────────────────────────
    for u in sorted(ref.units, key=lambda x: x.level):
        in_groups = [g for g in groups if any(f in groups[g] for f in u._feeds)]
        out_groups = [g for g in groups if any(p in groups[g] for p in u._exits)]

        # inbound quantities & costs (FIXED)
        q_in = {
            g: sum(pyo.value(u._feeds[f]) for f in groups[g] if f in u._feeds)
            for g in in_groups
        }
        cost = {g: u.cost * q for g, q in q_in.items()}

        # outbound matrix
        out = {og: {ig: 0.0 for ig in in_groups} for og in out_groups}
        for og in out_groups:
            for ig in in_groups:
                out[og][ig] = sum(
                    u.yields[f].get(p, 0.0) * pyo.value(u._feeds[f])
                    for f in groups[ig]
                    if f in u._feeds
                    for p in groups[og]
                )

        # render
        t = new_table()
        t.add_column("Yields")
        for ig in in_groups:
            t.add_column(_cyan(ig), justify="right")
        t.add_column("Total", justify="right")

        for og in out_groups:
            row = [_cyan(og)] + [_qty(out[og][ig]) for ig in in_groups]
            row.append(_qty(sum(out[og].values())))
            t.add_row(*row)

        t.add_section()
        t.add_row(
            "Total", *(_qty(q_in[ig]) for ig in in_groups), _qty(sum(q_in.values()))
        )
        t.add_row(
            "Cost",
            *(_money(cost[ig], "red") for ig in in_groups),
            _money(sum(cost.values()), "red"),
        )

        con.print(panel(t, f"Unit: {u.name}", "blue"))

    # ─── blending summary ──────────────────────────────────────────
    t = new_table()
    t.add_column("Pool")
    for b in ref.blends.names:
        t.add_column(_cyan(b), justify="right")

    for grp, ns in groups.items():
        if not any(n in ref.pools.names for n in ns):
            continue
        row = [_cyan(grp)]
        for b in ref.blends:
            qty = sum(pyo.value(b._feeds.get(n, 0)) for n in ns)
            row.append(_qty(qty))
        t.add_row(*row)

    t.add_section()
    t.add_row("Total", *(_qty(sum_map(b._feeds)) for b in ref.blends))
    t.add_row(
        "Revenue", *(_money(b.price * sum_map(b._feeds), "green") for b in ref.blends)
    )
    con.print(panel(t, "Blending", "magenta"))
