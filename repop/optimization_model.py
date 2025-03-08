"""
optimization_model.py

Contém a função que constrói e resolve o modelo de otimização da refinaria utilizando Pyomo.
Inclui a função:
  - solve_refinery_model

Gabriel Braun, 2025
"""

from typing import Dict, List, Tuple

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
)

from .models import RefineryModel


def solve_refinery_model(
    model_data: RefineryModel,
) -> Tuple[ConcreteModel, Dict[str, List[str]]]:
    """
    Constrói e resolve o modelo de otimização da refinaria utilizando Pyomo.

    O processo consiste em:
      1. Definir os conjuntos (crudes, unidades, insumos, outputs, streams para blending e produtos).
      2. Criar as variáveis de decisão.
      3. Adicionar as restrições básicas (capacidade, disponibilidade de crude, balanço de matéria, blending, etc.).
      4. Definir a função objetivo (maximização do lucro).
      5. Resolver o modelo com um solver (Gurobi, por padrão).

    Args:
        model_data (RefineryModel): Objeto com os dados do modelo.

    Returns:
        Tuple[ConcreteModel, Dict[str, List[str]]]:
            - model: O modelo Pyomo resolvido.
            - I_dict: Dicionário mapeando cada unidade para a lista de insumos aceitos.
    """
    model = ConcreteModel()

    # 1) Definição de conjuntos
    model.Crudes = Set(initialize=list(model_data.crudes.keys()))
    U = list(model_data.units.keys())
    model.Units = Set(initialize=U)

    # Para cada unidade, os insumos disponíveis (chaves do yields)
    I_dict = {u: list(model_data.units[u].yields.keys()) for u in U}
    model.Inputs = Set(initialize=list({c for u in U for c in I_dict[u]}))

    # Conjunto de outputs produzidos pelas unidades
    output_commodities = set()
    for u in U:
        for c in I_dict[u]:
            output_commodities.update(model_data.units[u].yields[c].keys())
    model.Outputs = Set(initialize=list(output_commodities))

    # Conjunto de streams disponíveis para blending
    blend_streams = list(model_data.streams.keys())
    model.BlendStreams = Set(initialize=blend_streams)

    # Produtos finais de blending
    model.Products = Set(initialize=list(model_data.blending.keys()))

    # 2) Variáveis de decisão
    def x_index_rule(m):
        return ((u, c) for u in m.Units for c in I_dict[u])

    model.x_index = Set(initialize=x_index_rule, dimen=2)
    model.x = Var(model.x_index, domain=NonNegativeReals)
    model.y = Var(model.Products, model.BlendStreams, domain=NonNegativeReals)
    model.p_prod = Var(model.Products, domain=NonNegativeReals)
    model.blend_ratio_aux = Var(model.Products, domain=NonNegativeReals)

    # 3) Restrições básicas
    def capacity_rule(m, u):
        return sum(m.x[u, c] for c in I_dict[u]) <= model_data.units[u].capacity

    model.capacity_con = Constraint(model.Units, rule=capacity_rule)

    def raw_availability_rule(m, r):
        return (
            sum(m.x[u, r] for u in m.Units if r in I_dict[u])
            <= model_data.crudes[r].availability
        )

    model.raw_avail_con = Constraint(model.Crudes, rule=raw_availability_rule)

    # 4) Expressões de produção e disponibilidade
    def production_expr_rule(m, s):
        return sum(
            model_data.units[u].yields[c].get(s, 0.0) * m.x[u, c]
            for u in m.Units
            for c in I_dict[u]
            if s in model_data.units[u].yields[c]
        )

    model.Production = Expression(model.Outputs, rule=production_expr_rule)

    def avail_blend_rule(m, s):
        if s in m.Outputs:
            return m.Production[s]
        else:
            return 0

    model.Avail = Expression(model.BlendStreams, rule=avail_blend_rule)

    # 5) Restrições de blending
    def product_production_rule(m, p):
        comps = model_data.blending[p].components
        return m.p_prod[p] == sum(m.y[p, s] for s in comps)

    model.product_prod_con = Constraint(model.Products, rule=product_production_rule)

    def blend_allocation_rule(m, s):
        used_in = [p for p in m.Products if s in model_data.blending[p].components]
        if used_in:
            return sum(m.y[p, s] for p in used_in) <= m.Avail[s]
        else:
            return Constraint.Skip

    model.blend_alloc_con = Constraint(model.BlendStreams, rule=blend_allocation_rule)

    # 6) Balanço de matéria intermediária
    def intermediate_balance_rule(m, s):
        if s in model_data.crudes:
            return Constraint.Skip
        total_prod_s = m.Production[s] if s in m.Outputs else 0
        usage_in_units = sum(m.x[u, s] for u in m.Units if s in I_dict[u])
        usage_in_blends = sum(
            m.y[p, s] for p in m.Products if s in model_data.blending[p].components
        )
        return usage_in_units + usage_in_blends <= total_prod_s

    model.intermediate_balance_con = Constraint(
        model.BlendStreams, rule=intermediate_balance_rule
    )

    # 7) Restrições unificadas (qualidade, produção, relações)
    model.unified_constraints_con = ConstraintList()
    for p in model.Products:
        if model_data.blending[p].constraints is not None:
            comps = model_data.blending[p].components
            for cons in model_data.blending[p].constraints:
                if cons.type == "min_RON":
                    model.unified_constraints_con.add(
                        sum(
                            model.y[p, s] * model_data.streams[s].RON
                            for s in comps
                            if model_data.streams[s].RON is not None
                        )
                        >= cons.value * model.p_prod[p]
                    )
                elif cons.type == "max_vapor_pressure":
                    model.unified_constraints_con.add(
                        sum(
                            model.y[p, s] * model_data.streams[s].vapor_pressure
                            for s in comps
                            if model_data.streams[s].vapor_pressure is not None
                        )
                        <= cons.value * model.p_prod[p]
                    )
                elif cons.type == "max_sulfur":
                    model.unified_constraints_con.add(
                        sum(
                            model.y[p, s] * model_data.streams[s].sulfur
                            for s in comps
                            if model_data.streams[s].sulfur is not None
                        )
                        <= cons.value * model.p_prod[p]
                    )
                elif cons.type == "min_ratio":
                    model.unified_constraints_con.add(
                        model.p_prod[p] >= cons.value * model.p_prod[cons.reference]
                    )
                elif cons.type == "max_ratio":
                    model.unified_constraints_con.add(
                        model.p_prod[p] <= cons.value * model.p_prod[cons.reference]
                    )
                elif cons.type == "min production":
                    model.unified_constraints_con.add(model.p_prod[p] >= cons.value)
                elif cons.type == "max production":
                    model.unified_constraints_con.add(model.p_prod[p] <= cons.value)

    # 8) Restrições para blend_ratio fixo
    def blend_ratio_rule(m, p, s):
        br = model_data.blending[p].blend_ratio
        if br is not None and s in br:
            return m.y[p, s] == br[s] * m.blend_ratio_aux[p]
        else:
            return Constraint.Skip

    model.blend_ratio_con = Constraint(
        model.Products, model.BlendStreams, rule=blend_ratio_rule
    )

    def blend_ratio_total_rule(m, p):
        br = model_data.blending[p].blend_ratio
        if br is not None:
            return m.p_prod[p] == sum(br[s] * m.blend_ratio_aux[p] for s in br)
        else:
            return Constraint.Skip

    model.blend_ratio_total_con = Constraint(
        model.Products, rule=blend_ratio_total_rule
    )

    # 9) Função objetivo
    def objective_rule(m):
        revenue = sum(model_data.blending[p].price * m.p_prod[p] for p in m.Products)
        cost_processing = sum(
            model_data.units[u].cost * sum(m.x[u, c] for c in I_dict[u])
            for u in m.Units
        )
        cost_crude = sum(
            model_data.crudes[r].cost
            * sum(m.x[u, r] for u in m.Units if r in I_dict[u])
            for r in model_data.crudes
        )
        return revenue - (cost_processing + cost_crude)

    model.objective = Objective(rule=objective_rule, sense=maximize)

    # 10) Resolver o modelo
    solver = SolverFactory("gurobi")
    solver.solve(model, tee=False)

    return model, I_dict
