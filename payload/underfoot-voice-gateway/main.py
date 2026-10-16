from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

PROJECT_NAME = os.getenv("UNDERFOOT_PROJECT_NAME", "underfoot")
WORLD_SERVICE_URL = (os.getenv("UNDERFOOT_WORLD_SERVICE_URL", "http://underfoot-world:8460") or "http://underfoot-world:8460").rstrip("/")
VOICE_JOIN_SECRET = os.getenv("UNDERFOOT_VOICE_JOIN_SECRET", "")
GATEWAY_PORT = int(os.getenv("UNDERFOOT_VOICE_GATEWAY_PORT", "8470"))


def now_ts() -> int:
    return int(time.time())


def decode_voice_token(raw_token: str) -> dict[str, Any]:
    if len(VOICE_JOIN_SECRET) < 32:
        raise HTTPException(status_code=503, detail="voice_join_secret_not_configured")
    clean = (raw_token or "").strip()
    if "." not in clean:
        raise HTTPException(status_code=403, detail="Malformed voice token.")

    encoded, signature = clean.split(".", 1)
    padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
    try:
        body = base64.urlsafe_b64decode(padded.encode("ascii"))
        expected = hmac.new(VOICE_JOIN_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=403, detail="Invalid voice token signature.")
        payload = json.loads(body.decode("utf-8"))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=403, detail=f"Invalid voice token payload: {exc}") from exc

    if int(payload.get("expiresAtEpoch", 0) or 0) < now_ts():
        raise HTTPException(status_code=403, detail="Voice token expired.")
    return payload


@dataclass
class PeerConnectionState:
    peer_id: str
    display_name: str
    role: str
    team_id: int
    websocket: WebSocket
    session_id: str
    connected_at: int
    packet_loss_percent: float = 0.0
    jitter_ms: float = 0.0
    is_speaking: bool = False
    has_input: bool = False
    device_lost: bool = False
    last_signal_at: int = field(default_factory=now_ts)
    subscriptions: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "peerId": self.peer_id,
            "displayName": self.display_name,
            "role": self.role,
            "teamId": self.team_id,
            "sessionId": self.session_id,
            "connectedAtEpoch": self.connected_at,
            "packetLossPercent": self.packet_loss_percent,
            "jitterMs": self.jitter_ms,
            "isSpeaking": self.is_speaking,
            "hasInput": self.has_input,
            "deviceLost": self.device_lost,
            "lastSignalAtEpoch": self.last_signal_at,
            "subscriptions": list(self.subscriptions),
        }


@dataclass
class MatchVoiceRoom:
    match_id: str
    peers: dict[str, PeerConnectionState] = field(default_factory=dict)
    dropped_peers: int = 0
    audio_frames_relayed: int = 0
    audio_bytes_relayed: int = 0
    updated_at: int = field(default_factory=now_ts)
    last_stats_publish_at: int = 0

    def stats(self) -> dict[str, Any]:
        peer_rows = list(self.peers.values())
        connected_peers = len(peer_rows)
        speaking_peers = sum(1 for row in peer_rows if row.is_speaking)
        avg_packet_loss = round(sum(row.packet_loss_percent for row in peer_rows) / connected_peers, 2) if connected_peers else 0.0
        avg_jitter = round(sum(row.jitter_ms for row in peer_rows) / connected_peers, 2) if connected_peers else 0.0
        return {
            "matchId": self.match_id,
            "activePeers": len(peer_rows),
            "connectedPeers": connected_peers,
            "speakingPeers": speaking_peers,
            "droppedPeers": self.dropped_peers,
            "averagePacketLossPercent": avg_packet_loss,
            "averageJitterMs": avg_jitter,
            "averageJitterMilliseconds": avg_jitter,
            "audioFramesRelayed": self.audio_frames_relayed,
            "audioBytesRelayed": self.audio_bytes_relayed,
            "updatedAtEpoch": self.updated_at,
        }


