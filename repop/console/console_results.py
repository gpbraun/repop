from collections import defaultdict

from pyomo.environ import ConcreteModel, value
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from repop.models import Refinery


def _normalize_zero(v: float, prec: int) -> float:
    return 0.0 if round(v, prec) == 0 else v


def _fmt_quantity(v: float, prec: int = 2) -> str:
    v = _normalize_zero(v, prec)
    txt = f"{v:,.{prec}f}"
    return f"[magenta]{txt}[/magenta]" if abs(v) > 1e-9 else f"[dim]{txt}[/dim]"


def _fmt_money(v: float, kind: str = "normal", symbol: str = "$") -> str:
    v = _normalize_zero(v, 2)
    txt = f"{symbol}{v:,.2f}"
    if abs(v) < 1e-9:
        return f"[dim]{txt}[/dim]"
    color = {"rev": "green", "cost": "red"}.get(kind, "magenta")
    return f"[{color}]{txt}[/{color}]"


def style_name(n: str) -> str:
    return f"[bold cyan]{n}[/bold cyan]"


def log_results(model: ConcreteModel, refinery: Refinery, console: Console) -> None:
    """
    Display refinery results in English:
    - Panel width 150, titles centered uppercase bold
    - Tables with header & total lines only
    - Table headers in Title Case
    - Names bold cyan; quantities magenta; costs red; revenues green
    - Units ordered by level; all tables transposed
    """
    WIDTH, PADDING = 120, (1, 2)
    header_style = "white"

    # lookups
    crudes = {c.name: c for c in refinery.crudes}
    units = {u.name: u for u in refinery.units}
    blends = {b.name: b for b in refinery.blends}

    console.print()

    # group by prefix
    groups = defaultdict(list)
    for nm in sorted(refinery.crudes.names() + refinery.pools.names()):
        prefix = nm.split(".", 1)[0]
        groups[prefix].append(nm)

    def make_panel(tbl: Table, title: str, color: str) -> Panel:
        return Panel(
            tbl,
            title=f"[bold]{title.upper()}[/bold]",
            title_align="center",
            box=box.ROUNDED,
            border_style=color,
            width=WIDTH,
            padding=PADDING,
        )

    def new_table() -> Table:
        return Table(
            box=box.SIMPLE_HEAD, show_edge=False, expand=True, header_style=header_style
        )

    # ─── Overview ────────────────────────────────────────────────────────────────
    sales = sum(bl.price * value(model.blend_qty[b]) for b, bl in blends.items())
    op_cost = sum(
        u.cost * sum(value(model.feed_qty[u.name, f]) for f in u.feeds)
        for u in refinery.units
    )
    in_cost = sum(
        cd.cost
        * sum(
            value(model.feed_qty[u, cd.name])
            for u in model.Units
            if cd.name in units[u].feeds
        )
        for cd in refinery.crudes
    )
    profit = sales - op_cost - in_cost

    tbl = new_table()
    tbl.add_column("Metric", justify="left")
    tbl.add_column("Value", justify="right")
    tbl.add_row("Sales", _fmt_money(sales, "rev"))
    tbl.add_row("Op cost", _fmt_money(op_cost, "cost"))
    tbl.add_row("In cost", _fmt_money(in_cost, "cost"))
    tbl.add_section()
    tbl.add_row("Profit", _fmt_money(profit, "rev"))
    console.print(make_panel(tbl, "Overview", "green"))

    # ─── Crudes ──────────────────────────────────────────────────────────────────
    crude_list = sorted(model.Crudes)
    tbl = new_table()
    tbl.add_column("Metric", justify="left")
    for c in crude_list:
        tbl.add_column(style_name(c), justify="right")
    tbl.add_section()
    qtys = [
        sum(value(model.feed_qty[u, c]) for u in model.Units if c in units[u].feeds)
        for c in crude_list
    ]
    tbl.add_row("Total", *[_fmt_quantity(q) for q in qtys])
    costs = [crudes[c].cost * qtys[i] for i, c in enumerate(crude_list)]
    tbl.add_row("Cost", *[_fmt_money(cst, "cost") for cst in costs])
    console.print(make_panel(tbl, "Crudes", "yellow"))

    # ─── Unit details ────────────────────────────────────────────────────────────
    for u in sorted(model.Units, key=lambda x: units[x].level):
        unit = units[u]
        in_ps = sorted(
            p for p, ns in groups.items() if any(f in ns for f in unit.feeds)
        )
        out_ps = sorted(
            p
            for p, ns in groups.items()
            if any(o in ns for f in unit.feeds for o in unit.yields[f])
        )

        # compute maps
        q_in, cost_in = {}, {}
        out_map = {op: {} for op in out_ps}
        for p in in_ps:
            feeds = [f for f in groups[p] if f in unit.feeds]
            q = sum(value(model.feed_qty[u, f]) for f in feeds)
            q_in[p], cost_in[p] = q, unit.cost * q
            for op in out_ps:
                out_qty = sum(
                    unit.yields[f].get(o, 0.0) * value(model.feed_qty[u, f])
                    for f in feeds
                    for o in unit.yields[f]
                    if o in groups[op]
                )
                out_map[op][p] = out_qty

        total_q = sum(q_in.values())
        total_c = sum(cost_in.values())
        total_out = {op: sum(vals.values()) for op, vals in out_map.items()}

        tbl = new_table()
        tbl.add_column("Yields", justify="left")
        for p in in_ps:
            tbl.add_column(style_name(p), justify="right")
        tbl.add_column("Total", justify="right")
        tbl.add_section()
        for op in out_ps:
            tbl.add_row(
                style_name(op),
                *[_fmt_quantity(out_map[op][p]) for p in in_ps],
                _fmt_quantity(total_out[op]),
            )
        tbl.add_section()
        tbl.add_row(
            "Total", *[_fmt_quantity(q_in[p]) for p in in_ps], _fmt_quantity(total_q)
        )
        tbl.add_row(
            "Cost",
            *[_fmt_money(cost_in[p], "cost") for p in in_ps],
            _fmt_money(total_c, "cost"),
        )

        console.print(make_panel(tbl, f"Unit: {u}", "blue"))

    # ─── Blending summary ────────────────────────────────────────────────────────
    blend_list = sorted(model.Blends)
    tbl = new_table()
    tbl.add_column("Pool", justify="left")
    for b in blend_list:
        tbl.add_column(style_name(b), justify="right")
    tbl.add_section()
    for grp, ns in groups.items():
        if not any(n in refinery.pools.names() for n in ns):
            continue
        tbl.add_row(
            style_name(grp),
            *[
                _fmt_quantity(
                    sum(
                        value(model.allocate[b, n])
                        for n in ns
                        if n in blends[b].components
                    )
                )
                for b in blend_list
            ],
        )
    tbl.add_section()
    tbl.add_row(
        "Total", *[_fmt_quantity(value(model.blend_qty[b])) for b in blend_list]
    )
    tbl.add_row(
        "Revenue",
        *[
            _fmt_money(blends[b].price * value(model.blend_qty[b]), "rev")
            for b in blend_list
        ],
    )
    console.print(make_panel(tbl, "Blending", "magenta"))
