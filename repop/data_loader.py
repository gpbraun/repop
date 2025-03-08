"""
data_loader.py

Contém funções para ler um arquivo YAML e carregar os dados do modelo de refinaria.
Inclui as funções:
  - read_yaml_file
  - load_refinery_model

Gabriel Braun, 2025
"""

from pathlib import Path

import yaml

from .models import Blending, Crude, Metadata, ProcessingUnit, RefineryModel, Stream


def read_yaml_file(file_path: Path) -> dict:
    """
    Lê um arquivo YAML e retorna seu conteúdo como um dicionário.

    Args:
        file_path (str): Caminho do arquivo YAML.

    Returns:
        dict: Conteúdo do arquivo.
    """
    with file_path.open("r") as file:
        data = yaml.safe_load(file)
    return data


def load_refinery_model(file_path: Path) -> RefineryModel:
    """
    Carrega o modelo de refinaria a partir de um arquivo YAML.
    Descobre as streams automaticamente a partir dos yields das unidades e dos componentes dos blends.
    Se alguma stream não estiver definida em 'stream_properties', é criada com valores padrão.

    Args:
        file_path (str): Caminho do arquivo YAML.

    Returns:
        RefineryModel: Objeto com todos os dados validados.
    """
    data = read_yaml_file(file_path)
    metadata = Metadata(**data["metadata"])

    # Lê crudes e unidades
    crudes = {name: Crude(**info) for name, info in data.get("crudes", {}).items()}
    units = {
        name: ProcessingUnit(**info) for name, info in data.get("units", {}).items()
    }

    # Lê blends
    blending = {}
    for name, info in data.get("blending", {}).items():
        blending[name] = Blending(
            price=info["price"],
            components=info["components"],
            blend_ratio=info.get("blend_ratio"),
            constraints=info.get("constraints"),
        )

    # Descobre as streams a partir dos yields e dos componentes de blending
    all_streams = set()
    # A partir dos yields
    for unit_name, unit_data in data.get("units", {}).items():
        for _, yield_dict in unit_data["yields"].items():
            for out_stream in yield_dict.keys():
                all_streams.add(out_stream)
    # A partir dos componentes de blending
    for blend_name, blend_data in data.get("blending", {}).items():
        for comp in blend_data["components"]:
            all_streams.add(comp)

    # Carrega as propriedades definidas (se houver)
    defined_stream_props = data.get("stream_properties", {})

    # Cria o dicionário final de streams
    final_streams = {}
    for s in all_streams:
        if s in defined_stream_props:
            final_streams[s] = Stream(name=s, **defined_stream_props[s])
        else:
            final_streams[s] = Stream(name=s)

    return RefineryModel(
        metadata=metadata,
        crudes=crudes,
        units=units,
        blending=blending,
        streams=final_streams,
    )
