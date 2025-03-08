"""
main.py

Interface de linha de comando para o REPOP - Refinery Planning and Optimization.

Este script:
  - Carrega o modelo de refinaria a partir de um arquivo YAML.
  - Otimiza o processo e exibe os resultados, se solicitado.
  - Gera o fluxograma do processo, se solicitado.

Uso:
  python main.py [-o] [-f] <arquivo_yaml>

Posicionais:
  arquivo_yaml         Arquivo YAML de entrada com os dados do modelo.

Opções:
  -o, --optimize       Executa a otimização do processo.
  -f, --flowchart      Gera o fluxograma do processo.

Gabriel Braun, 2025
"""

import argparse
from pathlib import Path

from repop import (
    display_results,
    generate_flowchart,
    load_refinery_model,
    solve_refinery_model,
)


def main():
    """Função principal para processar os argumentos e executar as ações solicitadas."""
    parser = argparse.ArgumentParser(
        description="REPOP - Refinery Process Optimization Package"
    )
    parser.add_argument("input_file", type=Path, help="YAML file with model data")
    parser.add_argument(
        "-o", "--optimize", action="store_true", help="Optimize the process"
    )
    parser.add_argument(
        "-f", "--flowchart", action="store_true", help="Generate process flowchart"
    )
    args = parser.parse_args()

    # Carrega os dados do modelo a partir do arquivo YAML
    model_data = load_refinery_model(args.input_file)

    # Executa a otimização, se solicitado
    if args.optimize:
        model, I_dict = solve_refinery_model(model_data)
        display_results(model, model_data, I_dict)

    # Gera o fluxograma, se solicitado
    if args.flowchart:
        generate_flowchart(model_data, output_file=args.input_file.stem, format="pdf")


if __name__ == "__main__":
    main()
