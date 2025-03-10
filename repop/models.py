"""
models.py

Estruturas de dados para o modelo de refinaria utilizando Pydantic.

Gabriel Braun, 2025
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, model_validator


class Metadata(BaseModel):
    """
    Metadados gerais sobre o modelo de refino.

    Attributes:
        description (str): Descrição do modelo.
        version (str): Versão do modelo.
        last_updated (str): Data da última atualização.
        author (str): Autor ou responsável pelo modelo.
    """

    description: str
    version: str
    last_updated: str
    author: str


class Crude(BaseModel):
    """
    Representa um crude (petróleo bruto).

    Attributes:
        availability (float): Quantidade máxima disponível (ex: em barris).
        cost (float): Custo por unidade (ex: $/barril).
    """

    availability: float
    cost: float


class Stream(BaseModel):
    """
    Representa uma corrente (stream) de processo.

    Attributes:
        name (str): Nome da stream.
        RON (Optional[float]): Número de octanas, se aplicável.
        vapor_pressure (Optional[float]): Pressão de vapor, se aplicável.
        sulfur (Optional[float]): Teor de enxofre, se aplicável.
    """

    name: str
    RON: Optional[float] = None
    vapor_pressure: Optional[float] = None
    sulfur: Optional[float] = None


class Restriction(BaseModel):
    """
    Representa uma restrição de qualidade ou de produção no blending.

    Attributes:
        type (str): Tipo da restrição (ex: "min_RON", "max_vapor_pressure", "max_ratio", etc.).
        value (float): Valor numérico da restrição.
        reference (Optional[str]): Nome de outro produto para restrições de razão.
    """

    type: str  # Ex: "min_RON", "max_vapor_pressure", etc.
    value: float
    reference: Optional[str] = None


class ProcessingUnit(BaseModel):
    """
    Representa uma unidade de processamento na refinaria.

    Attributes:
        capacity (float): Capacidade máxima de processamento.
        cost (float): Custo de operação por unidade processada.
        yields (Dict[str, Dict[str, float]]): Dicionário definindo, para cada insumo, os produtos gerados e suas proporções.
    """

    capacity: float
    cost: float
    yields: Dict[str, Dict[str, float]]


class Blending(BaseModel):
    """
    Representa um produto final de blending.

    Attributes:
        price (float): Preço de venda do produto.
        components (List[str]): Lista de streams que compõem o blend.
        blend_ratio (Optional[Dict[str, float]]): Proporções fixas para cada componente, se definidas.
        constraints (Optional[List[Restriction]]): Restrições de qualidade/produção.
    """

    price: float
    components: List[str]
    blend_ratio: Optional[Dict[str, float]] = None
    constraints: Optional[List[Restriction]] = None


class RefineryModel(BaseModel):
    """
    Modelo principal que agrega todos os dados do processo de refino.

    Attributes:
        metadata (Metadata): Metadados do modelo.
        crudes (Dict[str, Crude]): Crudes disponíveis.
        units (Dict[str, ProcessingUnit]): Unidades de processamento.
        blending (Dict[str, Blending]): Produtos de blending.
        streams (Dict[str, Stream]): Streams do processo.
    """

    metadata: Metadata
    crudes: Dict[str, Crude]
    units: Dict[str, ProcessingUnit]
    blending: Dict[str, Blending]
    streams: Dict[str, Stream]

    @model_validator(mode="before")
    @classmethod
    def _validate_streams(cls, values: dict) -> dict:
        """
        Constroi o dicionário de `streams`.
        """
        # Descobre as streams a partir dos yields das unidades
        all_streams = set()
        for unit in values.get("units", {}).values():
            for yield_dict in unit.get("yields", {}).values():
                all_streams.update(yield_dict.keys())
        # Descobre as streams a partir dos componentes de blending
        for blend in values.get("blending", {}).values():
            all_streams.update(blend.get("components", []))

        # Obtém as propriedades definidas para as streams, se houver
        defined_stream_props = values.get("stream_properties", {})

        # Constrói o dicionário final de streams
        streams = {}
        for s in all_streams:
            stream_props = defined_stream_props.get(s, {})
            streams[s] = {"name": s, **stream_props}

        # Insere o dicionário de streams no objeto que será validado
        values["streams"] = streams
        return values
