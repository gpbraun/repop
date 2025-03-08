"""
flowchart.py

Contém a função para gerar um fluxograma do processo de refinaria, utilizando Graphviz.
Inclui a função:
  - generate_flowchart

Gabriel Braun, 2025
"""

from graphviz import Digraph

from .models import RefineryModel


def generate_flowchart(
    model_data: RefineryModel,
    output_file: str = "process_flowchart",
    format: str = "pdf",
) -> None:
    """
    Gera um fluxograma do processo com orientação LEFT-RIGHT, organizado em camadas dinâmicas:
      - Coluna 0: Crudes (nível 0)
      - Coluna 2L-1: Unidades de processamento com nível L (L ≥ 1)
      - Coluna 2L: Commodities produzidas por unidades de nível L
      - Coluna final (2*L_max+1): Produtos de blending

    O nível de uma unidade é definido como:
      unit_level(u) = 1 + max({0 se o insumo é crude} ∪ {unit_level(v) para cada v que produz um insumo não-crude})
    O nível de uma commodity é o máximo dos níveis das unidades que a produzem.

    As arestas são desenhadas sem rótulos, com a cor EDGE_COLOR. O fluxograma é salvo
    no arquivo 'output_file' com o formato especificado.

    Args:
        model_data (RefineryModel): Dados do modelo de refinaria.
        output_file (str): Nome base do arquivo de saída (sem extensão).
        format (str): Formato do arquivo (ex: "pdf", "png").
    """
    CRUDE_COLOR = "#fb7185"
    POOL_COLOR = "#fde68a"
    BLEND_COLOR = "#a5b4fc"
    UNIT_COLOR = "#cbd5e1"
    EDGE_COLOR = "#1e293b"

    dot = Digraph(comment="Process Flowchart", format=format)
    dot.attr(rankdir="LR", splines="ortho", fontsize="12", fontname="Helvetica")

    # Determina os produtores de cada commodity
    commodity_producers = {}
    for u, unit in model_data.units.items():
        for inp, outs in unit.yields.items():
            for out in outs.keys():
                commodity_producers.setdefault(out, []).append(u)

    unit_levels = {}
    for u, unit in model_data.units.items():
        inputs = list(unit.yields.keys())
        if all(inp in model_data.crudes for inp in inputs):
            unit_levels[u] = 1
        else:
            unit_levels[u] = None

    changed = True
    while changed:
        changed = False
        for u, unit in model_data.units.items():
            if unit_levels[u] is None:
                levels = []
                can_compute = True
                for inp in unit.yields.keys():
                    if inp in model_data.crudes:
                        levels.append(0)
                    else:
                        producers = commodity_producers.get(inp, [])
                        known = [
                            unit_levels[v]
                            for v in producers
                            if unit_levels[v] is not None
                        ]
                        if known:
                            levels.append(max(known))
                        else:
                            can_compute = False
                            break
                if can_compute and levels:
                    unit_levels[u] = 1 + max(levels)
                    changed = True

    for u in unit_levels:
        if unit_levels[u] is None:
            unit_levels[u] = 1

    max_unit_level = max(unit_levels.values()) if unit_levels else 0

    commodity_levels = {}
    for c in model_data.streams.keys():
        if c in model_data.crudes:
            commodity_levels[c] = 0
        elif c in commodity_producers:
            commodity_levels[c] = max(
                unit_levels[u]
                for u in commodity_producers[c]
                if unit_levels[u] is not None
            )
        else:
            commodity_levels[c] = 0

    # Define as camadas
    layers = {}
    layers[0] = [("Crude_" + c, c) for c in model_data.crudes.keys()]
    for L in range(1, max_unit_level + 1):
        col_units = 2 * L - 1
        layers[col_units] = [
            ("Unit_" + u, u) for u in model_data.units.keys() if unit_levels[u] == L
        ]
        col_comm = 2 * L
        layers[col_comm] = [
            ("Commodity_" + c, c)
            for c in model_data.streams.keys()
            if commodity_levels.get(c, 0) == L
        ]
    final_col = 2 * max_unit_level + 1
    layers[final_col] = [("Blend_" + p, p) for p in model_data.blending.keys()]

    style_dict = {
        "Crude": {
            "shape": "box",
            "style": "filled,rounded",
            "fillcolor": CRUDE_COLOR,
            "fontname": "Helvetica",
            "fontsize": "12",
        },
        "Unit": {
            "shape": "box",
            "style": "filled,rounded",
            "fillcolor": UNIT_COLOR,
            "fontname": "Helvetica",
            "fontsize": "12",
            "height": "1.2",
        },
        "Commodity": {
            "shape": "ellipse",
            "style": "filled,rounded",
            "fillcolor": POOL_COLOR,
            "fontname": "Helvetica",
            "fontsize": "12",
        },
        "Blend": {
            "shape": "box",
            "style": "filled,rounded",
            "fillcolor": BLEND_COLOR,
            "fontname": "Helvetica",
            "fontsize": "12",
        },
    }

    for col, nodes in layers.items():
        with dot.subgraph(name=f"col_{col}") as sub:
            sub.attr(rank="same", style="invis")
            for node_id, label in nodes:
                if node_id.startswith("Crude_"):
                    attr = style_dict["Crude"]
                elif node_id.startswith("Unit_"):
                    attr = style_dict["Unit"]
                elif node_id.startswith("Commodity_"):
                    attr = style_dict["Commodity"]
                elif node_id.startswith("Blend_"):
                    attr = style_dict["Blend"]
                else:
                    attr = {}
                sub.node(node_id, label, **attr)

    edges_added = set()

    def add_edge(src: str, tgt: str):
        key = (src, tgt)
        if key not in edges_added:
            dot.edge(src, tgt, arrowsize="0.5", color=EDGE_COLOR)
            edges_added.add(key)

    for u, unit in model_data.units.items():
        for inp in unit.yields.keys():
            if inp in model_data.crudes:
                add_edge("Crude_" + inp, "Unit_" + u)
            else:
                add_edge("Commodity_" + inp, "Unit_" + u)

    for u, unit in model_data.units.items():
        for inp in unit.yields.keys():
            for out in unit.yields[inp].keys():
                if out in model_data.streams:
                    add_edge("Unit_" + u, "Commodity_" + out)

    for p, prod in model_data.blending.items():
        for comp in prod.components:
            add_edge("Commodity_" + comp, "Blend_" + p)

    dot.render(output_file, cleanup=True)
