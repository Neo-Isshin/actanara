"""Approved LLM provider presets for Open Nova runtime settings.

The catalog is seeded from OpenClaw 2026.6.1 onboard/provider metadata, then
scrubbed into a Nova-owned static library. It intentionally contains endpoint
and model metadata only; operator credentials remain in Nova settings/env.
"""

from __future__ import annotations

import copy
import re
from typing import Any

CUSTOM_PROVIDER_ID = "custom"
OPENCLAW_SOURCE = "openclaw-2026.6.1-static"
SUPPORTED_APIS = {"openai-compatible", "anthropic-messages"}
DEFAULT_PIPELINE_CONCURRENCY = 3
DEFAULT_PIPELINE_GATE_TOKENS = 30000
DEFAULT_LLM_TIMEOUT_SECONDS = 300
MAX_AUTO_PIPELINE_GATE_TOKENS = 80000
AUTO_PIPELINE_GATE_RATIO = 0.15
PIPELINE_GATE_MODE_AUTO = "auto"
PIPELINE_GATE_MODE_MANUAL = "manual"
PIPELINE_GATE_MODES = {PIPELINE_GATE_MODE_AUTO, PIPELINE_GATE_MODE_MANUAL}
ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OPENCLAW_ONBOARD_PROVIDER_IDS = {
    "openai",
    "anthropic",
    "xai",
    "google",
    "arcee",
    "brave",
    "byteplus",
    "cerebras",
    "chutes",
    "cloudflare-ai-gateway",
    "codex",
    "copilot",
    "custom",
    "deepinfra",
    "deepseek",
    "fireworks",
    "gmi",
    "google-vertex",
    "groq",
    "huggingface",
    "kilocode",
    "litellm",
    "lmstudio",
    "microsoft-foundry",
    "minimax",
    "mistral",
    "moonshot",
    "novita",
    "nvidia",
    "ollama",
    "opencode",
    "openrouter",
    "qianfan",
    "qwen",
    "sglang",
    "stepfun",
    "synthetic",
    "tencent",
    "together",
    "venice",
    "vercel-ai-gateway",
    "vllm",
    "volcengine",
    "xiaomi",
    "zai",
}


def _model(
    model_id: str,
    name: str | None = None,
    context_window: int | None = None,
    max_tokens: int | None = None,
    *,
    reasoning: bool | None = None,
) -> dict[str, Any]:
    model: dict[str, Any] = {"id": model_id, "name": name or model_id}
    if context_window:
        model["contextWindow"] = context_window
    if max_tokens:
        model["maxTokens"] = max_tokens
    if reasoning is not None:
        model["reasoning"] = reasoning
    return model


def auto_pipeline_gate_tokens(context_window: Any, fallback: int = DEFAULT_PIPELINE_GATE_TOKENS) -> int:
    """Return the default quality gate for a model context window."""
    try:
        parsed = int(context_window)
    except (TypeError, ValueError):
        parsed = 0
    if parsed <= 0:
        return fallback
    return max(1000, min(int(parsed * AUTO_PIPELINE_GATE_RATIO), MAX_AUTO_PIPELINE_GATE_TOKENS))


def _provider(
    provider_id: str,
    name: str,
    *,
    api: str = "openai-compatible",
    endpoint: str = "",
    auth: str = "bearer",
    models: list[dict[str, Any]] | None = None,
    status: str = "supported",
    source_api: str | None = None,
    note: str = "",
    source: str = OPENCLAW_SOURCE,
) -> dict[str, Any]:
    enabled = status == "supported" and bool(endpoint) and api in SUPPORTED_APIS and bool(models)
    return {
        "id": provider_id,
        "name": name,
        "api": api,
        "endpoint": endpoint,
        "auth": auth,
        "models": models or [],
        "enabled": enabled,
        "status": status if not enabled else "supported",
        "source": source,
        **({"sourceApi": source_api} if source_api else {}),
        **({"note": note} if note else {}),
    }


