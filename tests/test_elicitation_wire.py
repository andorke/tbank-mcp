"""Exercise real MCP SDK serialization against the strict host form contract.

No banking session or network is used. FakeCtx-based payment tests cannot catch
Pydantic metadata that Codex rejects before a form reaches the human.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.TemporaryDirectory(prefix="tbank-elicit-wire-")
for key, name in (("TBANK_ATTEMPTS", "attempts.jsonl"),
                  ("TBANK_EVENTS", "events.jsonl"),
                  ("TBANK_TRACE_FILE", "calls.jsonl")):
    os.environ[key] = str(Path(_TMP.name) / name)

from mcp.types import ElicitResult
from tbank_mcp import server, transfer_quote


class StrictPeer:
    def __init__(self, action="accept", content=None):
        self.action, self.content = action, content or {}
        self.seen = []

    def check_client_capability(self, capability):
        return True

    async def elicit_form(self, *, message, requestedSchema, related_request_id):
        # Codex app-server's generated McpElicitationSchema denies unknown root
        # keys. This is deliberately independent of our schema-cleanup helper.
        assert set(requestedSchema) <= {"$schema", "type", "properties", "required"}
        assert requestedSchema["type"] == "object"
        assert isinstance(requestedSchema["properties"], dict)
        # OpenClaw's structured-input boundary rejects layout control characters.
        assert not any(ord(c) < 32 for c in message)
        self.seen.append((message, requestedSchema))
        return ElicitResult(action=self.action,
                            content=self.content if self.action == "accept" else None)

    def context(self):
        return server.Context(request_context=SimpleNamespace(
            session=self, request_id="synthetic-confirmation"))


class ElicitationWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_probe_uses_real_sdk_without_bank_access(self):
        peer = StrictPeer()
        with patch.object(server, "_require", side_effect=AssertionError("bank accessed")):
            result = await server.banking_confirmation_test(ctx=peer.context())
        self.assertIn("Кнопка подтверждения получена", result)
        self.assertEqual(peer.seen[0][1]["properties"], {})
        self.assertIn("0 RUB", peer.seen[0][0])

    async def test_decline_and_cancel_never_become_accept(self):
        for action, text in (("decline", "Отменено пользователем"),
                             ("cancel", "Подтверждение не получено")):
            peer = StrictPeer(action)
            result = await server.banking_confirmation_test(ctx=peer.context())
            self.assertIn(text, result)
            self.assertEqual(len(peer.seen), 1)

    async def test_picker_keeps_required_enum_and_validates_reply(self):
        options = ["Тестовый банк А", "Тестовый банк Б"]
        for choice, outcome in ((options[1], server.ELICIT_ACCEPT),
                                ("Неизвестный банк", server.ELICIT_ERROR)):
            peer = StrictPeer(content={"choice": choice})
            actual, picked = await server._elicit_choice(peer.context(), "Банк?", options)
            self.assertEqual(actual, outcome)
            self.assertEqual(picked, choice if outcome == server.ELICIT_ACCEPT else None)
            form = peer.seen[0][1]
            self.assertEqual(form["required"], ["choice"])
            self.assertEqual(form["properties"]["choice"]["enum"], options)

    async def test_exact_transfer_terms_survive_inline_rendering(self):
        pack = {"amount": "100.25", "source": "TEST-SOURCE", "fee": "1.50",
                "total": "101.75", "recipient": "Тестовый получатель",
                "bank": "Тестовый банк", "fields": {
                    "bankAcnt": "TEST-DESTINATION", "comment": "Тестовое назначение"}}
        original = transfer_quote.display(pack)
        peer = StrictPeer()
        self.assertIsNone(await server._money_gate(peer.context(), original))
        shown = peer.seen[0][0]
        for line in original.splitlines():
            self.assertIn(line, shown)
        self.assertLess(len(shown), 300)  # TTL enforced in execution, not UI boilerplate


if __name__ == "__main__":
    unittest.main()