class VoiceGatewayState:
    def __init__(self) -> None:
        self.rooms: dict[str, MatchVoiceRoom] = {}
        self._lock = asyncio.Lock()

    async def ensure_room(self, match_id: str) -> MatchVoiceRoom:
        async with self._lock:
            room = self.rooms.get(match_id)
            if room is None:
                room = MatchVoiceRoom(match_id=match_id)
                self.rooms[match_id] = room
            return room

    async def register_peer(self, match_id: str, peer: PeerConnectionState) -> MatchVoiceRoom:
        room = await self.ensure_room(match_id)
        async with self._lock:
            room.peers[peer.peer_id] = peer
            room.updated_at = now_ts()
            return room

    async def unregister_peer(self, match_id: str, peer_id: str) -> MatchVoiceRoom | None:
        async with self._lock:
            room = self.rooms.get(match_id)
            if room is None:
                return None
            if peer_id in room.peers:
                room.peers.pop(peer_id, None)
                room.dropped_peers += 1
                room.updated_at = now_ts()
            return room


state = VoiceGatewayState()
app = FastAPI(title="Underfoot Voice Gateway", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def publish_room_stats(match_id: str) -> None:
    room = await state.ensure_room(match_id)
    payload = room.stats()
    async with httpx.AsyncClient(timeout=4.0) as client:
        try:
            await client.post(f"{WORLD_SERVICE_URL}/api/matches/{match_id}/voice/gateway-stats", json=payload)
            room.last_stats_publish_at = now_ts()
        except Exception:
            return


async def maybe_publish_room_stats(match_id: str, min_interval_seconds: int = 1) -> None:
    room = await state.ensure_room(match_id)
    if now_ts() - int(room.last_stats_publish_at or 0) < max(1, min_interval_seconds):
        return
    await publish_room_stats(match_id)


async def broadcast(match_id: str, message: dict[str, Any], exclude_peer_id: str | None = None) -> None:
    room = await state.ensure_room(match_id)
    dead_peers: list[str] = []
    for peer_id, peer in room.peers.items():
        if exclude_peer_id and peer_id == exclude_peer_id:
            continue
        try:
            await peer.websocket.send_json(message)
        except Exception:
            dead_peers.append(peer_id)

    for dead_peer_id in dead_peers:
        await state.unregister_peer(match_id, dead_peer_id)


@app.get("/health")
async def health() -> dict[str, Any]:
    rooms = list(state.rooms.values())
    return {
        "status": "ok",
        "project": PROJECT_NAME,
        "gatewayPort": GATEWAY_PORT,
        "activeMatches": len(rooms),
        "activePeers": sum(len(room.peers) for room in rooms),
    }


@app.get("/api/voice/overview")
async def voice_overview() -> dict[str, Any]:
    rooms = list(state.rooms.values())
    return {
        "status": "ok",
        "matches": [room.stats() for room in rooms],
        "activeMatches": len(rooms),
        "activePeers": sum(len(room.peers) for room in rooms),
        "worldServiceUrl": WORLD_SERVICE_URL,
    }


@app.get("/api/matches/{match_id}/stats")
async def match_stats(match_id: str) -> dict[str, Any]:
    room = await state.ensure_room(match_id)
    return {"status": "ok", "stats": room.stats()}


@app.get("/api/matches/{match_id}/sessions")
async def match_sessions(match_id: str) -> dict[str, Any]:
    room = await state.ensure_room(match_id)
    return {"status": "ok", "peers": [peer.snapshot() for peer in room.peers.values()], "stats": room.stats()}


@app.websocket("/ws/signaling/{match_id}/{peer_id}")
async def signaling_socket(
    websocket: WebSocket,
    match_id: str,
    peer_id: str,
    token: str = Query(default=""),
) -> None:
    payload = decode_voice_token(token)
    if str(payload.get("matchId", "")).strip() != (match_id or "").strip():
        await websocket.close(code=4403, reason="Voice token match mismatch.")
        return
    if str(payload.get("playerId", "")).strip() != (peer_id or "").strip():
        await websocket.close(code=4403, reason="Voice token player mismatch.")
        return

    await websocket.accept()
    peer = PeerConnectionState(
        peer_id=peer_id,
        display_name=str(payload.get("displayName", peer_id)),
        role=str(payload.get("role", "warble")),
        team_id=int(payload.get("teamId", -1)),
        websocket=websocket,
        session_id=str(payload.get("sessionId", f"voice_{match_id}_{peer_id}")),
        connected_at=now_ts(),
    )
    room = await state.register_peer(match_id, peer)
    await broadcast(
        match_id,
        {
            "type": "peer_joined",
            "peer": peer.snapshot(),
            "stats": room.stats(),
        },
        exclude_peer_id=peer_id,
    )
    await websocket.send_json(
        {
            "type": "welcome",
            "peerId": peer_id,
            "matchId": match_id,
            "stats": room.stats(),
            "peers": [item.snapshot() for item in room.peers.values() if item.peer_id != peer_id],
        }
    )
    asyncio.create_task(publish_room_stats(match_id))

    try:
        while True:
            message = await websocket.receive_json()
            peer.last_signal_at = now_ts()
            message_type = str(message.get("type", "")).strip().lower()
            if message_type in {"offer", "answer", "ice"}:
                target_peer_id = str(message.get("targetPeerId", "")).strip()
                target = room.peers.get(target_peer_id)
                if target is None:
                    await websocket.send_json({"type": "error", "detail": f"Unknown peer target: {target_peer_id}"})
                    continue
                await target.websocket.send_json(
                    {
                        "type": message_type,
                        "sourcePeerId": peer_id,
                        "targetPeerId": target_peer_id,
                        "payload": message.get("payload", {}),
                    }
                )
            elif message_type == "stats":
                peer.packet_loss_percent = max(0.0, float(message.get("packetLossPercent", 0.0) or 0.0))
                peer.jitter_ms = max(0.0, float(message.get("jitterMs", 0.0) or 0.0))
                peer.has_input = bool(message.get("hasInput", False))
                peer.device_lost = bool(message.get("deviceLost", False))
                room.updated_at = now_ts()
                asyncio.create_task(publish_room_stats(match_id))
            elif message_type == "speaking":
                peer.is_speaking = bool(message.get("isSpeaking", False))
                peer.has_input = bool(message.get("hasInput", peer.has_input))
                room.updated_at = now_ts()
                await broadcast(
                    match_id,
                    {
                        "type": "speaking",
                        "sourcePeerId": peer_id,
                        "isSpeaking": peer.is_speaking,
                    },
                    exclude_peer_id=peer_id,
                )
                asyncio.create_task(publish_room_stats(match_id))
            elif message_type == "subscription_graph":
                raw_subscriptions = message.get("subscriptions", [])
                peer.subscriptions = [str(item).strip() for item in raw_subscriptions if str(item).strip()]
                room.updated_at = now_ts()
            elif message_type == "ping":
                await websocket.send_json({"type": "pong", "ts": now_ts()})
            elif message_type == "audio_frame":
                payload = message.get("payload", "")
                sample_rate = int(message.get("sampleRate", 16000) or 16000)
                channels = int(message.get("channels", 1) or 1)
                sequence_id = int(message.get("sequenceId", 0) or 0)
                room.updated_at = now_ts()
                target_peer_ids = peer.subscriptions if peer.subscriptions else [item for item in room.peers.keys() if item != peer_id]
                forwarded = 0
                for target_peer_id in target_peer_ids:
                    if target_peer_id == peer_id:
                        continue
                    target = room.peers.get(target_peer_id)
                    if target is None:
                        continue
                    await target.websocket.send_json(
                        {
                            "type": "audio_frame",
                            "sourcePeerId": peer_id,
                            "sampleRate": sample_rate,
                            "channels": channels,
                            "sequenceId": sequence_id,
                            "payload": payload,
                        }
                    )
                    forwarded += 1
                if forwarded > 0:
                    room.audio_frames_relayed += forwarded
                    room.audio_bytes_relayed += len(str(payload).encode("utf-8")) * forwarded
                    asyncio.create_task(maybe_publish_room_stats(match_id))
            else:
                await websocket.send_json({"type": "error", "detail": f"Unknown message type: {message_type or 'empty'}"})
    except WebSocketDisconnect:
        await state.unregister_peer(match_id, peer_id)
        await broadcast(match_id, {"type": "peer_left", "peerId": peer_id})
        asyncio.create_task(publish_room_stats(match_id))


install_runtime_guard(app, 'underfoot-voice-gateway', require_auth=False)