_CATALOG = [
    # Existing Nova-compatible presets retained for saved-settings compatibility.
    _provider(
        "minimax-cn",
        "MiniMax CN",
        api="anthropic-messages",
        endpoint="https://api.minimaxi.com",
        auth="x-api-key",
        source="nova-compatibility",
        models=[
            _model("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed", 204800, 128000, reasoning=True),
            _model("MiniMax-M3", "MiniMax M3", 524288, 524288, reasoning=True),
            _model("MiniMax-M2.5", "MiniMax M2.5", 200000, 8192, reasoning=True),
        ],
    ),
    _provider(
        "glm",
        "GLM",
        api="anthropic-messages",
        endpoint="https://open.bigmodel.cn/api/anthropic",
        auth="x-api-key",
        source="nova-compatibility",
        models=[
            _model("glm-5.2", "GLM-5.2", 1000000, 128000, reasoning=True),
            _model("glm-5.1", "GLM-5.1", 200000, 128000, reasoning=True),
            _model("glm-5-turbo", "GLM-5-Turbo", 200000, 128000, reasoning=True),
            _model("glm-4.7", "GLM-4.7", 200000, 128000, reasoning=True),
        ],
    ),
    _provider(
        "dashscope",
        "DashScope / Qwen",
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        source="nova-compatibility",
        models=[
            _model("qwen3.5-flash-2026-02-23", "Qwen3.5 Flash", 131072, 8192),
            _model("qwen3.5-plus-2026-02-15", "Qwen3.5 Plus", 131072, 8192),
            _model("qwen3-max-2026-01-23", "Qwen3 Max", 131072, 4096),
            _model("qwen3.5-35b-a3b", "Qwen3.5 35B", 131072, 4096),
        ],
    ),
    # OpenClaw onboard Model/auth provider entries.
    _provider(
        "openai",
        "OpenAI",
        endpoint="https://api.openai.com/v1",
        source="nova-compatibility",
        source_api="openai-completions",
        models=[
            _model("gpt-5.6-sol", "GPT-5.6 Sol", 1050000, 128000, reasoning=True),
            _model("gpt-5.6-terra", "GPT-5.6 Terra", 1050000, 128000, reasoning=True),
            _model("gpt-5.6-luna", "GPT-5.6 Luna", 1050000, 128000, reasoning=True),
            _model("gpt-5.1", "GPT-5.1", 400000, 128000, reasoning=True),
            _model("gpt-5", "GPT-5", 400000, 128000, reasoning=True),
            _model("gpt-5-mini", "GPT-5 mini", 400000, 128000, reasoning=True),
        ],
    ),
    _provider(
        "anthropic",
        "Anthropic",
        api="anthropic-messages",
        endpoint="https://api.anthropic.com/v1/messages",
        auth="x-api-key",
        source="nova-compatibility",
        source_api="anthropic-messages",
        models=[
            _model("claude-fable-5", "Claude Fable 5", 1000000, 128000, reasoning=True),
            _model("claude-sonnet-5", "Claude Sonnet 5", 1000000, 128000, reasoning=True),
            _model("claude-opus-4-6", "Claude Opus 4.6", 200000, 32000),
            _model("claude-sonnet-4-5", "Claude Sonnet 4.5", 200000, 64000),
        ],
    ),
    _provider(
        "xai",
        "xAI (Grok)",
        api="openai-responses",
        endpoint="https://api.x.ai/v1",
        status="needs_transport",
        source_api="openai-responses",
        models=[_model("grok-build-0.1"), _model("grok-4.1-fast"), _model("grok-4.1")],
    ),
    _provider(
        "google",
        "Google",
        api="google-generative-ai",
        endpoint="https://generativelanguage.googleapis.com/v1beta",
        status="needs_transport",
        source_api="google-generative-ai",
        models=[
            _model("gemini-2.5-pro", "Gemini 2.5 Pro", 1000000, 65536),
            _model("gemini-2.5-flash", "Gemini 2.5 Flash", 1000000, 65536),
            _model("gemini-2.0-flash", "Gemini 2.0 Flash", 1000000, 8192),
        ],
    ),
    _provider(
        "arcee",
        "Arcee AI",
        endpoint="https://api.arcee.ai/api/v1",
        source_api="openai-completions",
        models=[_model("trinity-mini", "Trinity Mini"), _model("trinity-nano", "Trinity Nano"), _model("maestro-reasoning")],
    ),
    _provider("brave", "Brave", status="auth_only_or_local", note="Search provider, not a Diary generation LLM preset."),
    _provider(
        "byteplus",
        "BytePlus",
        endpoint="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        source_api="openai-completions",
        models=[_model("ark-code-latest", context_window=256000, max_tokens=32000), _model("seed-1-8-251228")],
    ),
    _provider(
        "cerebras",
        "Cerebras",
        endpoint="https://api.cerebras.ai/v1",
        source_api="openai-completions",
        models=[_model("zai-glm-4.7"), _model("llama-3.3-70b"), _model("qwen-3-coder-480b")],
    ),
    _provider(
        "chutes",
        "Chutes",
        endpoint="https://llm.chutes.ai/v1",
        source_api="openai-completions",
        models=[_model("Qwen/Qwen3-32B-TEE"), _model("deepseek-ai/DeepSeek-V3.2"), _model("moonshotai/Kimi-K2.6")],
    ),
    _provider("cloudflare-ai-gateway", "Cloudflare AI Gateway", status="auth_only_or_local", note="Requires gateway account-specific routing."),
    _provider("codex", "Codex", status="auth_only_or_local", note="App-server/sign-in provider, not a generic API-key preset."),
    _provider("copilot", "Copilot", status="auth_only_or_local", note="OAuth/device-pairing provider."),
    _provider(
        "deepinfra",
        "DeepInfra",
        endpoint="https://api.deepinfra.com/v1/openai",
        source_api="openai-completions",
        models=[_model("deepseek-ai/DeepSeek-V4-Flash"), _model("Qwen/Qwen3-Coder-480B-A35B-Instruct"), _model("meta-llama/Llama-3.3-70B-Instruct")],
    ),
    _provider(
        "deepseek",
        "DeepSeek",
        endpoint="https://api.deepseek.com",
        source_api="openai-completions",
        models=[_model("deepseek-v4-flash", "DeepSeek V4 Flash", 128000, 8192), _model("deepseek-chat", "DeepSeek Chat", 131072), _model("deepseek-reasoner", "DeepSeek Reasoner", 131072, reasoning=True)],
    ),
    _provider(
        "fireworks",
        "Fireworks",
        endpoint="https://api.fireworks.ai/inference/v1",
        source_api="openai-completions",
        models=[_model("accounts/fireworks/models/kimi-k2p6", "Kimi K2.6"), _model("accounts/fireworks/models/deepseek-v3p2")],
    ),
    _provider(
        "gmi",
        "GMI Cloud",
        endpoint="https://api.gmi-serving.com/v1",
        source_api="openai-completions",
        models=[_model("zai-org/GLM-5.1-FP8"), _model("Qwen/Qwen3-Coder-480B-A35B-Instruct"), _model("moonshotai/Kimi-K2.6")],
    ),
    _provider(
        "google-vertex",
        "Google Vertex",
        api="google-vertex",
        endpoint="https://{location}-aiplatform.googleapis.com",
        status="needs_transport",
        source_api="google-vertex",
        models=[_model("gemini-2.5-pro"), _model("gemini-2.5-flash")],
    ),
    _provider("groq", "Groq", status="needs_transport", note="OpenClaw onboard auth entry; static callable catalog not bundled in this install."),
    _provider(
        "huggingface",
        "Hugging Face",
        endpoint="https://router.huggingface.co/v1",
        source_api="openai-completions",
        models=[_model("deepseek-ai/DeepSeek-R1"), _model("Qwen/Qwen3-Coder-480B-A35B-Instruct"), _model("moonshotai/Kimi-K2.6")],
    ),
    _provider("kilocode", "Kilo Gateway", endpoint="https://api.kilo.ai/api/gateway/", source_api="openai-completions", models=[_model("kilo/auto")]),
    _provider("litellm", "LiteLLM", endpoint="http://localhost:4000", auth="none", source_api="openai-completions", models=[_model("claude-opus-4-6")]),
    _provider("lmstudio", "LM Studio", status="auth_only_or_local", note="Local server preset needs operator-specific model discovery."),
    _provider("microsoft-foundry", "Microsoft Foundry", status="needs_transport", note="Requires Entra/API-key Foundry-specific setup."),
    _provider(
        "minimax",
        "MiniMax",
        api="anthropic-messages",
        endpoint="https://api.minimax.io/anthropic",
        auth="x-api-key",
        source_api="anthropic-messages",
        models=[_model("MiniMax-M3", context_window=524288, max_tokens=524288, reasoning=True), _model("MiniMax-M2.7-highspeed", context_window=204800, max_tokens=128000, reasoning=True), _model("MiniMax-M2.5", context_window=1000000, max_tokens=65536, reasoning=True)],
    ),
    _provider(
        "mistral",
        "Mistral AI",
        endpoint="https://api.mistral.ai/v1",
        source_api="openai-completions",
        models=[_model("codestral-latest"), _model("mistral-large-latest"), _model("magistral-medium-latest", reasoning=True)],
    ),
    _provider(
        "moonshot",
        "Moonshot AI (Kimi K2.7)",
        endpoint="https://api.moonshot.ai/v1",
        source_api="openai-completions",
        models=[
            _model("kimi-k2.7-code", "Kimi K2.7 Code", 262144, reasoning=True),
            _model("kimi-k2.7-code-highspeed", "Kimi K2.7 Code Highspeed", 262144, reasoning=True),
            _model("kimi-k2.6", "Kimi K2.6", 262144, reasoning=True),
            _model("kimi-k2.5", "Kimi K2.5", 262144, reasoning=True),
        ],
    ),
    _provider(
        "kimi-code",
        "Kimi Code",
        api="anthropic-messages",
        endpoint="https://api.kimi.com/coding/",
        auth="x-api-key",
        source="nova-compatibility",
        source_api="anthropic-messages",
        note="Kimi Code API; use Anthropic-compatible /coding/v1/messages, not Moonshot OpenAI-compatible API.",
        models=[_model("kimi-for-coding", "Kimi for Coding", 262144, 32768)],
    ),
    _provider(
        "novita",
        "NovitaAI",
        endpoint="https://api.novita.ai/openai/v1",
        source_api="openai-completions",
        models=[_model("moonshotai/kimi-k2.5"), _model("deepseek/deepseek-v3.2"), _model("qwen/qwen3-coder")],
    ),
    _provider(
        "nvidia",
        "NVIDIA",
        endpoint="https://integrate.api.nvidia.com/v1",
        source_api="openai-completions",
        models=[_model("nvidia/nemotron-3-ultra-550b-a55b"), _model("moonshotai/kimi-k2-0905"), _model("qwen/qwen3-coder-480b-a35b-instruct")],
    ),
    _provider("ollama", "Ollama", status="auth_only_or_local", note="Local model discovery required; use Custom for a known /v1 endpoint."),
    _provider("opencode", "OpenCode", status="auth_only_or_local", note="OpenCode harness integration, not a generic API-key preset."),
    _provider("openrouter", "OpenRouter", endpoint="https://openrouter.ai/api/v1", source_api="openai-completions", models=[_model("openrouter/auto"), _model("anthropic/claude-sonnet-4.5"), _model("moonshotai/kimi-k2.6")]),
    _provider("qianfan", "Qianfan", endpoint="https://qianfan.baidubce.com/v2", source_api="openai-completions", models=[_model("deepseek-v3.2"), _model("ernie-x1.1")]),
    _provider(
        "qwen",
        "Qwen Cloud",
        endpoint="https://coding-intl.dashscope.aliyuncs.com/v1",
        source_api="openai-completions",
        models=[
            _model("qwen3.5-plus", context_window=1000000, max_tokens=65536),
            _model("qwen3.6-plus", context_window=1000000, max_tokens=65536),
            _model("qwen3-max-2026-01-23", context_window=262144, max_tokens=65536),
            _model("qwen3-coder-next", context_window=262144, max_tokens=65536),
            _model("qwen3-coder-plus", context_window=1000000, max_tokens=65536),
            _model("MiniMax-M2.5", context_window=1000000, max_tokens=65536, reasoning=True),
            _model("glm-5", context_window=202752, max_tokens=16384),
            _model("kimi-k2.5", context_window=262144, max_tokens=32768),
        ],
    ),
    _provider("qwen-coding", "Qwen Coding", endpoint="https://coding-intl.dashscope.aliyuncs.com/v1", source_api="openai-completions", models=[_model("qwen3.5-plus", context_window=1000000, max_tokens=65536), _model("qwen3-coder-plus", context_window=1000000, max_tokens=65536)]),
    _provider("sglang", "SGLang", status="auth_only_or_local", note="Local server preset needs operator-specific endpoint/model."),
    _provider("stepfun", "StepFun", endpoint="https://api.stepfun.ai/v1", source_api="openai-completions", models=[_model("step-3.5-flash"), _model("step-3.5-mini")]),
    _provider("synthetic", "Synthetic", api="anthropic-messages", endpoint="https://api.synthetic.new/anthropic", source_api="anthropic-messages", models=[_model("hf:MiniMaxAI/MiniMax-M2.5"), _model("anthropic:claude-opus-4.6"), _model("openai:gpt-5.1")]),
    _provider("tencent", "Tencent Cloud", endpoint="https://tokenhub.tencentmaas.com/v1", source_api="openai-completions", models=[_model("hy3-preview")]),
    _provider("together", "Together AI", endpoint="https://api.together.xyz/v1", source_api="openai-completions", models=[_model("moonshotai/Kimi-K2.6"), _model("deepseek-ai/DeepSeek-V3.2"), _model("Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8")]),
    _provider(
        "venice",
        "Venice AI",
        endpoint="https://api.venice.ai/api/v1",
        source_api="openai-completions",
        models=[
            _model("zai-org-glm-5-1", "GLM 5.1"),
            _model("kimi-k2-5", "Kimi K2.5", 262144, 32768),
            _model("qwen3-coder-480b-a35b-instruct", "Qwen3 Coder 480B", 256000, 65536),
            _model("deepseek-v3.2", "DeepSeek V3.2", 160000, 32768, reasoning=True),
        ],
    ),
    _provider("vercel-ai-gateway", "Vercel AI Gateway", api="anthropic-messages", endpoint="https://ai-gateway.vercel.sh", source_api="anthropic-messages", models=[_model("anthropic/claude-opus-4.6"), _model("anthropic/claude-sonnet-4.5"), _model("openai/gpt-5.1")]),
    _provider("vllm", "vLLM", status="auth_only_or_local", note="Local server preset needs operator-specific endpoint/model."),
    _provider("volcengine", "Volcano Engine", endpoint="https://ark.cn-beijing.volces.com/api/coding/v3", source_api="openai-completions", models=[_model("ark-code-latest", context_window=256000, max_tokens=32000), _model("deepseek-v3.2"), _model("doubao-seed-2.0-code")]),
    _provider("volcano", "Volcano Ark", endpoint="https://ark.cn-beijing.volces.com/api/coding/v3", source="nova-compatibility", models=[_model("ark-code-latest", context_window=256000, max_tokens=32000), _model("deepseek-v3.2", context_window=128000, max_tokens=32000), _model("doubao-seed-2.0-code", context_window=256000, max_tokens=128000)]),
    _provider("xiaomi", "Xiaomi", endpoint="https://api.xiaomimimo.com/v1", source_api="openai-completions", models=[_model("mimo-v2-flash"), _model("mimo-v2.5-pro"), _model("mimo-v2-coder")]),
    _provider("zai", "Z.AI", status="needs_transport", note="OpenClaw onboard auth entry; static callable catalog not bundled in this install."),
]


