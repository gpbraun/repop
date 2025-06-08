"""
models.py

Modelagem e otimização de refinarias usando Pydantic v2 e Pyomo,
com método optimize() enxuto e de alta legibilidade.

Gabriel Braun, 2025
"""

from typing import Dict, Generic, Iterator, List, Optional, TypeVar

from pydantic import BaseModel, Field, RootModel, model_validator
from pyomo.environ import *

T = TypeVar("T")


class ModelList(RootModel[list[T]], Generic[T]):
    """
    Lista tipada que aceita indexação por índice ou por nome (`.name` do modelo),
    e fornece métodos auxiliares para uso em modelos.
    """

    def __iter__(self) -> Iterator[T]:
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    def __getitem__(self, idx: int | str) -> T:
        if isinstance(idx, str):
            for item in self.root:
                if getattr(item, "name", None) == idx:
                    return item
            raise KeyError(f"Item com nome '{idx}' não encontrado.")
        return self.root[idx]

    def names(self) -> List[str]:
        """Retorna lista de todos os nomes contidos nos modelos."""
        return [item.name for item in self.root]

    def mapping(self) -> Dict[str, T]:
        """Retorna dicionário name -> instância do modelo."""
        return {item.name: item for item in self.root}


class Metadata(BaseModel):
    description: str
    version: str
    last_updated: str
    author: str


class Crude(BaseModel):
    name: str
    availability: float
    cost: float
    quantity: float = 0.0


class Stream(BaseModel):
    name: str
    quantity: float = 0.0
    RON: float = 0.0
    vapor_pressure: float = 0.0
    sulphur: float = 0.0


class ProcessingUnit(BaseModel):
    name: str
    capacity: float
    cost: float
    yields: Dict[str, Dict[str, float]]  # Ex.: { "Crude1": { "LN":0.1, ... }}


class BlendingConstraint(BaseModel):
    constraint_type: str = Field(alias="type")
    value: float
    reference: Optional[str] = None


class Blending(BaseModel):
    name: str
    price: float
    components: List[str]
    blend_ratio: Optional[Dict[str, float]] = None
    constraints: Optional[List[BlendingConstraint]] = None


