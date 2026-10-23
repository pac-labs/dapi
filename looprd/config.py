"""Host configuration and secret references (values never enter specifications)."""

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    sources: str = "examples/sources.yaml"
    data: Path = Path("state")
    secrets: Path = Path("secrets")
    poll_seconds: float = 60
    drain_seconds: float = 30
    request_seconds: float = 30
    max_body_bytes: int = 1_048_576
    event_queue: int = 128
    admin_secret: str = "admin-token"
    bootstrap_admin: bool = True
    allow_python: bool = False
    allowed_origins: tuple[str, ...] = ()
    origin_restrictions: tuple[str, ...] | None = None
    file_max_bytes: int = 100 * 1024 * 1024
    file_quota_bytes: int = 1024 * 1024 * 1024
    event_namespace: str = "looprd"
    trusted_components: tuple[str, ...] | None = None
    worker_memory_mb: int = 256
    worker_concurrency: int = 2
    container_metrics_file: Path | None = None
    push_public_url: str | None = None
    push_delivery_url: str | None = None
    push_token_secret: str | None = None
    discovery_enabled: bool = False
    discovery_components: tuple[str, ...] | None = None
    discovery_https_origins: tuple[str, ...] = ()
    discovery_delivery_enabled: bool = False
    discovery_receiver_url: str | None = None
    discovery_credential_secret: str | None = None
    deployment_id: str | None = None
    public_url: str | None = None
    registration_readers: str = "{}"

    def __post_init__(self):
        from .discovery_source import origin
        for value in self.discovery_https_origins:
            if origin(value) != value:
                raise ValueError("discovery origins must omit trailing slashes")
        from .push_transport import validate_provider
        validate_provider(self)
        if self.origin_restrictions is not None:
            from .cors_spec import validate_origin
            for origin in self.origin_restrictions:
                validate_origin(origin)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.event_namespace):
            raise ValueError("event namespace must contain 1-64 letters, digits, underscores or hyphens")
        if not 64 <= self.worker_memory_mb <= 2048:
            raise ValueError("worker memory limit must be between 64 and 2048 MiB")
        if not 1 <= self.worker_concurrency <= 16:
            raise ValueError("worker concurrency must be between 1 and 16")

    @classmethod
    def from_env(cls):
        return cls(
            sources=os.getenv("LOOPRD_SOURCES", cls.sources),
            data=Path(os.getenv("LOOPRD_DATA", "state")),
            secrets=Path(os.getenv("LOOPRD_SECRETS", "secrets")),
            poll_seconds=max(1, float(os.getenv("LOOPRD_POLL_SECONDS", "60"))),
            drain_seconds=max(0, float(os.getenv("LOOPRD_DRAIN_SECONDS", "30"))),
            request_seconds=max(1, float(os.getenv("LOOPRD_REQUEST_SECONDS", "30"))),
            bootstrap_admin=os.getenv("LOOPRD_BOOTSTRAP_ADMIN", "true").lower() == "true",
            allow_python=os.getenv("LOOPRD_ALLOW_PYTHON", "false").lower() == "true",
            allowed_origins=tuple(value.strip() for value in os.getenv("LOOPRD_ORIGINS", "").split(",") if value.strip()),
            origin_restrictions=tuple(value.strip() for value in os.environ["LOOPRD_ORIGIN_RESTRICTIONS"].split(",") if value.strip()) if "LOOPRD_ORIGIN_RESTRICTIONS" in os.environ else None,
            file_max_bytes=max(1, int(os.getenv("LOOPRD_FILE_MAX_BYTES", str(cls.file_max_bytes)))),
            file_quota_bytes=max(1, int(os.getenv("LOOPRD_FILE_QUOTA_BYTES", str(cls.file_quota_bytes)))),
            event_namespace=os.getenv("LOOPRD_EVENT_NAMESPACE", "looprd"),
            trusted_components=tuple(value.strip() for value in os.environ["LOOPRD_TRUSTED_COMPONENTS"].split(",") if value.strip()) if "LOOPRD_TRUSTED_COMPONENTS" in os.environ else None,
            worker_memory_mb=int(os.getenv("LOOPRD_WORKER_MEMORY_MB", "256")),
            worker_concurrency=int(os.getenv("LOOPRD_WORKER_CONCURRENCY", "2")),
            container_metrics_file=Path(os.environ["LOOPRD_CONTAINER_METRICS_FILE"]) if os.getenv("LOOPRD_CONTAINER_METRICS_FILE") else None,
            push_public_url=os.getenv("LOOPRD_PUSH_PUBLIC_URL"),
            push_delivery_url=os.getenv("LOOPRD_PUSH_DELIVERY_URL"),
            push_token_secret=os.getenv("LOOPRD_PUSH_TOKEN_SECRET"),
            discovery_enabled=os.getenv("LOOPRD_DISCOVERY_ENABLED", "false").lower() == "true",
            discovery_delivery_enabled=os.getenv("LOOPRD_DISCOVERY_DELIVERY", "false").lower() == "true",
            discovery_receiver_url=os.getenv("LOOPRD_DISCOVERY_RECEIVER"),
            discovery_credential_secret=os.getenv("LOOPRD_DISCOVERY_CREDENTIAL"),
            deployment_id=os.getenv("LOOPRD_DEPLOYMENT_ID"),
            public_url=os.getenv("LOOPRD_PUBLIC_URL"),
            registration_readers=os.getenv("LOOPRD_REGISTRATION_READERS", "{}"),
            discovery_components=tuple(value.strip() for value in os.environ["LOOPRD_DISCOVERY_COMPONENTS"].split(",") if value.strip()) if "LOOPRD_DISCOVERY_COMPONENTS" in os.environ else None,
            discovery_https_origins=tuple(value.strip() for value in os.getenv("LOOPRD_DISCOVERY_HTTPS_ORIGINS", "").split(",") if value.strip()),
        )

    def secret(self, reference: str) -> str:
        root = self.secrets.resolve()
        target = (root / reference).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ValueError("invalid secret reference")
        value = target.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("empty secret")
        return value

