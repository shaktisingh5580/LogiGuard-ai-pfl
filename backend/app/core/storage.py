"""Abstract storage backend with local and S3 implementations."""
from __future__ import annotations

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract interface for blob storage (documents, exports, traces)."""

    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload bytes and return the storage key."""
        ...

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Download bytes by key."""
        ...

    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Get a presigned/public URL for the object."""
        ...

    @staticmethod
    def compute_hash(data: bytes) -> str:
        """SHA-256 hash of the file content for integrity verification."""
        return hashlib.sha256(data).hexdigest()


class LocalStorage(StorageBackend):
    """Filesystem-based storage for local development."""

    def __init__(self, base_dir: str | None = None):
        self._base_dir = Path(base_dir or get_settings().local_storage_path)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        file_path = self._base_dir / key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        logger.info("Stored %d bytes to local: %s", len(data), key)
        return key

    async def download(self, key: str) -> bytes:
        file_path = self._base_dir / key
        if not file_path.exists():
            raise FileNotFoundError(f"Object not found: {key}")
        return file_path.read_bytes()

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"file://{self._base_dir / key}"


class S3Storage(StorageBackend):
    """AWS S3 / MinIO storage backend."""

    def __init__(self):
        settings = get_settings()
        try:
            import boto3
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
            )
        except ImportError:
            raise ImportError("boto3 is required for S3Storage. Install with: pip install boto3")
        self._bucket = settings.s3_bucket

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        logger.info("Stored %d bytes to S3: %s/%s", len(data), self._bucket, key)
        return key

    async def download(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def get_storage_backend() -> StorageBackend:
    """Factory — returns the configured storage backend."""
    settings = get_settings()
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorage()
    elif backend == "s3":
        return S3Storage()
    else:
        raise ValueError(f"Unknown storage backend: {backend}. Supported: local, s3")
