"""存储层：实体持久化抽象。"""
from aitester.storage.base import Repository
from aitester.storage.in_memory import InMemoryRepository

__all__ = ["Repository", "InMemoryRepository"]
