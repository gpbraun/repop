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

from .models import RefineryModel


def load_refinery_model(file_path: Path) -> RefineryModel:
    """
    Carrega o modelo de refinaria a partir de um arquivo YAML utilizando as funcionalidades do Pydantic.
    Além disso, descobre as streams automaticamente a partir dos yields das unidades e dos componentes dos blends.
    Se alguma stream não estiver definida em 'stream_properties', é criada com valores padrão.

    Args:
        file_path (Path): Caminho do arquivo YAML.

    Returns:
        RefineryModel: Objeto com todos os dados validados.
    """
    data = yaml.safe_load(file_path.read_text())

    # Descobre as streams a partir dos yields das unidades e dos componentes dos blends
    all_streams = set()
    for unit_data in data.get("units", {}).values():
        for yield_dict in unit_data.get("yields", {}).values():
            all_streams.update(yield_dict.keys())
    for blend_data in data.get("blending", {}).values():
        all_streams.update(blend_data["components"])

    # Obtém as propriedades definidas para as streams, se houver
    defined_stream_props = data.get("stream_properties", {})

    # Cria o dicionário final de streams
    streams = {}
    for s in all_streams:
        # Se houver propriedades definidas, incorpora-as; caso contrário, usa somente o nome
        streams[s] = {"name": s, **defined_stream_props.get(s, {})}

    # Insere o dicionário de streams no objeto principal
    data["streams"] = streams

    # Converte o dicionário para o modelo RefineryModel usando o método parse_obj do Pydantic
    return RefineryModel.model_validate(data)
