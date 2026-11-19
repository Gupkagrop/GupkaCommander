"""Пакет когнитивного ядра и взаимодействия с локальными моделями GupkaCommander."""

from __future__ import annotations

from core.brain.llm_client import SYSTEM_TOOLS, FallbackRuleEngine, LocalLLMClient

__all__ = ["FallbackRuleEngine", "LocalLLMClient", "SYSTEM_TOOLS"]
