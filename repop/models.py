"""
models.py

Modelagem e otimização de refinarias usando Pydantic v2 e Pyomo,
com Pools, Blends (lista de dicts componente→razão), restrições de qualidade
customizáveis via propriedades genéricas e assinatura de funções clara.

Gabriel Braun, 2025
"""

from collections import defaultdict
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    Generic,
    Iterator,
    List,
    Optional,
    TypeVar,
)

from graphviz import Digraph
from pydantic import (
    BaseModel,
    Field,
    RootModel,
    computed_field,
    field_validator,
    model_validator,
)
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
    value,
)

CRUDE_COLOR = "#fb7185"
POOL_COLOR = "#fde68a"
BLEND_COLOR = "#a5b4fc"
UNIT_COLOR = "#cbd5e1"
EDGE_COLOR = "#1e293b"

STYLES = {
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

T = TypeVar("T")
# assinatura de função de blending:
# (blend_name,
#  constraint_props,
#  blend_qtys,      # mapa blend → Var de quantidade
#  pool_allocs,     # mapa pool → Var de alocação para este blend
#  pool_properties) # mapa pool → seu dict de propriedades
BlendFunc = Callable[
    [str, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, float]]],
    Any,
]


# ──────────────────────────────────────────────────────────────────────────────
# funções built-in de blend (exemplos)
# ──────────────────────────────────────────────────────────────────────────────
def min_ron(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    threshold = constraint_props["value"]
    return (
        sum(pool_allocs[p] * pool_properties[p].get("RON", 0.0) for p in pool_allocs)
        >= threshold * blend_qtys[blend_name]
    )


def max_rvp(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    threshold = constraint_props["value"]
    return (
        sum(pool_allocs[p] * pool_properties[p].get("RVP", 0.0) for p in pool_allocs)
        <= threshold * blend_qtys[blend_name]
    )


def max_sulphur(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    threshold = constraint_props["value"]
    return (
        sum(
            pool_allocs[p] * pool_properties[p].get("sulphur", 0.0) for p in pool_allocs
        )
        <= threshold * blend_qtys[blend_name]
    )


def min_ratio(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    factor = constraint_props["value"]
    ref = constraint_props["reference"]
    return blend_qtys[blend_name] >= factor * blend_qtys[ref]


def max_ratio(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    factor = constraint_props["value"]
    ref = constraint_props["reference"]
    return blend_qtys[blend_name] <= factor * blend_qtys[ref]


def min_production(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    return blend_qtys[blend_name] >= constraint_props["value"]


def max_production(
    blend_name: str,
    constraint_props: Dict[str, Any],
    blend_qtys: Dict[str, Any],
    pool_allocs: Dict[str, Any],
    pool_properties: Dict[str, Dict[str, float]],
):
    return blend_qtys[blend_name] <= constraint_props["value"]


# ──────────────────────────────────────────────────────────────────────────────
# ModelList: lista tipada com utilitários
# ──────────────────────────────────────────────────────────────────────────────
class ModelList(RootModel[list[T]], Generic[T]):
    def __iter__(self) -> Iterator[T]:
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    def __getitem__(self, idx: int | str) -> T:
        if isinstance(idx, str):
            return next(item for item in self.root if item.name == idx)
        return self.root[idx]

    def names(self) -> List[str]:
        return [item.name for item in self.root]


# ──────────────────────────────────────────────────────────────────────────────
# Blocos de dados
# ──────────────────────────────────────────────────────────────────────────────
class Metadata(BaseModel):
    description: str
    version: str
    last_updated: str
    author: str


class Crude(BaseModel):
    name: str
    availability: float
    cost: float = 0.0
    quantity: float = 0.0


class Pool(BaseModel):
    name: str
    quantity: float = 0.0
    level: int = 1
    properties: Dict[str, float] = {}


class ProcessingUnit(BaseModel):
    name: str
    capacity: float
    level: int = 1
    cost: float = 0.0
    yields: Dict[str, Dict[str, float]]

    @computed_field
    @property
    def feeds(self) -> List[str]:
        return list(self.yields.keys())


class BlendingConstraint(BaseModel):
    name: str = Field(alias="type")
    properties: Dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _assemble_properties(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        data["properties"] = {
            k: v for k, v in data.items() if k not in ("name", "type")
        }
        return data


class Blend(BaseModel):
    name: str
    price: float
    components: List[str]
    blend_ratios: Optional[List[Dict[str, float]]] = None
    constraints: Optional[List[BlendingConstraint]] = None
    quantity: float = 0.0

    @field_validator("blend_ratios", mode="before")
    @classmethod
    def _validate_ratios(cls, v: Any, info) -> Optional[List[Dict[str, float]]]:
        comps = info.data.get("components", [])
        if v is None:
            return None
        if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
            raise ValueError("blend_ratios deve ser lista de dicts componente→razão")
        normalized: List[Dict[str, float]] = []
        for row in v:
            entry: Dict[str, float] = {}
            for comp, val in row.items():
                if comp not in comps:
                    raise ValueError(f"Componente '{comp}' não está em {comps}")
                if not isinstance(val, (int, float)) or val <= 0:
                    continue
                entry[comp] = float(val)
            if not entry:
                raise ValueError(
                    "Cada dict de blend_ratios precisa de pelo menos uma razão > 0"
                )
            normalized.append(entry)
        return normalized


# ──────────────────────────────────────────────────────────────────────────────
# RefineryModel
# ──────────────────────────────────────────────────────────────────────────────
class RefineryModel(BaseModel):
    metadata: Metadata
    crudes: ModelList[Crude]
    units: ModelList[ProcessingUnit]
    blends: ModelList[Blend]
    pools: ModelList[Pool]

    round_decimals: ClassVar[int] = 6

    _blend_constraints: ClassVar[Dict[str, BlendFunc]] = {
        "min_ron": min_ron,
        "max_rvp": max_rvp,
        "max_sulphur": max_sulphur,
        "min_ratio": min_ratio,
        "max_ratio": max_ratio,
        "min_production": min_production,
        "max_production": max_production,
    }

    class Config:
        extra = "ignore"

    @classmethod
    def register_blend_constraint(cls, name: str, func: BlendFunc) -> None:
        key = name.strip().replace(" ", "_").lower()
        cls._blend_constraints[key] = func

    @model_validator(mode="before")
    @classmethod
    def _prepare(cls, values: dict) -> dict:
        values["crudes"] = [
            {"name": n, **d} for n, d in values.get("crudes", {}).items()
        ]
        values["units"] = [{"name": n, **d} for n, d in values.get("units", {}).items()]
        raw_b = values.get("blending", {})
        values["blends"] = [{"name": n, **d} for n, d in raw_b.items()]

        pool_set: set[str] = set()
        for u in values["units"]:
            for fm in u.get("yields", {}).values():
                pool_set |= fm.keys()
        for b in raw_b.values():
            pool_set |= set(b.get("components", []))

        props = values.pop("stream_properties", {})
        values["pools"] = [
            {"name": p, "properties": props.get(p, {})} for p in sorted(pool_set)
        ]
        return values

    @model_validator(mode="after")
    def _assign_levels(self) -> "RefineryModel":
        """
        Atribui `.level` em ProcessingUnit e Pool de forma topológica:
          - Units: nível 1 se só consomem crudes; senão 1 + max(nível dos produtores)
          - Pools: nível 0 se é crude; senão max(nível das units que o produzem)
        """
        # 1) mapeia cada pool às units que o produzem
        producers = defaultdict(list)
        for unit in self.units:
            for out_map in unit.yields.values():
                for pool in out_map:
                    producers[pool].append(unit.name)

        crude_set = set(self.crudes.names())
        levels: Dict[str, int] = {}

        # 2) inicializa níveis das units que só consomem crudes
        for unit in self.units:
            if all(feed in crude_set for feed in unit.feeds):
                levels[unit.name] = 1

        # 3) itera topologicamente até atribuir todas
        total = len(self.units)
        while len(levels) < total:
            progress = False
            for unit in self.units:
                name = unit.name
                if name in levels:
                    continue
                # tenta computar nível atual
                lvls = []
                for feed in unit.feeds:
                    if feed in crude_set:
                        lvls.append(0)
                    else:
                        parents = producers.get(feed, [])
                        if any(parent not in levels for parent in parents):
                            break
                        lvls.append(max(levels[parent] for parent in parents))
                else:
                    # todos os feeds têm nível
                    levels[name] = 1 + (max(lvls) if lvls else 0)
                    progress = True
            if not progress:
                # fallback: unidades restantes viram nível 1
                for unit in self.units:
                    levels.setdefault(unit.name, 1)
                break

        # 4) aplica níveis nas units
        for unit in self.units:
            unit.level = levels[unit.name]

        # 5) calcula níveis de pools
        for pool in self.pools:
            if pool.name in crude_set:
                pool.level = 0
            else:
                parents = producers.get(pool.name, [])
                pool.level = max(levels.get(parent, 0) for parent in parents)

        return self

    def optimize(self, solver_name: str = "gurobi") -> ConcreteModel:
        mdl = ConcreteModel()
        crudes = self.crudes.names()
        units = self.units.names()
        blends = self.blends.names()
        pools = self.pools.names()

        # conjuntos
        mdl.Crudes = Set(initialize=sorted(crudes))
        mdl.Units = Set(initialize=sorted(units))
        mdl.Blends = Set(initialize=sorted(blends))
        mdl.Pools = Set(initialize=sorted(pools))

        # índices de feed
        idx = [(u, f) for u in units for f in self.units[u].feeds]
        mdl.feed_qty = Var(Set(initialize=idx), domain=NonNegativeReals)
        mdl.allocate = Var(mdl.Blends, mdl.Pools, domain=NonNegativeReals)
        mdl.blend_qty = Var(mdl.Blends, domain=NonNegativeReals)

        # produção e disponibilidade
        mdl.Production = Expression(
            mdl.Pools,
            rule=lambda m, p: sum(
                self.units[u].yields[f].get(p, 0.0) * m.feed_qty[u, f]
                for u in units
                for f in self.units[u].feeds
                if p in self.units[u].yields[f]
            ),
        )
        mdl.Availability = Expression(mdl.Pools, rule=lambda m, p: m.Production[p])

        # pool→blends
        pool_to_blends = {
            p: [b for b in blends if p in self.blends[b].components] for p in pools
        }

        # restrições básicas
        mdl.UnitCapacity = Constraint(
            mdl.Units,
            rule=lambda m, u: sum(m.feed_qty[u, f] for f in self.units[u].feeds)
            <= self.units[u].capacity,
        )
        mdl.CrudeLimit = Constraint(
            mdl.Crudes,
            rule=lambda m, c: sum(
                m.feed_qty[u, c] for u in units if c in self.units[u].feeds
            )
            <= self.crudes[c].availability,
        )
        mdl.BlendDef = Constraint(
            mdl.Blends,
            rule=lambda m, b: m.blend_qty[b]
            == sum(m.allocate[b, p] for p in self.blends[b].components),
        )
        mdl.PoolAlloc = Constraint(
            mdl.Pools,
            rule=lambda m, p: sum(m.allocate[b, p] for b in pool_to_blends[p])
            <= m.Availability[p],
        )
        mdl.PoolBalance = Constraint(
            mdl.Pools,
            rule=lambda m, p: (
                sum(m.feed_qty[u, p] for u in units if p in self.units[u].feeds)
                + sum(m.allocate[b, p] for b in pool_to_blends[p])
                <= m.Production[p]
            ),
        )

        # qualidade customizável
        mdl.Quality = ConstraintList()
        blend_qtys_map = {b: mdl.blend_qty[b] for b in blends}
        for b in blends:
            pool_allocs = {p: mdl.allocate[b, p] for p in self.blends[b].components}
            pool_props = {
                p: self.pools[p].properties for p in self.blends[b].components
            }
            for cons in self.blends[b].constraints or []:
                key = cons.name.strip().replace(" ", "_").lower()
                fn = self._blend_constraints.get(key)
                if fn is None:
                    raise KeyError(f"Blend constraint '{cons.name}' não registrada")
                mdl.Quality.add(
                    fn(b, cons.properties, blend_qtys_map, pool_allocs, pool_props)
                )

        # blend_ratios
        ratio_map = {
            (b, k, p): r
            for b in blends
            for k, row in enumerate(self.blends[b].blend_ratios or [])
            for p, r in row.items()
        }
        ratio_keys = sorted({(b, k) for (b, k, _) in ratio_map})
        triplets = sorted(ratio_map)
        mdl.ratio_aux = Var(Set(initialize=ratio_keys), domain=NonNegativeReals)
        mdl.RatioFix = Constraint(
            Set(initialize=triplets),
            rule=lambda m, b, k, p: m.allocate[b, p]
            == ratio_map[(b, k, p)] * m.ratio_aux[b, k],
        )

        # objetivo
        mdl.Profit = Objective(
            rule=lambda m: (
                sum(self.blends[b].price * m.blend_qty[b] for b in blends)
                - sum(
                    self.units[u].cost
                    * sum(m.feed_qty[u, f] for f in self.units[u].feeds)
                    for u in units
                )
                - sum(
                    self.crudes[c].cost
                    * sum(m.feed_qty[u, c] for u in units if c in self.units[u].feeds)
                    for c in crudes
                )
            ),
            sense=maximize,
        )

        SolverFactory(solver_name).solve(mdl, tee=False)

        # atribuir resultados com rounding
        prec = self.round_decimals
        for c in crudes:
            qty = sum(
                float(value(mdl.feed_qty[u, c]))
                for u in units
                if (u, c) in mdl.feed_qty.index_set()
            )
            self.crudes[c].quantity = round(qty, prec)
        for b in blends:
            self.blends[b].quantity = round(float(value(mdl.blend_qty[b])), prec)
        for p in pools:
            prod = float(value(mdl.Production[p]))
            self.pools[p].quantity = round(prod, prec)

        return mdl

    def generate_flowchart(
        self,
        output_file: str = "process_flowchart",
        format: str = "pdf",
    ) -> None:
        """
        Gera um fluxograma LEFT→RIGHT para este RefineryModel, agregando pools
        com mesmo prefixo (antes do '.') em único nó, usando STYLES e EDGE_COLOR globais.
        """
        dot = Digraph(comment="Process Flowchart", format=format)
        dot.attr(rankdir="LR", splines="ortho", fontsize="10", fontname="Helvetica")

        # 1) Nomes de referência
        crude_names = self.crudes.names()
        blend_names = self.blends.names()

        # 2) Agrupar pools por prefixo antes de '.'
        pool_group = {
            p.name: p.name.split(".", 1)[0] if "." in p.name else p.name
            for p in self.pools
        }

        # 3) Montar camadas: defaultdict de listas de (id, label, estilo)
        layers = defaultdict(list)
        # camada 0: crudes
        for c in sorted(crude_names):
            layers[0].append((f"Crude_{c}", c, "Crude"))

        max_lvl = max((u.level for u in self.units), default=0)
        for lvl in range(1, max_lvl + 1):
            # unidades de nível `lvl`
            for u in sorted(self.units, key=lambda x: x.name):
                if u.level == lvl:
                    layers[2 * lvl - 1].append((f"Unit_{u.name}", u.name, "Unit"))
            # pools de nível `lvl`, unidos por prefixo
            grp_names = {pool_group[p.name] for p in self.pools if p.level == lvl}
            for g in sorted(grp_names):
                layers[2 * lvl].append((f"Pool_{g}", g, "Pool"))

        # coluna final: blends
        for b in sorted(blend_names):
            layers[2 * max_lvl + 1].append((f"Blend_{b}", b, "Blend"))

        # 4) Desenhar nós por camada
        for col, nodes in sorted(layers.items()):
            with dot.subgraph(name=f"col_{col}") as sg:
                sg.attr(rank="same", style="invis")
                for node_id, label, kind in nodes:
                    sg.node(node_id, label, **STYLES[kind])

        # 5) Arestas únicas
        seen = set()

        def add_edge(src: str, dst: str):
            if (src, dst) not in seen:
                dot.edge(src, dst, arrowsize="0.5", color=EDGE_COLOR)
                seen.add((src, dst))

        # 6) Crude/Pool → Unit
        for u in self.units:
            for feed in u.feeds:
                src_kind = "Crude" if feed in crude_names else "Pool"
                src = f"{src_kind}_{feed if feed in crude_names else pool_group[feed]}"
                add_edge(src, f"Unit_{u.name}")

        # 7) Unit → Pool
        for u in self.units:
            for outs in u.yields.values():
                for p in outs:
                    add_edge(f"Unit_{u.name}", f"Pool_{pool_group[p]}")

        # 8) Pool → Blend
        for b in self.blends:
            for comp in b.components:
                add_edge(f"Pool_{pool_group[comp]}", f"Blend_{b.name}")

        dot.render(output_file, cleanup=True)
