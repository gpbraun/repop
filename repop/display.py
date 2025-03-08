"""
result_display.py

Contém a função para exibir os resultados do modelo de refinaria.
Inclui a função:
  - display_results

Gabriel Braun, 2025
"""

from typing import Dict, List

from pyomo.environ import ConcreteModel
from rich.console import Console
from rich.table import Table

from .models import RefineryModel


def display_results(
    model: ConcreteModel, model_data: RefineryModel, I_dict: Dict[str, List[str]]
) -> None:
    """
    Exibe os resultados do modelo resolvido utilizando tabelas formatadas pelo Rich.

    Args:
        model (ConcreteModel): Modelo Pyomo resolvido.
        model_data (RefineryModel): Dados do modelo de refinaria.
        I_dict (Dict[str, List[str]]): Dicionário que mapeia cada unidade aos insumos aceitos.
    """
    console = Console()
    table_width = 100

    # Métricas gerais
    total_vendas = sum(
        model_data.blending[p].price * model.p_prod[p].value for p in model.Products
    )
    total_gastos_producao = sum(
        model_data.units[u].cost * sum(model.x[u, c].value for c in I_dict[u])
        for u in model.Units
    )
    total_gastos_insumos = sum(
        model_data.crudes[r].cost
        * sum(model.x[u, r].value for u in model.Units if r in I_dict[u])
        for r in model_data.crudes
    )
    lucro_total = total_vendas - (total_gastos_producao + total_gastos_insumos)

    general_table = Table(title="Resultados Gerais", width=table_width)
    general_table.add_column("Métrica", style="cyan", justify="left")
    general_table.add_column("Valor", style="magenta", justify="right")
    general_table.add_row(
        "[bold]Total de vendas[/bold]", f"[bold]{total_vendas:.2f}[/bold]"
    )
    general_table.add_row(
        "[bold]Total de gastos de produção[/bold]",
        f"[bold]{total_gastos_producao:.2f}[/bold]",
    )
    general_table.add_row(
        "[bold]Total de gastos de insumos (crudes)[/bold]",
        f"[bold]{total_gastos_insumos:.2f}[/bold]",
    )
    general_table.add_row("[bold]Lucro total[/bold]", f"[bold]{lucro_total:.2f}[/bold]")
    console.print(general_table)

    # Exibição de crudes
    crude_table = Table(title="Crudes", width=table_width)
    crude_table.add_column("Crude", style="cyan")
    crude_table.add_column("Comprado (barrels)", style="green", justify="right")
    crude_table.add_column("Custo", style="magenta", justify="right")
    for r in model_data.crudes:
        total_comprado = sum(model.x[u, r].value for u in model.Units if r in I_dict[u])
        custo_r = model_data.crudes[r].cost * total_comprado
        crude_table.add_row(r, f"{total_comprado:.2f}", f"{custo_r:.2f}")
    console.print(crude_table)

    # Resumo das Unidades
    unit_summary = Table(
        title="Resumo das Unidades de Processamento", width=table_width
    )
    unit_summary.add_column("Unidade", style="cyan")
    unit_summary.add_column(
        "Total Processado (barrels)", style="green", justify="right"
    )
    unit_summary.add_column("Custo de Operação", style="magenta", justify="right")
    for u in model.Units:
        total_utilizado = sum(model.x[u, c].value for c in I_dict[u])
        custo_operacao = model_data.units[u].cost * total_utilizado
        unit_summary.add_row(u, f"{total_utilizado:.2f}", f"{custo_operacao:.2f}")
    console.print(unit_summary)

    # Detalhamento por Unidade
    for u in model.Units:
        unit_outputs = set()
        for c in I_dict[u]:
            unit_outputs.update(model_data.units[u].yields[c].keys())
        unit_outputs = sorted(list(unit_outputs))

        unit_table = Table(title=f"Detalhamento - Unidade {u}", width=table_width)
        unit_table.add_column("Insumo", style="cyan")
        unit_table.add_column("Processado (barrels)", style="green", justify="right")
        for s in unit_outputs:
            unit_table.add_column(
                f"Saída {s} (barrels)", style="magenta", justify="right"
            )

        total_unit_input = 0.0
        totals_outputs = {s: 0.0 for s in unit_outputs}
        for c in I_dict[u]:
            processed = model.x[u, c].value
            total_unit_input += processed
            row = [c, f"{processed:.2f}"]
            for s in unit_outputs:
                factor = model_data.units[u].yields[c].get(s, 0.0)
                produced = factor * processed
                totals_outputs[s] += produced
                row.append(f"{produced:.2f}")
            unit_table.add_row(*row)
        unit_table.add_section()
        total_row = ["[bold]Total[/bold]", f"[bold]{total_unit_input:.2f}[/bold]"]
        for s in unit_outputs:
            total_row.append(f"[bold]{totals_outputs[s]:.2f}[/bold]")
        unit_table.add_row(*total_row)
        console.print(unit_table)

    # Exibição dos produtos de blending
    for p in model.Products:
        blend_table = Table(title=f"Produto de Blending: {p}", width=table_width)
        blend_table.add_column("Insumo", style="cyan")
        blend_table.add_column("Quantidade (barrels)", style="green", justify="right")
        for s in model_data.blending[p].components:
            amt = model.y[p, s].value
            blend_table.add_row(s, f"{amt:.2f}")
        blend_table.add_section()
        total_blend = model.p_prod[p].value
        venda_blend = model_data.blending[p].price * total_blend
        blend_table.add_row(
            "[bold]Total Produzido[/bold]", f"[bold]{total_blend:.2f}[/bold]"
        )
        blend_table.add_row(
            "[bold]Preço Total de Venda[/bold]", f"[bold]{venda_blend:.2f}[/bold]"
        )
        console.print(blend_table)
