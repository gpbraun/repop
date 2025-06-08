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

import yaml

import repop
from repop.console import console, log_results


def main():
    """
    Função principal para processar os argumentos e executar as ações solicitadas.
    """
    parser = argparse.ArgumentParser(
        description="REPOP - Refinery Process Optimization Package"
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="YAML file with model data",
    )
    parser.add_argument(
        "-o",
        "--optimize",
        action="store_true",
        help="Optimize the process",
    )
    parser.add_argument(
        "-f",
        "--flowchart",
        action="store_true",
        help="Generate process flowchart",
    )
    args = parser.parse_args()

    # Carrega os dados do modelo a partir do arquivo YAML
    if args.input_file.suffix == ".yaml":
        data = yaml.safe_load(args.input_file.read_text())
    else:
        raise Exception(f"Formato de arquivo '{args.input_file.stem}' não suportado.")

    model_data = repop.RefineryModel.model_validate(data)

    # Executa a otimização, se solicitado
    if args.optimize:
        pyomo_model = model_data.optimize(solver_name="glpk")
        # repop.display_results(model, model_data, I_dict)

    log_results(pyomo_model, model_data, console)

    # Gera o fluxograma, se solicitado
    if args.flowchart:
        model_data.generate_flowchart(
            output_file=args.input_file.with_suffix(""),
            format="svg",
        )


if __name__ == "__main__":
    main()
