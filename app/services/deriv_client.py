from __future__ import annotations

import json
from typing import Any

import websockets
from websockets.legacy.client import WebSocketClientProtocol


TRADING_OPERATION_KEYS = {
    "buy",
    "sell",
    "proposal",
    "proposal_open_contract",
    "contract_update",
    "cancel",
}


class DerivWebSocketClient:
    """Small read-only wrapper around Deriv's public market-data WebSocket."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def connect(self) -> WebSocketClientProtocol:
        return await websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_queue=1024,
        )

    async def send(self, websocket: WebSocketClientProtocol, payload: dict[str, Any]) -> None:
        blocked = TRADING_OPERATION_KEYS.intersection(payload)
        if blocked:
            blocked_keys = ", ".join(sorted(blocked))
            raise ValueError(f"Trading operation blocked in scan-only client: {blocked_keys}")

        await websocket.send(json.dumps(payload))
