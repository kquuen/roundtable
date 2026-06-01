"""Roundtable configuration manager.

Loads product-level provider configuration from config/providers.yaml,
supports environment variable substitution (${ENV_VAR}), and provides
hot-reload capability.

All external services (LLM, ASR) are managed here so users don't need
to configure individual API keys — they are injected at deployment time.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("roundtable.config")

# Default config path (relative to project root)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "providers.yaml"


class ProviderConfig:
    """Normalized configuration for a single provider."""

    def __init__(self, raw: dict[str, Any]):
        self.id = raw.get("id", "")
        self.name = raw.get("name", self.id)
        self.protocol = raw.get("protocol", "openai")
        self.base_url = raw.get("base_url", "")
        self.ws_url = raw.get("ws_url", "")
        self.api_key = raw.get("api_key", "")
        self.timeout = raw.get("timeout", 60)
        self.models: list[dict[str, Any]] = raw.get("models", [])
        self._raw = raw

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Find a model definition by id."""
        for m in self.models:
            if m.get("id") == model_id:
                return m
        return None

    def model_exists(self, model_id: str) -> bool:
        return self.get_model(model_id) is not None


class ConfigManager:
    """Singleton configuration manager.

    Loads config/providers.yaml and resolves ${ENV_VAR} placeholders.
    """

    _instance: ConfigManager | None = None

    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path or DEFAULT_CONFIG_PATH)
        self._providers: dict[str, ProviderConfig] = {}
        self._agent_models: dict[str, str] = {}
        self._voice_config: dict[str, Any] = {}
        self._loaded = False
        self.load()

    @classmethod
    def get(cls) -> ConfigManager:
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = ConfigManager()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (mainly for testing)."""
        cls._instance = None

    def load(self) -> None:
        """Load and parse the YAML configuration file."""
        if not self._config_path.exists():
            logger.warning("Config file not found: %s. Using defaults.", self._config_path)
            self._loaded = False
            return

        try:
            raw_text = self._config_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read config: %s", e)
            self._loaded = False
            return

        # Resolve ${ENV_VAR} placeholders
        resolved = _resolve_env_vars(raw_text)

        try:
            data = yaml.safe_load(resolved)
        except yaml.YAMLError as e:
            logger.error("Failed to parse config YAML: %s", e)
            self._loaded = False
            return

        if not isinstance(data, dict):
            logger.error("Config YAML root must be a dict")
            self._loaded = False
            return

        # Parse providers
        providers_raw = data.get("providers", {})
        self._providers = {}
        for pid, pval in providers_raw.items():
            if isinstance(pval, dict):
                pval["id"] = pid
                self._providers[pid] = ProviderConfig(pval)

        # Parse agent_models mapping
        self._agent_models = data.get("agent_models", {})

        # Parse voice config
        self._voice_config = data.get("voice", {})

        self._loaded = True
        logger.info(
            "Config loaded: %d providers, %d agent models, voice=%s",
            len(self._providers),
            len(self._agent_models),
            self._voice_config.get("asr_provider", "none"),
        )

    def reload(self) -> None:
        """Hot-reload configuration from disk."""
        logger.info("Reloading configuration from %s", self._config_path)
        self.load()
        try:
            from roundtable.providers import ProviderRouter

            router = ProviderRouter.get_instance()
            router.clear_cache()
            logger.info("ProviderRouter cache cleared after config reload")
        except Exception:
            logger.warning("Failed to clear ProviderRouter cache on reload", exc_info=True)

    # ── Provider queries ──

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """Get a provider configuration by id."""
        return self._providers.get(provider_id)

    def list_providers(self) -> list[str]:
        """List all configured provider ids."""
        return list(self._providers.keys())

    def has_provider(self, provider_id: str) -> bool:
        return provider_id in self._providers

    # ── Model queries ──

    def get_agent_model(self, skill_id: str) -> str | None:
        """Get the configured model reference for an agent skill.

        Returns provider/model string, e.g. "deepseek/deepseek-chat".
        """
        return self._agent_models.get(skill_id)

    def list_agent_models(self) -> dict[str, str]:
        """Return all agent → model mappings."""
        return dict(self._agent_models)

    def get_model_config(self, model_ref: str) -> tuple[ProviderConfig, str] | None:
        """Resolve a model reference like 'deepseek/deepseek-chat'.

        Returns (ProviderConfig, model_id) or None if not found.
        """
        if "/" not in model_ref:
            return None
        provider_id, model_id = model_ref.split("/", 1)
        provider = self._providers.get(provider_id)
        if provider is None:
            return None
        if not provider.model_exists(model_id):
            logger.warning("Model %s not found in provider %s", model_id, provider_id)
        return provider, model_id

    # ── Voice config ──

    def get_voice_config(self) -> dict[str, Any]:
        """Get voice-related configuration."""
        return dict(self._voice_config)

    def get_voice_asr_config(self) -> tuple[ProviderConfig, str] | None:
        """Get the configured ASR provider and model.

        Returns (ProviderConfig, model_id) or None.
        """
        asr_provider_id = self._voice_config.get("asr_provider")
        asr_model_id = self._voice_config.get("asr_model")
        if not asr_provider_id or not asr_model_id:
            return None
        provider = self._providers.get(asr_provider_id)
        if provider is None:
            return None
        return provider, asr_model_id

    def get_voice_default_llm(self) -> str | None:
        """Get the default LLM for voice Q&A."""
        return self._voice_config.get("default_llm")

    @property
    def loaded(self) -> bool:
        return self._loaded


# ── Environment variable resolution ──

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(text: str) -> str:
    """Replace ${ENV_VAR} placeholders with actual environment variable values.

    If the variable is not set, the placeholder is replaced with an empty
    string so that downstream validators (e.g. API key checks) fail cleanly
    and fall back to mock mode.
    """
    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.getenv(var_name)
        if value is None:
            logger.warning("Environment variable %s not set, replacing with empty string", var_name)
            return ""
        return value

    return _ENV_VAR_PATTERN.sub(_replacer, text)
