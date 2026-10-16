from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json
import hashlib
import logging
import os
import secrets
import shlex
import socket
import time
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


logging.basicConfig(level=os.getenv("UNDERFOOT_LOG_LEVEL", "INFO").upper())
LOGGER = logging.getLogger("underfoot.match_allocator")

ALLOCATOR_PORT = int(os.getenv("UNDERFOOT_ALLOCATOR_PORT", "8485"))
WORLD_SERVICE_URL = (os.getenv("UNDERFOOT_ALLOCATOR_WORLD_SERVICE_URL", "http://underfoot-world:8460") or "http://underfoot-world:8460").strip().rstrip("/")
PUBLIC_HOST = os.getenv("UNDERFOOT_ALLOCATOR_PUBLIC_HOST", "").strip()
DOCKER_NETWORK = os.getenv("UNDERFOOT_ALLOCATOR_DOCKER_NETWORK", "luminax_default").strip()
INSTANCE_IMAGE = (os.getenv("UNDERFOOT_DEDICATED_INSTANCE_IMAGE", "underfoot-dedicated-runtime:latest") or "underfoot-dedicated-runtime:latest").strip()
INSTANCE_PREFIX = (os.getenv("UNDERFOOT_ALLOCATOR_INSTANCE_PREFIX", "underfoot-instance") or "underfoot-instance").strip()
MATCH_PORT_RANGE = (os.getenv("UNDERFOOT_ALLOCATOR_MATCH_PORT_RANGE", "27000-27063") or "27000-27063").strip()
ROOM_PORT_RANGE = (os.getenv("UNDERFOOT_ALLOCATOR_ROOM_PORT_RANGE", "27100-27163") or "27100-27163").strip()
MATCH_INTERNAL_PORT = int(os.getenv("UNDERFOOT_DEDICATED_INTERNAL_MATCH_PORT", "7777"))
ROOM_INTERNAL_PORT = int(os.getenv("UNDERFOOT_DEDICATED_INTERNAL_ROOM_PORT", "7778"))
IMAGE_AUTO_BUILD = feature_enabled("UNDERFOOT_DEDICATED_IMAGE_AUTO_BUILD")
IMAGE_BUILD_CONTEXT = (os.getenv("UNDERFOOT_DEDICATED_IMAGE_BUILD_CONTEXT", "/data/project") or "/data/project").strip()
IMAGE_BUILD_DOCKERFILE = (os.getenv("UNDERFOOT_DEDICATED_IMAGE_DOCKERFILE", "server/dedicated_runtime/Dockerfile") or "server/dedicated_runtime/Dockerfile").strip()
IMAGE_BUILD_ARGUMENTS_RAW = (os.getenv("UNDERFOOT_DEDICATED_IMAGE_BUILD_ARGUMENTS", "{\"UNDERFOOT_SERVER_BUILD_ROOT\":\"unity/Builds/LinuxDedicatedServer\"}") or "{\"UNDERFOOT_SERVER_BUILD_ROOT\":\"unity/Builds/LinuxDedicatedServer\"}").strip()
DEFAULT_HEARTBEAT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_HEARTBEAT_SECONDS", "10"))
DEFAULT_RECONNECT_GRACE_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_RECONNECT_GRACE_SECONDS", "90"))
DEFAULT_ROOM_IDLE_TIMEOUT_SECONDS = int(os.getenv("UNDERFOOT_DEDICATED_ROOM_IDLE_TIMEOUT_SECONDS", "300"))
INSTANCE_COMMAND_RAW = (os.getenv("UNDERFOOT_ALLOCATOR_INSTANCE_COMMAND", "") or "").strip()
PROX_VOICE_ONLY = os.getenv("UNDERFOOT_PROX_VOICE_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}
SHOW_NAMEPLATES = os.getenv("UNDERFOOT_SHOW_NAMEPLATES", "false").strip().lower() in {"1", "true", "yes", "on"}
VOICE_TRANSPORT_MODE = (os.getenv("UNDERFOOT_VOICE_TRANSPORT_MODE", "unity_webrtc_sfu") or "unity_webrtc_sfu").strip()


def now_ts() -> int:
    return int(time.time())


def parse_port_range(raw_value: str) -> tuple[int, int]:
    cleaned = (raw_value or "").strip()
    if "-" in cleaned:
        start_raw, end_raw = cleaned.split("-", 1)
        start_port = int(start_raw)
        end_port = int(end_raw)
    else:
        start_port = int(cleaned or "0")
        end_port = start_port
    if start_port <= 0 or end_port < start_port:
        raise ValueError(f"Invalid port range: {raw_value}")
    return start_port, end_port


def parse_command(raw_value: str) -> list[str] | None:
    if not raw_value:
        return None
    if raw_value.startswith("["):
        decoded = json.loads(raw_value)
        if isinstance(decoded, list):
            return [str(item) for item in decoded if str(item).strip()]
    parsed = shlex.split(raw_value)
    return parsed or None


def parse_build_arguments(raw_value: str) -> dict[str, str]:
    if not raw_value:
        return {}
    try:
        decoded = json.loads(raw_value)
    except Exception as exc:
        raise ValueError(f"Invalid UNDERFOOT_DEDICATED_IMAGE_BUILD_ARGUMENTS JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("UNDERFOOT_DEDICATED_IMAGE_BUILD_ARGUMENTS must decode to an object.")
    return {str(key): str(value) for key, value in decoded.items()}


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


class CreateInstanceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    instance_kind: str = Field(alias="instanceKind")
    match_id: str = Field(alias="matchId")
    room_code: str = Field(default="", alias="roomCode")
    map_id: str = Field(alias="mapId")
    listing_type: str = Field(alias="listingType")
    boot_scene_key: str = Field(alias="bootSceneKey")
    boot_mode_key: str = Field(alias="bootModeKey")
    voice_mode: str = Field(alias="voiceMode")
    team_count: int = Field(default=3, alias="teamCount")
    team_size: int = Field(default=6, alias="teamSize")
    max_players: int = Field(default=20, alias="maxPlayers")
    biggun_slots: int = Field(default=1, alias="biggunSlots")
    requested_port: int = Field(default=7777, alias="requestedPort")
    public_host: str = Field(default="", alias="publicHost")
    join_ticket: str = Field(default="", alias="joinTicket")
    reconnect_grace_seconds: int = Field(default=DEFAULT_RECONNECT_GRACE_SECONDS, alias="reconnectGraceSeconds")
    idle_timeout_seconds: int = Field(default=DEFAULT_ROOM_IDLE_TIMEOUT_SECONDS, alias="idleTimeoutSeconds")


class StopInstanceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    remove: bool = Field(default=True, alias="remove")


class InstanceRegistry:
    def __init__(self) -> None:
        self._docker: docker.DockerClient | None = None
        self._instances: dict[str, dict[str, Any]] = {}
        self._route_index: dict[str, str] = {}
        self._command = parse_command(INSTANCE_COMMAND_RAW)
        self._build_arguments = parse_build_arguments(IMAGE_BUILD_ARGUMENTS_RAW)
        self._runtime_build_metadata_cache: dict[str, str] | None = None
        self._runtime_build_metadata_cache_key = ""
        self.reconcile()

    def docker_client(self) -> docker.DockerClient:
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    def reconcile(self) -> None:
        try:
            client = self.docker_client()
            containers = client.containers.list(all=True, filters={"label": "olympus.underfoot.instance=true"})
        except DockerException as exc:
            LOGGER.warning("Underfoot allocator could not reach Docker during reconcile: %s", exc)
            return

        self._instances.clear()
        self._route_index.clear()
        for container in containers:
            labels = container.labels or {}
            instance_id = str(labels.get("olympus.underfoot.instance_id", "")).strip()
            if not instance_id:
                continue
            record = self._build_record_from_container(container)
            self._instances[instance_id] = record
            route_key = str(record.get("route_key", "")).strip()
            if route_key:
                self._route_index[route_key] = instance_id

    def _resolve_runtime_build_root(self) -> Path:
        configured = str(self._build_arguments.get("UNDERFOOT_SERVER_BUILD_ROOT", "unity/Builds/LinuxDedicatedServer")).strip()
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = Path(IMAGE_BUILD_CONTEXT) / candidate
        return candidate.resolve()

    def _resolve_packages_lock_path(self, build_root: Path) -> Path:
        candidates = [
            Path(IMAGE_BUILD_CONTEXT) / "unity" / "Underfoot" / "Packages" / "packages-lock.json",
            build_root.parent.parent / "Underfoot" / "Packages" / "packages-lock.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return candidates[0].resolve()

    @staticmethod
    def _compute_short_sha256(path: Path) -> str:
        if not path.exists():
            return ""
        sha = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()[:16]

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _resolve_runtime_build_metadata(self) -> dict[str, str]:
        build_root = self._resolve_runtime_build_root()
        build_info_path = build_root / "UnderfootDedicatedServer.buildinfo.json"
        assembly_path = build_root / "UnderfootDedicatedServer_Data" / "Managed" / "Assembly-CSharp.dll"
        packages_lock_path = self._resolve_packages_lock_path(build_root)
        cache_key = "|".join(
            (
                str(build_info_path.resolve() if build_info_path.exists() else build_info_path),
                str(build_info_path.stat().st_mtime_ns if build_info_path.exists() else 0),
                str(assembly_path.resolve() if assembly_path.exists() else assembly_path),
                str(assembly_path.stat().st_mtime_ns if assembly_path.exists() else 0),
                str(packages_lock_path.resolve() if packages_lock_path.exists() else packages_lock_path),
                str(packages_lock_path.stat().st_mtime_ns if packages_lock_path.exists() else 0),
            )
        )
        if self._runtime_build_metadata_cache is not None and self._runtime_build_metadata_cache_key == cache_key:
            return dict(self._runtime_build_metadata_cache)

        build_info = self._read_json(build_info_path)
        packages_lock = self._read_json(packages_lock_path)
        dependencies = packages_lock.get("dependencies", {}) if isinstance(packages_lock, dict) else {}
        ngo_version = ""
        transport_version = ""
        if isinstance(dependencies, dict):
            ngo_row = dependencies.get("com.unity.netcode.gameobjects", {})
            transport_row = dependencies.get("com.unity.transport", {})
            if isinstance(ngo_row, dict):
                ngo_version = str(ngo_row.get("version", "")).strip()
            if isinstance(transport_row, dict):
                transport_version = str(transport_row.get("version", "")).strip()

        timestamp_utc = str(build_info.get("timestampUtc", "")).strip()
        if not timestamp_utc and assembly_path.exists():
            timestamp_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(assembly_path.stat().st_mtime))

        package_version_segments = []
        if ngo_version:
            package_version_segments.append(f"ngo={ngo_version}")
        if transport_version:
            package_version_segments.append(f"utp={transport_version}")
        package_version = "|".join(package_version_segments)
        assembly_hash = self._compute_short_sha256(assembly_path)
        fingerprint = ""
        if timestamp_utc or assembly_hash or package_version:
            fingerprint = f"ts={timestamp_utc}|asm={assembly_hash}|{package_version}".rstrip("|")

        metadata = {
            "runtimeBuildFingerprint": fingerprint,
            "runtimeBuildTimestampUtc": timestamp_utc,
            "runtimePackageVersion": package_version,
            "runtimeCompatibilityState": "ready" if fingerprint else "unknown",
            "runtimeBuildRoot": str(build_root),
            "runtimePackagesLockPath": str(packages_lock_path),
        }
        self._runtime_build_metadata_cache = dict(metadata)
        self._runtime_build_metadata_cache_key = cache_key
        return metadata

    @staticmethod
    def _read_image_runtime_metadata(image: docker.models.images.Image | None) -> dict[str, str]:
        labels = {}
        if image is not None:
            labels = image.labels or image.attrs.get("Config", {}).get("Labels", {}) or {}
        return {
            "runtimeBuildFingerprint": str(labels.get("olympus.underfoot.runtime_build_fingerprint", "")).strip(),
            "runtimeBuildTimestampUtc": str(labels.get("olympus.underfoot.runtime_build_timestamp_utc", "")).strip(),
            "runtimePackageVersion": str(labels.get("olympus.underfoot.runtime_package_version", "")).strip(),
        }

    def _resolve_runtime_image_compatibility_state(self, image: docker.models.images.Image | None) -> str:
        source = self._resolve_runtime_build_metadata()
        if not source["runtimeBuildFingerprint"]:
            return "unknown"
        if image is None:
            return "image_missing"
        image_metadata = self._read_image_runtime_metadata(image)
        if not image_metadata["runtimeBuildFingerprint"]:
            return "unstamped"
        return "aligned" if image_metadata["runtimeBuildFingerprint"] == source["runtimeBuildFingerprint"] else "mismatch"

    def _build_record_from_container(self, container: docker.models.containers.Container) -> dict[str, Any]:
        labels = container.labels or {}
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        instance_kind = str(labels.get("olympus.underfoot.instance_kind", "combat_match")).strip() or "combat_match"
        internal_port = ROOM_INTERNAL_PORT if instance_kind == "private_room" else MATCH_INTERNAL_PORT
        tcp_bindings = ports.get(f"{internal_port}/tcp") or []
        udp_bindings = ports.get(f"{internal_port}/udp") or []
        host_binding = tcp_bindings[0] if tcp_bindings else udp_bindings[0] if udp_bindings else {}
        public_port = int(host_binding.get("HostPort", "0") or 0)
        status = container.status or "unknown"
        record = {
            "instanceId": str(labels.get("olympus.underfoot.instance_id", container.name)).strip(),
            "serverId": str(labels.get("olympus.underfoot.server_id", labels.get("olympus.underfoot.instance_id", container.name))).strip(),
            "instanceKind": instance_kind,
            "matchId": str(labels.get("olympus.underfoot.match_id", "")).strip(),
            "roomCode": str(labels.get("olympus.underfoot.room_code", "")).strip(),
            "route_key": str(labels.get("olympus.underfoot.route_key", "")).strip(),
            "containerId": container.id,
            "containerName": container.name,
            "publicHost": str(labels.get("olympus.underfoot.public_host", PUBLIC_HOST)).strip() or PUBLIC_HOST,
            "publicPort": public_port,
            "internalPort": internal_port,
            "status": status,
            "isReady": status == "running",
            "transport": "ngo_utp",
            "summary": f"{'Room' if instance_kind == 'private_room' else 'Match'} instance {container.name} on {PUBLIC_HOST}:{public_port}",
            "createdAtEpoch": int(labels.get("olympus.underfoot.created_at_epoch", now_ts())),
            "updatedAtEpoch": now_ts(),
            "reconnectGraceExpiresAtEpoch": int(labels.get("olympus.underfoot.reconnect_grace_expires_at_epoch", now_ts() + DEFAULT_RECONNECT_GRACE_SECONDS)),
            "idleTimeoutSeconds": int(labels.get("olympus.underfoot.idle_timeout_seconds", DEFAULT_ROOM_IDLE_TIMEOUT_SECONDS)),
            "runtimeBuildFingerprint": str(labels.get("olympus.underfoot.runtime_build_fingerprint", "")).strip(),
            "runtimeBuildTimestampUtc": str(labels.get("olympus.underfoot.runtime_build_timestamp_utc", "")).strip(),
            "runtimeCompatibilityState": str(labels.get("olympus.underfoot.runtime_compatibility_state", "unknown")).strip() or "unknown",
            "bootContext": {
                "bootSceneKey": str(labels.get("olympus.underfoot.boot_scene_key", "")).strip(),
                "bootModeKey": str(labels.get("olympus.underfoot.boot_mode_key", "")).strip(),
                "voiceMode": str(labels.get("olympus.underfoot.voice_mode", "")).strip(),
                "listingType": str(labels.get("olympus.underfoot.listing_type", "")).strip(),
            },
        }
        return record

    def _reserve_host_port(self, instance_kind: str) -> int:
        start_port, end_port = parse_port_range(ROOM_PORT_RANGE if instance_kind == "private_room" else MATCH_PORT_RANGE)
        used_ports = {
            int(record.get("publicPort", 0))
            for record in self._instances.values()
            if str(record.get("status", "")).strip().lower() not in {"exited", "removed", "stopped"}
        }
        for port in range(start_port, end_port + 1):
            if port in used_ports:
                continue
            if is_port_available(port):
                return port
        raise HTTPException(status_code=503, detail=f"No free Underfoot {instance_kind} host ports remain in the configured allocator range.")

    def _sanitize_container_name(self, instance_id: str) -> str:
        clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in instance_id.lower())
        return f"{INSTANCE_PREFIX}-{clean}"[:63]

    def _build_container_environment(self, req: CreateInstanceRequest, instance_id: str, host_port: int) -> dict[str, str]:
        is_room = req.instance_kind == "private_room"
        public_host = req.public_host.strip() or PUBLIC_HOST
        world_host = WORLD_SERVICE_URL.replace("http://", "").replace("https://", "")
        runtime_metadata = self._resolve_runtime_build_metadata()
        return {
            "UNDERFOOT_WORLD_SERVICE_URL": WORLD_SERVICE_URL,
            "UNDERFOOT_WORLD_HOST": world_host,
            "UNDERFOOT_NETCODE_AUTO_HOST": "false",
            "UNDERFOOT_ALLOW_LOCAL_SANDBOX_MATCH": "false",
            "UNDERFOOT_NETCODE_ADDRESS": "0.0.0.0",
            "UNDERFOOT_NETCODE_PORT": str(ROOM_INTERNAL_PORT if is_room else MATCH_INTERNAL_PORT),
            "UNDERFOOT_DEDICATED_SERVER_ID": instance_id,
            "UNDERFOOT_DEDICATED_SERVER_LABEL": f"{'Room' if is_room else 'Match'} {req.match_id or req.room_code or instance_id}",
            "UNDERFOOT_DEDICATED_DEFAULT_PUBLIC_HOST": public_host,
            "UNDERFOOT_DEDICATED_DEFAULT_MATCH_PORT": str(host_port if not is_room else MATCH_INTERNAL_PORT),
            "UNDERFOOT_DEDICATED_DEFAULT_ROOM_PORT": str(host_port if is_room else ROOM_INTERNAL_PORT),
            "UNDERFOOT_DEDICATED_DEFAULT_MAX_ASSIGNMENTS": "1",
            "UNDERFOOT_DEDICATED_HEARTBEAT_SECONDS": str(DEFAULT_HEARTBEAT_SECONDS),
            "UNDERFOOT_DEDICATED_AUTO_REGISTER": "true",
            "UNDERFOOT_DEDICATED_BOOT_MATCH_ID": req.match_id,
            "UNDERFOOT_DEDICATED_BOOT_MAP_ID": req.map_id,
            "UNDERFOOT_DEDICATED_BOOT_MODE_KEY": req.boot_mode_key,
            "UNDERFOOT_DEDICATED_BOOT_JOIN_TICKET": req.join_ticket.strip(),
            "UNDERFOOT_DEDICATED_INSTANCE_KIND": req.instance_kind,
            "UNDERFOOT_DEDICATED_BOOT_ROOM_CODE": req.room_code,
            "UNDERFOOT_DEFAULT_MAP_ID": req.map_id,
            "UNDERFOOT_DEFAULT_MODE_KEY": req.boot_mode_key,
            "UNDERFOOT_MAX_PLAYERS": str(req.max_players),
            "UNDERFOOT_MAX_BIGGUNS": str(req.biggun_slots),
            "UNDERFOOT_PROX_VOICE_ONLY": "true" if PROX_VOICE_ONLY else "false",
            "UNDERFOOT_SHOW_NAMEPLATES": "true" if SHOW_NAMEPLATES else "false",
            "UNDERFOOT_VOICE_TRANSPORT_MODE": VOICE_TRANSPORT_MODE,
            "UNDERFOOT_RUNTIME_BUILD_FINGERPRINT": runtime_metadata["runtimeBuildFingerprint"],
            "UNDERFOOT_RUNTIME_BUILD_TIMESTAMP_UTC": runtime_metadata["runtimeBuildTimestampUtc"],
            "UNDERFOOT_RUNTIME_PACKAGE_VERSION": runtime_metadata["runtimePackageVersion"],
        }

    def ensure_runtime_image(self) -> None:
        runtime_metadata = self._resolve_runtime_build_metadata()
        expected_fingerprint = runtime_metadata["runtimeBuildFingerprint"]
        image: docker.models.images.Image | None = None
        try:
            image = self.docker_client().images.get(INSTANCE_IMAGE)
            if self._resolve_runtime_image_compatibility_state(image) == "aligned":
                return
            if not IMAGE_AUTO_BUILD:
                raise HTTPException(
                    status_code=503,
                    detail=f"Underfoot dedicated runtime image {INSTANCE_IMAGE} does not match the current dedicated build fingerprint {expected_fingerprint}.",
                )
        except ImageNotFound:
            if not IMAGE_AUTO_BUILD:
                raise HTTPException(
                    status_code=503,
                    detail=f"Underfoot dedicated runtime image {INSTANCE_IMAGE} is missing and auto-build is disabled.",
                )
        except DockerException as exc:
            raise HTTPException(status_code=503, detail=f"Underfoot allocator could not inspect runtime images: {exc}") from exc

        try:
            build_arguments = dict(self._build_arguments)
            build_arguments["UNDERFOOT_RUNTIME_BUILD_FINGERPRINT"] = runtime_metadata["runtimeBuildFingerprint"]
            build_arguments["UNDERFOOT_RUNTIME_BUILD_TIMESTAMP_UTC"] = runtime_metadata["runtimeBuildTimestampUtc"]
            build_arguments["UNDERFOOT_RUNTIME_PACKAGE_VERSION"] = runtime_metadata["runtimePackageVersion"]
            LOGGER.info(
                "Underfoot allocator auto-building runtime image %s from context %s and dockerfile %s using fingerprint %s",
                INSTANCE_IMAGE,
                IMAGE_BUILD_CONTEXT,
                IMAGE_BUILD_DOCKERFILE,
                expected_fingerprint or "unstamped",
            )
            self.docker_client().images.build(
                path=IMAGE_BUILD_CONTEXT,
                dockerfile=IMAGE_BUILD_DOCKERFILE,
                tag=INSTANCE_IMAGE,
                rm=True,
                pull=False,
                buildargs=build_arguments,
            )
        except DockerException as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Underfoot allocator could not auto-build the runtime image {INSTANCE_IMAGE}: {exc}",
            ) from exc

    def create_instance(self, req: CreateInstanceRequest) -> dict[str, Any]:
        self.reconcile()
        route_key = (req.room_code.strip().upper() if req.room_code.strip() else req.match_id.strip())
        if not route_key:
            raise HTTPException(status_code=400, detail="Route key is required for Underfoot instance allocation.")

        existing_id = self._route_index.get(route_key)
        if existing_id:
            existing = self.get_instance(existing_id)
            if str(existing.get("status", "")).strip().lower() in {"running", "created", "restarting"}:
                return existing

        host_port = self._reserve_host_port(req.instance_kind)
        self.ensure_runtime_image()
        instance_id = f"uf_{req.instance_kind}_{secrets.token_hex(6)}"
        container_name = self._sanitize_container_name(instance_id)
        environment = self._build_container_environment(req, instance_id, host_port)
        runtime_metadata = self._resolve_runtime_build_metadata()
        labels = {
            "olympus.underfoot.instance": "true",
            "olympus.underfoot.instance_id": instance_id,
            "olympus.underfoot.server_id": instance_id,
            "olympus.underfoot.instance_kind": req.instance_kind,
            "olympus.underfoot.match_id": req.match_id,
            "olympus.underfoot.room_code": req.room_code.strip().upper(),
            "olympus.underfoot.route_key": route_key,
            "olympus.underfoot.public_host": req.public_host.strip() or PUBLIC_HOST,
            "olympus.underfoot.created_at_epoch": str(now_ts()),
            "olympus.underfoot.reconnect_grace_expires_at_epoch": str(now_ts() + max(30, int(req.reconnect_grace_seconds))),
            "olympus.underfoot.idle_timeout_seconds": str(max(30, int(req.idle_timeout_seconds))),
            "olympus.underfoot.boot_scene_key": req.boot_scene_key,
            "olympus.underfoot.boot_mode_key": req.boot_mode_key,
            "olympus.underfoot.voice_mode": req.voice_mode,
            "olympus.underfoot.listing_type": req.listing_type,
            "olympus.underfoot.runtime_build_fingerprint": runtime_metadata["runtimeBuildFingerprint"],
            "olympus.underfoot.runtime_build_timestamp_utc": runtime_metadata["runtimeBuildTimestampUtc"],
            "olympus.underfoot.runtime_package_version": runtime_metadata["runtimePackageVersion"],
            "olympus.underfoot.runtime_compatibility_state": runtime_metadata["runtimeCompatibilityState"],
        }
        internal_port = ROOM_INTERNAL_PORT if req.instance_kind == "private_room" else MATCH_INTERNAL_PORT
        port_bindings = {
            f"{internal_port}/udp": host_port,
            f"{internal_port}/tcp": host_port,
        }

        try:
            client = self.docker_client()
            container = client.containers.run(
                INSTANCE_IMAGE,
                detach=True,
                name=container_name,
                network=DOCKER_NETWORK or None,
                environment=environment,
                labels=labels,
                ports=port_bindings,
                command=self._command,
            )
        except DockerException as exc:
            raise HTTPException(status_code=503, detail=f"Underfoot allocator could not start the instance container: {exc}") from exc

        record = self._build_record_from_container(container)
        self._instances[instance_id] = record
        self._route_index[route_key] = instance_id
        return record

    def get_instance(self, instance_id: str) -> dict[str, Any]:
        clean_instance_id = (instance_id or "").strip()
        if not clean_instance_id:
            raise HTTPException(status_code=400, detail="Instance id is required.")

        self.reconcile()
        record = self._instances.get(clean_instance_id)
        if record:
            return record
        raise HTTPException(status_code=404, detail="Unknown Underfoot instance.")

    def stop_instance(self, instance_id: str, remove: bool) -> dict[str, Any]:
        record = self.get_instance(instance_id)
        try:
            container = self.docker_client().containers.get(str(record.get("containerId", "")))
            container.stop(timeout=10)
            if remove:
                container.remove(force=True)
        except NotFound:
            pass
        except DockerException as exc:
            raise HTTPException(status_code=503, detail=f"Underfoot allocator could not stop instance {instance_id}: {exc}") from exc

        self.reconcile()
        stopped = self._instances.get(instance_id, dict(record))
        stopped["status"] = "stopped"
        stopped["isReady"] = False
        stopped["updatedAtEpoch"] = now_ts()
        return stopped

    def health(self) -> dict[str, Any]:
        self.reconcile()
        active_instances = [row for row in self._instances.values() if str(row.get("status", "")).strip().lower() == "running"]
        runtime_metadata = self._resolve_runtime_build_metadata()
        image = None
        try:
            image = self.docker_client().images.get(INSTANCE_IMAGE)
        except DockerException:
            image = None
        return {
            "status": "ok",
            "allocatorPort": ALLOCATOR_PORT,
            "dockerNetwork": DOCKER_NETWORK,
            "instanceImage": INSTANCE_IMAGE,
            "runtimeBuildFingerprint": runtime_metadata["runtimeBuildFingerprint"],
            "runtimeBuildTimestampUtc": runtime_metadata["runtimeBuildTimestampUtc"],
            "runtimeCompatibilityState": self._resolve_runtime_image_compatibility_state(image),
            "activeInstances": len(active_instances),
            "trackedInstances": len(self._instances),
            "worldServiceUrl": WORLD_SERVICE_URL,
            "publicHost": PUBLIC_HOST,
            "imageAutoBuild": IMAGE_AUTO_BUILD,
            "imageBuildContext": IMAGE_BUILD_CONTEXT,
            "imageBuildDockerfile": IMAGE_BUILD_DOCKERFILE,
            "matchPortRange": MATCH_PORT_RANGE,
            "roomPortRange": ROOM_PORT_RANGE,
        }


registry = InstanceRegistry() if runtime_enabled() and feature_enabled("LUMINAX_HOST_EXECUTION_ENABLED") else None
app = FastAPI(title="Underfoot Match Allocator", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    return registry.health()


@app.post("/api/instances")
async def create_instance(req: CreateInstanceRequest) -> dict[str, Any]:
    return {"status": "ok", "instance": registry.create_instance(req)}


@app.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str) -> dict[str, Any]:
    return {"status": "ok", "instance": registry.get_instance(instance_id)}


@app.post("/api/instances/{instance_id}/stop")
async def stop_instance(instance_id: str, req: StopInstanceRequest) -> dict[str, Any]:
    return {"status": "ok", "instance": registry.stop_instance(instance_id, req.remove)}


install_runtime_guard(app, 'underfoot-match-allocator', require_auth=True, required_feature='LUMINAX_HOST_EXECUTION_ENABLED')
