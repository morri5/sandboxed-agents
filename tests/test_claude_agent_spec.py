"""The `claude_agent` VM spec, straight to DeepSeek and through the Langfuse AI gateway."""

from pathlib import Path

import pytest
from microsandbox import DestGroup, Destination, Protocol, Rule

from lab import config
from lab.harnesses import claude_agent
from lab.harnesses.claude_agent import ClaudeAgentHarness, sdk_model
from lab.harnesses.claude_factory import ClaudeFactoryHarness
from lab.sandbox import SandboxError

GATEWAY = "http://host.microsandbox.internal:3031/anthropic"


@pytest.fixture(autouse=True)
def sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed SDK and both keys on the host; no gateway unless a test sets one."""
    monkeypatch.setattr(claude_agent, "SDK_HOST", tmp_path)
    monkeypatch.setattr(config, "MODEL_GATEWAY", "")
    monkeypatch.setattr(config, "GATEWAY_CONNECTION", "deepseek")
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setenv("LAB_GATEWAY_KEY", "sk-gateway")


def test_direct_spec_reaches_deepseek_only() -> None:
    spec = ClaudeAgentHarness().vm_spec()
    assert spec.env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert spec.network is not None and spec.network.policy is not None
    assert spec.network.policy.rules == (
        *Rule.allow_dns(),
        Rule.allow(destination=Destination.domain("api.deepseek.com")),
    )
    assert {(s.env_var, s.value, s.allow) for s in spec.secrets} == {
        ("ANTHROPIC_AUTH_TOKEN", "sk-deepseek", ("api.deepseek.com",)),
        ("ANTHROPIC_API_KEY", "sk-deepseek", ("api.deepseek.com",)),
    }
    assert all(s.require_tls_identity for s in spec.secrets)
    assert sdk_model() == "deepseek-flash"


def test_gateway_spec_reaches_the_gateway_port_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DeepSeek key stays out of the spec: the VM gets the gateway key's placeholder,
    for the gateway host only, and no `ANTHROPIC_API_KEY` (two credentials give a 401)."""
    monkeypatch.setattr(config, "MODEL_GATEWAY", GATEWAY)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    for harness in (ClaudeAgentHarness(), ClaudeFactoryHarness()):
        spec = harness.vm_spec()
        assert spec.env["ANTHROPIC_BASE_URL"] == GATEWAY
        assert spec.network is not None and spec.network.policy is not None
        assert spec.network.policy.rules == (
            *Rule.allow_dns(),
            Rule.allow(
                destination=Destination.group(DestGroup.HOST), protocol=Protocol.TCP, port=3031
            ),
        )
        [secret] = spec.secrets
        assert (secret.env_var, secret.value) == ("ANTHROPIC_AUTH_TOKEN", "sk-gateway")
        assert secret.allow == ("host.microsandbox.internal",)
        assert secret.substitution.header_fields == ("authorization",)
        assert not secret.require_tls_identity  # plain HTTP on the host
    # Prices stay keyed by the DeepSeek model; only the ID sent to the gateway changes.
    assert sdk_model() == "deepseek:deepseek-flash"


def test_gateway_spec_needs_its_key_and_a_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MODEL_GATEWAY", GATEWAY)
    monkeypatch.delenv("LAB_GATEWAY_KEY")
    with pytest.raises(SandboxError, match="LAB_GATEWAY_KEY"):
        ClaudeAgentHarness().vm_spec()
    monkeypatch.setenv("LAB_GATEWAY_KEY", "sk-gateway")
    monkeypatch.setattr(config, "MODEL_GATEWAY", "http://host.microsandbox.internal/anthropic")
    with pytest.raises(SandboxError, match="port"):
        ClaudeAgentHarness().vm_spec()
