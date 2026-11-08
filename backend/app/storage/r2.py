from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class ParquetReceipt:
    key: str
    size: int
    row_count: int
    checksum: str


class ObjectStore(Protocol):
    def put_object(self, key: str, body: bytes, *, content_type: str, metadata: dict[str, str] | None = None) -> None: ...
    def get_object(self, key: str) -> bytes: ...
    def head_object(self, key: str) -> ObjectInfo | None: ...
    def delete_object(self, key: str) -> None: ...
    def list_objects(self, prefix: str = "") -> list[ObjectInfo]: ...
    def object_exists(self, key: str) -> bool: ...
    def put_parquet(self, key: str, rows: list[dict]) -> ParquetReceipt: ...
    def read_parquet(self, key: str) -> list[dict]: ...


class R2NotConfigured(RuntimeError):
    pass


def parquet_bytes(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq
    sink = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), sink, compression="zstd")
    return sink.getvalue()


def parquet_row_count(payload: bytes) -> int:
    import pyarrow.parquet as pq
    return pq.read_metadata(io.BytesIO(payload)).num_rows


def parquet_rows(payload: bytes) -> list[dict]:
    import pyarrow.parquet as pq
    return pq.read_table(io.BytesIO(payload)).to_pylist()


class ParquetObjectStoreMixin:
    def put_parquet(self, key: str, rows: list[dict]) -> ParquetReceipt:
        if not rows:
            raise ValueError("Cannot archive an empty Parquet partition")
        payload = parquet_bytes(rows)
        checksum = hashlib.sha256(payload).hexdigest()
        existing = self.head_object(key)
        if existing and existing.size == len(payload):
            current = self.get_object(key)
            if (hashlib.sha256(current).hexdigest() == checksum
                    and parquet_row_count(current) == len(rows)):
                return ParquetReceipt(key, len(payload), len(rows), checksum)
        self.put_object(key, payload, content_type="application/vnd.apache.parquet", metadata={"sha256": checksum, "row-count": str(len(rows))})
        info = self.head_object(key)
        if info is None or info.size != len(payload):
            raise IOError("Parquet object size verification failed")
        actual = self.get_object(key)
        actual_checksum = hashlib.sha256(actual).hexdigest()
        actual_rows = parquet_row_count(actual)
        if actual_checksum != checksum:
            raise IOError("Parquet object checksum verification failed")
        if actual_rows != len(rows):
            raise IOError("Parquet object row-count verification failed")
        return ParquetReceipt(key, len(payload), actual_rows, actual_checksum)

    def read_parquet(self, key: str) -> list[dict]:
        return parquet_rows(self.get_object(key))


class R2ObjectStore(ParquetObjectStoreMixin):
    def __init__(self) -> None:
        cfg = get_settings()
        if not (cfg.r2_endpoint and cfg.r2_access_key_id and cfg.r2_secret_access_key and cfg.r2_bucket):
            raise R2NotConfigured("R2_NOT_CONFIGURED")
        self.bucket = cfg.r2_bucket
        self.client = boto3.client("s3", endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key_id,
            aws_secret_access_key=cfg.r2_secret_access_key, region_name="auto")

    def put_object(self, key: str, body: bytes, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, Metadata=metadata or {})

    def get_object(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def head_object(self, key: str) -> ObjectInfo | None:
        try:
            result = self.client.head_object(Bucket=self.bucket, Key=key)
            return ObjectInfo(key, result["ContentLength"], result.get("ETag"), result.get("Metadata", {}))
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        found = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            found.extend(ObjectInfo(item["Key"], item["Size"], item.get("ETag")) for item in page.get("Contents", []))
        return found

    def object_exists(self, key: str) -> bool:
        return self.head_object(key) is not None


class InMemoryObjectStore(ParquetObjectStoreMixin):
    """Explicit test/local adapter; never selected as a production fallback."""
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}

    def put_object(self, key: str, body: bytes, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
        self._objects[key] = (bytes(body), content_type, dict(metadata or {}))

    def get_object(self, key: str) -> bytes:
        try:
            return self._objects[key][0]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    def head_object(self, key: str) -> ObjectInfo | None:
        item = self._objects.get(key)
        if item is None:
            return None
        payload, _, metadata = item
        return ObjectInfo(key, len(payload), hashlib.md5(payload).hexdigest(), metadata.copy())

    def delete_object(self, key: str) -> None:
        self._objects.pop(key, None)

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        return [self.head_object(key) for key in sorted(self._objects) if key.startswith(prefix)]

    def object_exists(self, key: str) -> bool:
        return key in self._objects
