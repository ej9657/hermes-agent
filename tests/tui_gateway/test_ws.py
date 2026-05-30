"""Tests for the browser WebSocket transport."""

from __future__ import annotations

import pytest

from tui_gateway.ws import handle_ws


class EarlyDisconnectWebSocket:
    def __init__(self, *, fail_on_accept: bool = False) -> None:
        self.fail_on_accept = fail_on_accept
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        if self.fail_on_accept:
            raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')
        self.accepted = True

    async def send_text(self, _line: str) -> None:
        return None

    async def receive_text(self) -> str:
        raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_handle_ws_ignores_client_disconnect_before_accept():
    ws = EarlyDisconnectWebSocket(fail_on_accept=True)

    await handle_ws(ws)

    assert not ws.accepted


@pytest.mark.asyncio
async def test_handle_ws_ignores_client_disconnect_after_accept():
    ws = EarlyDisconnectWebSocket()

    await handle_ws(ws)

    assert ws.accepted is True
    assert ws.closed is True
