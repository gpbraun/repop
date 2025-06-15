"""
models.py

Modelagem e otimização de refinarias usando Pydantic v2 e Pyomo,
com método optimize() enxuto e de alta legibilidade.

Gabriel Braun, 2025
"""

from typing import Generic, Iterator, TypeVar

from pydantic import RootModel, computed_field
from pyomo.environ import *

T = TypeVar("T")


class ModelList(RootModel[list[T]], Generic[T]):
    """
    Lista tipada que aceita indexação por índice ou por nome (`.name` do modelo).
    """

    def __iter__(self) -> Iterator[T]:
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    def __contains__(self, item: T | str) -> bool:
        if isinstance(item, str):
            return item in self.names
        return item.name in self.names

    def __getitem__(self, idx: int | str) -> T:
        """
        Retorna: item
        """
        if isinstance(idx, str):
            for item in self.root:
                if getattr(item, "name", None) == idx:
                    return item
            raise KeyError(f"Item com nome '{idx}' não encontrado.")
        return self.root[idx]

    @computed_field
    @property
    def names(self) -> list[str]:
        """
        Retorna: lista de todos os nomes contidos nos modelos.
        """
        return [item.name for item in self.root]
