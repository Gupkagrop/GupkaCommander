"""Пакет долговременной памяти и базы данных ассистента."""

from __future__ import annotations

from core.memory.db import init_database
from core.memory.memory_manager import MemoryManager

__all__ = ["MemoryManager", "init_database"]
