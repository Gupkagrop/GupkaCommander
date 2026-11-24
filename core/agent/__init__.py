"""Пакет автономного ReAct-агента GupkaCommander 2.0 (ToolRegistry, StepVerifier, ReActEngine)."""

from __future__ import annotations

from core.agent.react_loop import AgentStepEvent, ReActEngine, ReActRunResult, StepStatus
from core.agent.step_verifier import StateSnapshot, StepVerifier, VerificationResult
from core.agent.tool_registry import (
    AGENT_TOOL_SCHEMAS,
    MAX_OUTPUT_CHARS,
    EngineType,
    ToolExecutionResult,
    ToolRegistry,
    truncate_output,
)

__all__ = [
    "AGENT_TOOL_SCHEMAS",
    "AgentStepEvent",
    "EngineType",
    "MAX_OUTPUT_CHARS",
    "ReActEngine",
    "ReActRunResult",
    "StateSnapshot",
    "StepStatus",
    "StepVerifier",
    "ToolExecutionResult",
    "ToolRegistry",
    "VerificationResult",
    "truncate_output",
]
