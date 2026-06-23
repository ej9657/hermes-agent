"""Regression tests for standalone Telegram delivery lazy deps."""

import asyncio
import builtins
import sys
import types
from types import SimpleNamespace


def test_standalone_telegram_send_lazy_installs_missing_sdk(monkeypatch):
    """Cron fallback delivery should lazy-install Telegram before failing."""
    from tools.send_message_tool import _send_telegram

    for name in ("telegram", "telegram.constants", "telegram.request"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    calls = []
    installed = False
    original_import = builtins.__import__

    class FakeBot:
        def __init__(self, token):
            self.token = token

        async def send_message(self, **kwargs):
            return SimpleNamespace(message_id=123, kwargs=kwargs)

    def fake_ensure(feature, *, prompt=True):
        calls.append((feature, prompt))
        parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2", HTML="HTML")
        telegram_mod = types.ModuleType("telegram")
        telegram_mod.Bot = FakeBot
        constants_mod = types.ModuleType("telegram.constants")
        constants_mod.ParseMode = parse_mode
        telegram_mod.constants = constants_mod
        monkeypatch.setitem(sys.modules, "telegram", telegram_mod)
        monkeypatch.setitem(sys.modules, "telegram.constants", constants_mod)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if not installed and (name == "telegram" or name.startswith("telegram.")):
            raise ImportError("No module named 'telegram'")
        return original_import(name, globals, locals, fromlist, level)

    def fake_ensure_and_install(feature, *, prompt=True):
        nonlocal installed
        fake_ensure(feature, prompt=prompt)
        installed = True

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("tools.lazy_deps.ensure", fake_ensure_and_install)

    result = asyncio.run(_send_telegram("token", "12345", "hello"))

    assert calls == [("platform.telegram", False)]
    assert result["success"] is True
    assert result["platform"] == "telegram"
    assert result["message_id"] == "123"
