"""Phase 12 - AI Assistant: dogal dil -> Editor komut dispatch katmani."""

from .intent import CommandIntent, IntentAction, ParseResult
from .intent_parser import IntentParser
from .orchestrator import AssistantOrchestrator, AssistantResult, IntentExecution
from .dialogue import (
    BuildingRegistry,
    DialogueResult,
    DialogueSession,
    DialogueTurn,
    PendingClarification,
    UnknownBuildingError,
)
from .llm_bridge import (
    AnthropicLLMBridge,
    AnthropicLLMBridgeConfig,
    LLMBridgeError,
    make_mock_llm_fn,
)
from .llm_providers import (
    AnthropicConfig,
    AnthropicProvider,
    DEFAULT_INTENT_SYSTEM_PROMPT,
    GGUFConfig,
    GGUFProvider,
    LLMCallError,
    LLMProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderUnavailableError,
    create_provider_from_env,
    intent_llm_fn,
    make_fixed_provider,
)
from .report_narrator import narrate_facade_compliance, narrate_room_compliance

__all__ = [
    "CommandIntent",
    "IntentAction",
    "ParseResult",
    "IntentParser",
    "AssistantOrchestrator",
    "AssistantResult",
    "IntentExecution",
    "BuildingRegistry",
    "DialogueResult",
    "DialogueSession",
    "DialogueTurn",
    "PendingClarification",
    "UnknownBuildingError",
    "AnthropicLLMBridge",
    "AnthropicLLMBridgeConfig",
    "LLMBridgeError",
    "make_mock_llm_fn",
    "AnthropicConfig",
    "AnthropicProvider",
    "DEFAULT_INTENT_SYSTEM_PROMPT",
    "GGUFConfig",
    "GGUFProvider",
    "LLMCallError",
    "LLMProvider",
    "OpenAICompatibleConfig",
    "OpenAICompatibleProvider",
    "ProviderUnavailableError",
    "create_provider_from_env",
    "intent_llm_fn",
    "make_fixed_provider",
    "narrate_facade_compliance",
    "narrate_room_compliance",
]