def llm_provider_catalog() -> list[dict[str, Any]]:
    catalog = copy.deepcopy(_CATALOG)
    catalog.append(
        {
            "id": CUSTOM_PROVIDER_ID,
            "name": "Custom Provider",
            "api": "custom",
            "endpoint": "",
            "auth": "bearer",
            "models": [],
            "enabled": True,
            "status": "custom",
            "source": "operator",
        }
    )
    return catalog


def llm_provider_operations_status() -> dict[str, Any]:
    """Return provider catalog coverage and helper-script policy without secrets."""
    catalog = llm_provider_catalog()
    ids = {provider["id"] for provider in catalog}
    statuses: dict[str, int] = {}
    for provider in catalog:
        status = str(provider.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    executable = [provider["id"] for provider in catalog if provider.get("enabled") and provider["id"] != CUSTOM_PROVIDER_ID]
    return {
        "source": OPENCLAW_SOURCE,
        "catalogCount": len(catalog),
        "enabledPresetCount": len(executable),
        "enabledPresetIds": sorted(executable),
        "customProviderEnabled": any(provider["id"] == CUSTOM_PROVIDER_ID and provider.get("enabled") for provider in catalog),
        "statusCounts": statuses,
        "onboardCoverage": {
            "expected": sorted(OPENCLAW_ONBOARD_PROVIDER_IDS),
            "missing": sorted(OPENCLAW_ONBOARD_PROVIDER_IDS - ids),
            "extra": sorted(ids - OPENCLAW_ONBOARD_PROVIDER_IDS),
        },
        "secretPolicy": {
            "catalogContainsSecrets": False,
            "operatorCredentialField": "llmProvider.apiKey",
            "getResponsesRedactSecrets": True,
        },
        "helperScripts": [
            {
                "path": "src/diary_generator/diary_summary.py",
                "classification": "migration-only",
                "decision": "retain",
                "runtimeSupported": False,
                "requiresSharedResolverBeforeSupport": True,
            },
            {
                "path": "src/diary_generator/diary_summary_editor.py",
                "classification": "candidate-removal",
                "decision": "defer-removal",
                "runtimeSupported": False,
                "requiresExplicitCleanupApproval": True,
            },
        ],
    }


def find_provider(provider_id: str | None, *, require_enabled: bool = True) -> dict[str, Any] | None:
    for provider in _CATALOG:
        if provider["id"] == provider_id and (provider.get("enabled") or not require_enabled):
            return copy.deepcopy(provider)
    return None


def find_model(provider_id: str | None, model_id: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    provider = find_provider(provider_id, require_enabled=True)
    if not provider:
        return None
    models = provider.get("models") or []
    if model_id:
        for model in models:
            if model.get("id") == model_id:
                return provider, copy.deepcopy(model)
    if models:
        return provider, copy.deepcopy(models[0])
    return None


def default_llm_provider_settings() -> dict[str, Any]:
    auto_gate = auto_pipeline_gate_tokens(None)
    return {
        "mode": CUSTOM_PROVIDER_ID,
        "provider": CUSTOM_PROVIDER_ID,
        "presetProvider": "",
        "endpoint": "",
        "model": "",
        "api": "openai-compatible",
        "contextWindow": None,
        "maxTokens": None,
        "pipelineConcurrency": DEFAULT_PIPELINE_CONCURRENCY,
        "pipelineGateMode": PIPELINE_GATE_MODE_AUTO,
        "pipelineGateTokens": auto_gate,
        "autoPipelineGateTokens": auto_gate,
        "timeoutSeconds": DEFAULT_LLM_TIMEOUT_SECONDS,
        "apiKey": "",
        "apiKeyEnv": "LLM_API_KEY",
    }


def normalize_llm_provider_update(update: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    current = current or {}
    update = update if isinstance(update, dict) else {}
    requested_mode = str(update.get("mode") or "").strip()
    requested_provider = str(update.get("provider") or update.get("presetProvider") or "").strip()
    if requested_mode:
        mode = requested_mode
    elif requested_provider:
        # `open-nova model set --provider ...` is the product-facing switch
        # boundary and intentionally has no separate --mode flag.  An
        # explicit provider therefore outranks a stale mode from the current
        # provider; otherwise custom -> preset silently remains custom.
        mode = CUSTOM_PROVIDER_ID if requested_provider == CUSTOM_PROVIDER_ID else "preset"
    else:
        mode = str(current.get("mode") or "preset")
    provider_id = str(
        requested_provider
        or current.get("provider")
        or current.get("presetProvider")
        or ""
    )
    update_has_gate = "pipelineGateTokens" in update and str(update.get("pipelineGateTokens") or "").strip()
    if mode != CUSTOM_PROVIDER_ID and provider_id != CUSTOM_PROVIDER_ID:
        model_id = str(update.get("model") or current.get("model") or "")
        matched = find_model(provider_id, model_id)
        if matched:
            provider, model = matched
            model_changed = model.get("id") != current.get("model") or provider.get("id") != current.get("provider")
            default_gate = auto_pipeline_gate_tokens(model.get("contextWindow"))
            gate_mode = _pipeline_gate_mode(update, current, update_has_gate=update_has_gate, model_changed=model_changed)
            manual_gate = _positive_int(
                update.get("pipelineGateTokens") if update_has_gate else None,
                current.get("pipelineGateTokens"),
                default_gate,
            )
            return {
                **current,
                "mode": "preset",
                "provider": provider["id"],
                "presetProvider": provider["id"],
                "endpoint": provider["endpoint"],
                "model": model["id"],
                "api": provider["api"],
                "contextWindow": model.get("contextWindow"),
                "maxTokens": model.get("maxTokens"),
                "pipelineConcurrency": _positive_int(update.get("pipelineConcurrency"), current.get("pipelineConcurrency"), DEFAULT_PIPELINE_CONCURRENCY),
                "pipelineGateMode": gate_mode,
                "pipelineGateTokens": default_gate if gate_mode == PIPELINE_GATE_MODE_AUTO else manual_gate,
                "autoPipelineGateTokens": default_gate,
                "timeoutSeconds": _positive_int(update.get("timeoutSeconds"), current.get("timeoutSeconds"), DEFAULT_LLM_TIMEOUT_SECONDS),
                "apiKey": update.get("apiKey", current.get("apiKey", "")),
                "apiKeyEnv": _normalized_api_key_env(update, current),
            }
    switching_to_custom = mode == CUSTOM_PROVIDER_ID and str(
        current.get("provider") or current.get("presetProvider") or ""
    ) != CUSTOM_PROVIDER_ID
    current_for_custom = {} if switching_to_custom else current
    custom_context = int(update.get("contextWindow") or current_for_custom.get("contextWindow") or 0) or None
    default_gate = auto_pipeline_gate_tokens(custom_context)
    gate_mode = _pipeline_gate_mode(update, current, update_has_gate=update_has_gate, model_changed=False)
    manual_gate = _positive_int(
        update.get("pipelineGateTokens") if update_has_gate else None,
        current.get("pipelineGateTokens"),
        default_gate,
    )
    return {
        **current,
        "mode": CUSTOM_PROVIDER_ID,
        "provider": CUSTOM_PROVIDER_ID,
        "presetProvider": "",
        "endpoint": str(update.get("endpoint") or current_for_custom.get("endpoint") or ""),
        "model": str(update.get("model") or current_for_custom.get("model") or ""),
        "api": str(update.get("api") or current_for_custom.get("api") or "openai-compatible"),
        "contextWindow": custom_context,
        "maxTokens": int(update.get("maxTokens") or current_for_custom.get("maxTokens") or 0) or None,
        "pipelineConcurrency": _positive_int(update.get("pipelineConcurrency"), current.get("pipelineConcurrency"), DEFAULT_PIPELINE_CONCURRENCY),
        "pipelineGateMode": gate_mode,
        "pipelineGateTokens": default_gate if gate_mode == PIPELINE_GATE_MODE_AUTO else manual_gate,
        "autoPipelineGateTokens": default_gate,
        "timeoutSeconds": _positive_int(update.get("timeoutSeconds"), current.get("timeoutSeconds"), DEFAULT_LLM_TIMEOUT_SECONDS),
        "apiKey": update.get("apiKey", current.get("apiKey", "")),
        "apiKeyEnv": _normalized_api_key_env(update, current),
    }


def _normalized_api_key_env(update: dict[str, Any], current: dict[str, Any]) -> str:
    candidate = str(update.get("apiKeyEnv") or current.get("apiKeyEnv") or "LLM_API_KEY")
    return candidate if ENV_VAR_NAME_RE.match(candidate) else "LLM_API_KEY"


def _pipeline_gate_mode(
    update: dict[str, Any],
    current: dict[str, Any],
    *,
    update_has_gate: bool,
    model_changed: bool,
) -> str:
    requested = str(update.get("pipelineGateMode") or update.get("pipelineGateSource") or "").strip().lower()
    if requested in PIPELINE_GATE_MODES:
        return requested
    if update_has_gate:
        return PIPELINE_GATE_MODE_MANUAL
    current_mode = str(current.get("pipelineGateMode") or "").strip().lower()
    if current_mode in PIPELINE_GATE_MODES:
        return current_mode
    if current.get("pipelineGateTokens") and not model_changed:
        return PIPELINE_GATE_MODE_MANUAL
    return PIPELINE_GATE_MODE_AUTO


def _positive_int(*values: Any) -> int:
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 1
