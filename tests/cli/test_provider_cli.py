from pathlib import Path
import unittest

from typer.testing import CliRunner

from kagweb_cli import provider_cmd
from kagweb_cli.main import app

ROOT = Path(__file__).resolve().parents[2]
PROVIDER_CMD = (ROOT / "kagweb_cli" / "provider_cmd.py").read_text(encoding="utf-8")
CLI_README = (ROOT / "kagweb_cli" / "README.md").read_text(encoding="utf-8")
ROOT_README = (ROOT / "README.md").read_text(encoding="utf-8")


class ProviderCliDocsContractTest(unittest.TestCase):
    def test_provider_contract_describes_copilot_as_validation_not_oauth_login(self) -> None:
        self.assertIn(
            '"Provider: github-copilot (validate existing Copilot auth) | "',
            PROVIDER_CMD,
        )
        self.assertIn('"codebuddy (validate CodeBuddy SDK auth)"', PROVIDER_CMD)
        self.assertIn('"""Authenticate or validate provider access."""', PROVIDER_CMD)
        self.assertIn("GitHub Copilot auth validation succeeded.", PROVIDER_CMD)
        self.assertIn("GitHub Copilot auth validation failed:", PROVIDER_CMD)
        self.assertIn("CodeBuddy auth validation succeeded.", PROVIDER_CMD)
        self.assertIn("CodeBuddy auth validation failed:", PROVIDER_CMD)
        self.assertIn("Starting CodeBuddy login flow", PROVIDER_CMD)
        self.assertNotIn("openai-codex", PROVIDER_CMD)
        self.assertNotIn("GitHub Copilot OAuth authentication succeeded.", PROVIDER_CMD)

    def test_readmes_match_the_cli_contract(self) -> None:
        self.assertIn(
            "Provider auth (`openai-codex` OAuth login; `github-copilot` validates an existing Copilot auth session; `codebuddy` validates CodeBuddy SDK auth and starts login when needed)",
            ROOT_README,
        )
        self.assertIn(
            "kagweb provider login github-copilot    # 校验现有 GitHub Copilot 认证是否可用",
            CLI_README,
        )
        self.assertIn(
            "kagweb provider login codebuddy         # 校验 CodeBuddy SDK 登录；未登录时打开登录入口",
            CLI_README,
        )
        self.assertNotIn("OAuth login (`openai-codex`, `github-copilot`)", ROOT_README)


def test_openai_codex_provider_is_not_offered() -> None:
    """The codex_auth retirement removed the provider OAuth login; the CLI
    must no longer accept it, and its module must not reference the retired
    service or the CLI's own credential files."""
    assert "get_codex_oauth_service" not in PROVIDER_CMD
    assert "~/.codex" not in PROVIDER_CMD
    runner = CliRunner().invoke(app, ["provider", "login", "openai-codex"])
    assert runner.exit_code != 0
    assert "openai-codex" not in runner.stdout


if __name__ == "__main__":
    unittest.main()