class RefineryModel(BaseModel):
    metadata: Metadata
    crudes: ModelList[Crude]
    units: ModelList[ProcessingUnit]
    blending: ModelList[Blending]
    streams: ModelList[Stream]

    class Config:
        extra = "ignore"

    @model_validator(mode="before")
    @classmethod
    def _prepare(cls, v: dict) -> dict:
        # Converte crudes, units e blending (dicts) em listas com 'name'
        v["crudes"] = [{"name": n, **d} for n, d in v.get("crudes", {}).items()]
        v["units"] = [{"name": n, **d} for n, d in v.get("units", {}).items()]
        v["blending"] = [{"name": n, **d} for n, d in v.get("blending", {}).items()]

        # Coleta todas as streams de yields e blending
        streams_set = set()
        for unit in v.get("units", []):
            for yield_map in unit.get("yields", {}).values():
                streams_set |= yield_map.keys()
        for blend in v.get("blending", []):
            streams_set |= set(blend.get("components", []))

        # Extrai e remove propriedades temporárias
        props = v.pop("stream_properties", {})

        # Cria lista de streams com propriedades
        v["streams"] = [
            {
                "name": s,
                "RON": props.get(s, {}).get("RON", 0.0),
                "vapor_pressure": props.get(s, {}).get("vapor_pressure", 0.0),
                "sulphur": props.get(s, {}).get("sulphur", 0.0),
            }
            for s in streams_set
        ]
        return v

    def optimize(self, solver_name: str = "gurobi") -> ConcreteModel:
        m = ConcreteModel()
        # Mapas auxiliares e listas de nomes
        crude_map = self.crudes.mapping()
        unit_map = self.units.mapping()
        blend_map = self.blending.mapping()
        stream_map = self.streams.mapping()

        crude_names = self.crudes.names()
        unit_names = self.units.names()
        product_names = self.blending.names()
        stream_names = self.streams.names()

        # Conjuntos
        m.Crudes = Set(initialize=crude_names)
        m.Units = Set(initialize=unit_names)
        m.Products = Set(initialize=product_names)
        m.Streams = Set(initialize=stream_names)

        # Alimentações por unidade
        feeds = {u: list(unit_map[u].yields) for u in unit_names}
        m.Inputs = Set(initialize={f for fs in feeds.values() for f in fs})

        # Saídas de unidades
        outputs = {s for u in unit_names for s in unit_map[u].yields for _ in (0,)}
        m.Outputs = Set(initialize=list(outputs))

        # Pré-cálculo: quais produtos cada stream alimenta
        stream_to_products: Dict[str, List[str]] = {s: [] for s in stream_names}
        for p in product_names:
            for s in blend_map[p].components:
                stream_to_products[s].append(p)

        # Variáveis
        idx = [(u, f) for u, fs in feeds.items() for f in fs]
        m.x = Var(Set(initialize=idx), domain=NonNegativeReals)
        m.y = Var(m.Products, m.Streams, domain=NonNegativeReals)
        m.p_prod = Var(m.Products, domain=NonNegativeReals)
        m.blend_aux = Var(m.Products, domain=NonNegativeReals)

        # Expressões
        m.Production = Expression(
            m.Outputs,
            rule=lambda _m, s: sum(
                unit_map[u].yields[f].get(s, 0.0) * _m.x[u, f]
                for u in unit_names
                for f in feeds[u]
                if s in unit_map[u].yields[f]
            ),
        )
        m.Avail = Expression(
            m.Streams, rule=lambda _m, s: _m.Production[s] if s in _m.Outputs else 0.0
        )

        # Restrições básicas
        m.Capacity = Constraint(
            m.Units,
            rule=lambda _m, u: sum(_m.x[u, f] for f in feeds[u])
            <= unit_map[u].capacity,
        )
        m.CrudeAvail = Constraint(
            m.Crudes,
            rule=lambda _m, r: sum(_m.x[u, r] for u in unit_names if r in feeds[u])
            <= crude_map[r].availability,
        )
        m.BlendAlloc = Constraint(
            m.Streams,
            rule=lambda _m, s: (
                Constraint.Skip
                if not (prods := stream_to_products[s])
                else sum(_m.y[p, s] for p in prods) <= _m.Avail[s]
            ),
        )
        m.ProdBalance = Constraint(
            m.Streams,
            rule=lambda _m, s: (
                Constraint.Skip
                if s in crude_map
                else sum(_m.x[u, s] for u in unit_names if s in feeds[u])
                + sum(_m.y[p, s] for p in stream_to_products[s])
                <= (_m.Production[s] if s in _m.Outputs else 0.0)
            ),
        )
        m.DefineProd = Constraint(
            m.Products,
            rule=lambda _m, p: _m.p_prod[p]
            == sum(_m.y[p, s] for s in blend_map[p].components),
        )

        # Qualidade e proporções
        qc = ConstraintList()
        for p in product_names:
            comps = blend_map[p].components
            for c in blend_map[p].constraints or []:
                if c.constraint_type == "min_RON":
                    qc.add(
                        sum(m.y[p, s] * stream_map[s].RON for s in comps)
                        >= c.value * m.p_prod[p]
                    )
                elif c.constraint_type == "max_vapor_pressure":
                    qc.add(
                        sum(m.y[p, s] * stream_map[s].vapor_pressure for s in comps)
                        <= c.value * m.p_prod[p]
                    )
                elif c.constraint_type == "max_sulphur":
                    qc.add(
                        sum(m.y[p, s] * stream_map[s].sulphur for s in comps)
                        <= c.value * m.p_prod[p]
                    )
                elif c.constraint_type == "min_ratio":
                    qc.add(m.p_prod[p] >= c.value * m.p_prod[c.reference])
                elif c.constraint_type == "max_ratio":
                    qc.add(m.p_prod[p] <= c.value * m.p_prod[c.reference])
                elif c.constraint_type == "min production":
                    qc.add(m.p_prod[p] >= c.value)
                elif c.constraint_type == "max production":
                    qc.add(m.p_prod[p] <= c.value)

        m.QualityConstraints = qc

        # Blend_ratio fixo
        m.BlendFix = Constraint(
            m.Products,
            m.Streams,
            rule=lambda _m, p, s: (
                Constraint.Skip
                if not (br := blend_map[p].blend_ratio) or s not in br
                else _m.y[p, s] == br[s] * _m.blend_aux[p]
            ),
        )
        m.BlendTotal = Constraint(
            m.Products,
            rule=lambda _m, p: (
                Constraint.Skip
                if not (br := blend_map[p].blend_ratio)
                else _m.p_prod[p] == sum(br[s] * _m.blend_aux[p] for s in br)
            ),
        )

        # Objetivo: receita - custos
        m.obj = Objective(
            rule=lambda _m: sum(blend_map[p].price * m.p_prod[p] for p in product_names)
            - sum(
                unit_map[u].cost * sum(_m.x[u, f] for f in feeds[u]) for u in unit_names
            )
            - sum(
                crude_map[r].cost * sum(_m.x[u, r] for u in unit_names if r in feeds[u])
                for r in crude_names
            ),
            sense=maximize,
        )

        SolverFactory(solver_name).solve(m, tee=False)

        # Atualiza quantidades
        for c in self.crudes:
            c.quantity = sum(
                m.x[u, c.name].value
                for u in unit_names
                if (u, c.name) in m.x.index_set()
            )
        for s in self.streams:
            s.quantity = value(m.Production[s.name]) if s.name in m.Outputs else 0.0

        return m
