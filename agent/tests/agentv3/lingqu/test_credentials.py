from __future__ import annotations

from agentv3.lingqu.catalog import load_catalog
import pytest

from agentv3.lingqu.credentials import MissingCredential, SecretResolver, redact

from .test_catalog import CATALOG


def _catalog():
    return load_catalog(CATALOG)


def test_company_key_applies_to_all_providers():
    catalog = _catalog()
    resolver = SecretResolver({"COMPANY_LLM_API_KEY": "company-key-placeholder-0001"})
    for profile in catalog.profiles():
        cred = resolver.resolve(profile)
        assert cred.source == "company_llm"
        assert cred.api_key == "company-key-placeholder-0001"
        assert "company-key" not in repr(cred)


def test_provider_key_overrides_company_key():
    catalog = _catalog()
    resolver = SecretResolver(
        {
            "COMPANY_LLM_API_KEY": "company-key-placeholder-0001",
            "LINGQU_KIMI_API_KEY": "kimi-key-placeholder-0002",
        }
    )
    cred = resolver.resolve(catalog.get("kimi/kimi-k3.1-pro"))
    assert cred.api_key == "kimi-key-placeholder-0002"
    assert cred.source == "provider:kimi"


def test_missing_key_names_both_env_vars():
    catalog = _catalog()
    resolver = SecretResolver({})
    with pytest.raises(MissingCredential) as exc:
        resolver.resolve(catalog.default())
    assert "COMPANY_LLM_API_KEY" in str(exc.value)
    assert "LINGQU_DEEPSEEK_API_KEY" in str(exc.value)


def test_qwen_workspace_template_falls_back_without_id():
    catalog = _catalog()
    resolver = SecretResolver({"COMPANY_LLM_API_KEY": "company-key-placeholder-0001"})
    qwen = catalog.get("qwen/qwen4-max")
    cred = resolver.resolve(qwen)
    assert cred.base_url == qwen.base_url_fallback


def test_qwen_workspace_id_is_expanded():
    catalog = _catalog()
    resolver = SecretResolver(
        {
            "COMPANY_LLM_API_KEY": "company-key-placeholder-0001",
            "LINGQU_QWEN_WORKSPACE_ID": "ws-123",
        }
    )
    cred = resolver.resolve(catalog.get("qwen/qwen4-max"))
    assert "ws-123" in cred.base_url
    assert "{" not in cred.base_url


def test_redact_removes_key_shapes():
    assert "sk-live" not in redact("Authorization: Bearer sk-live-abcdef")
    assert "secret" not in redact("api_key=secret")


def test_company_gateway_sets_openai_compatible_mode():
    catalog = _catalog()
    resolver = SecretResolver(
        {
            "COMPANY_LLM_API_KEY": "gateway-key-placeholder-0003",
            "COMPANY_LLM_BASE_URL": "http://127.0.0.1:4000/v1",
        }
    )
    cred = resolver.resolve(catalog.default())
    assert cred.gateway is True
    assert cred.base_url == "http://127.0.0.1:4000/v1"


def test_direct_provider_does_not_set_gateway_mode():
    catalog = _catalog()
    resolver = SecretResolver(
        {
            "LINGQU_DEEPSEEK_API_KEY": "deepseek-key-placeholder-0004",
            "LINGQU_DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        }
    )
    cred = resolver.resolve(catalog.default())
    assert cred.gateway is False
