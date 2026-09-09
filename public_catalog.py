from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from network_preparation import list_prepared_attractions
from prepared_store import PreparedNetworkStore


_NETWORK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_MAX_MANIFEST_BYTES = 1_000_000
_MAX_MANIFEST_ENTRIES = 100


@dataclass(frozen=True)
class _RemoteNetwork:
    metadata: dict[str, Any]
    url: str
    sha256: str
    attractions_url: str | None = None
    attractions_sha256: str | None = None


class PublicNetworkCatalog:
    """Read-only catalog backed by local files or integrity-checked HTTPS blobs."""

    def __init__(
        self,
        local_root: Path,
        *,
        manifest_url: str | None = None,
        manifest_sha256: str | None = None,
        allowed_hosts: tuple[str, ...] = (".public.blob.vercel-storage.com",),
        maximum_document_bytes: int = 200_000_000,
    ) -> None:
        self._local = PreparedNetworkStore(local_root, read_only=True, cache_size=1)
        self._manifest_url = (manifest_url or "").strip() or None
        self._manifest_sha256 = (manifest_sha256 or "").strip().lower() or None
        self._allowed_hosts = tuple(
            host.strip().lower() for host in allowed_hosts if host.strip()
        )
        self._maximum_document_bytes = maximum_document_bytes
        self._manifest: dict[str, _RemoteNetwork] | None = None
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._attractions_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()

        if self._manifest_url:
            if not self._manifest_sha256 or not _SHA256.fullmatch(self._manifest_sha256):
                raise ValueError(
                    "APP_PUBLIC_NETWORK_MANIFEST_SHA256 musi zawierać sumę SHA-256 manifestu."
                )
            if not self._allowed_hosts:
                raise ValueError("Lista dozwolonych hostów publicznych danych jest pusta.")
            self._validate_url(self._manifest_url)

    def list(self) -> list[dict[str, Any]]:
        if not self._manifest_url:
            return self._local.list()
        entries = self._remote_manifest().values()
        return sorted(
            (dict(entry.metadata) for entry in entries),
            key=lambda item: (str(item.get("created_at", "")), item["name"]),
            reverse=True,
        )

    def load(self, network_id: str):
        if not self._manifest_url:
            return self._local.load(network_id)
        if not _NETWORK_ID.fullmatch(network_id):
            raise ValueError("Nieprawidłowy identyfikator sieci.")

        with self._lock:
            cached = self._cache.get(network_id)
            if cached is not None:
                self._cache.move_to_end(network_id)
                return cached

            entry = self._remote_manifest().get(network_id)
            if entry is None:
                raise FileNotFoundError(f"Nie znaleziono sieci: {network_id}")
            document = self._download(
                entry.url,
                maximum_bytes=self._maximum_document_bytes,
                expected_sha256=entry.sha256,
            )
            try:
                payload = json.loads(document)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Publiczny plik sieci nie jest poprawnym JSON-em.") from exc
            if not isinstance(payload, Mapping):
                raise ValueError("Publiczny plik sieci ma nieprawidłowy format.")
            network = self._local._deserialize(dict(payload))
            self._cache[network_id] = network
            self._cache.move_to_end(network_id)
            while len(self._cache) > 1:
                self._cache.popitem(last=False)
            return network

    def list_attractions(self, network_id: str) -> list[dict[str, Any]]:
        """Return editor rows without ever making the public catalog writable."""

        if not self._manifest_url:
            return self._local.list_attractions(network_id)
        if not _NETWORK_ID.fullmatch(network_id):
            raise ValueError("Nieprawidłowy identyfikator sieci.")

        with self._lock:
            cached = self._attractions_cache.get(network_id)
            if cached is not None:
                self._attractions_cache.move_to_end(network_id)
                return [dict(item) for item in cached]

            entry = self._remote_manifest().get(network_id)
            if entry is None:
                raise FileNotFoundError(f"Nie znaleziono sieci: {network_id}")
            if not entry.attractions_url or not entry.attractions_sha256:
                return list_prepared_attractions(self.load(network_id))

            content = self._download(
                entry.attractions_url,
                maximum_bytes=self._maximum_document_bytes,
                expected_sha256=entry.attractions_sha256,
            )
            try:
                document = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Publiczny plik atrakcji nie jest poprawnym JSON-em.") from exc
            items = self._parse_attractions_document(document, network_id)
            self._attractions_cache[network_id] = items
            self._attractions_cache.move_to_end(network_id)
            while len(self._attractions_cache) > 5:
                self._attractions_cache.popitem(last=False)
            return [dict(item) for item in items]

    @staticmethod
    def _parse_attractions_document(document: Any, network_id: str) -> list[dict[str, Any]]:
        required = {
            "id",
            "name",
            "duration_seconds",
            "weight",
            "enabled",
            "vertex_id",
        }
        if (
            not isinstance(document, Mapping)
            or document.get("version") != 1
            or document.get("network_id") != network_id
            or not isinstance(document.get("items"), list)
        ):
            raise ValueError("Publiczny plik atrakcji ma nieprawidłowy format.")
        items = document["items"]
        if any(not isinstance(item, Mapping) or not required.issubset(item) for item in items):
            raise ValueError("Publiczny plik atrakcji jest niekompletny.")
        return [dict(item) for item in items]

    def _remote_manifest(self) -> dict[str, _RemoteNetwork]:
        with self._lock:
            if self._manifest is not None:
                return self._manifest
            assert self._manifest_url is not None
            assert self._manifest_sha256 is not None
            content = self._download(
                self._manifest_url,
                maximum_bytes=_MAX_MANIFEST_BYTES,
                expected_sha256=self._manifest_sha256,
            )
            try:
                document = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Manifest publicznych sieci nie jest poprawnym JSON-em.") from exc
            if not isinstance(document, Mapping):
                raise ValueError("Manifest publicznych sieci ma nieprawidłowy format.")
            raw_entries = document.get("networks")
            if not isinstance(raw_entries, list) or len(raw_entries) > _MAX_MANIFEST_ENTRIES:
                raise ValueError("Manifest zawiera nieprawidłową liczbę sieci.")

            manifest: dict[str, _RemoteNetwork] = {}
            for raw_entry in raw_entries:
                entry = self._parse_manifest_entry(raw_entry)
                network_id = entry.metadata["id"]
                if network_id in manifest:
                    raise ValueError("Manifest zawiera powtórzony identyfikator sieci.")
                manifest[network_id] = entry
            self._manifest = manifest
            return manifest

    def _parse_manifest_entry(self, raw_entry: Any) -> _RemoteNetwork:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("Nieprawidłowy wpis w manifeście publicznych sieci.")
        network_id = str(raw_entry.get("id", ""))
        name = str(raw_entry.get("name", "")).strip()
        url = str(raw_entry.get("url", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip().lower()
        attractions_url = str(raw_entry.get("attractions_url", "")).strip()
        attractions_sha256 = str(raw_entry.get("attractions_sha256", "")).strip().lower()
        if not _NETWORK_ID.fullmatch(network_id):
            raise ValueError("Manifest zawiera nieprawidłowy identyfikator sieci.")
        if not name or len(name) > 100:
            raise ValueError("Manifest zawiera nieprawidłową nazwę sieci.")
        if not _SHA256.fullmatch(sha256):
            raise ValueError("Manifest zawiera nieprawidłową sumę SHA-256 sieci.")
        self._validate_url(url)
        if bool(attractions_url) != bool(attractions_sha256):
            raise ValueError("Manifest musi zawierać adres i sumę SHA-256 pliku atrakcji.")
        if attractions_url:
            if not _SHA256.fullmatch(attractions_sha256):
                raise ValueError("Manifest zawiera nieprawidłową sumę SHA-256 atrakcji.")
            self._validate_url(attractions_url)

        metadata: dict[str, Any] = {
            "id": network_id,
            "name": name,
            "attraction_vertices": 0,
        }
        for key in ("created_at", "updated_at"):
            value = raw_entry.get(key)
            if isinstance(value, str) and len(value) <= 50:
                metadata[key] = value
        for key in ("nodes", "edges", "attraction_vertices", "cost_matrix_entries"):
            value = raw_entry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000:
                metadata[key] = value
        bounds = raw_entry.get("bounds")
        if (
            isinstance(bounds, list)
            and len(bounds) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in bounds)
        ):
            west, south, east, north = (float(value) for value in bounds)
            if -180 <= west <= east <= 180 and -90 <= south <= north <= 90:
                metadata["bounds"] = [west, south, east, north]
        return _RemoteNetwork(
            metadata=metadata,
            url=url,
            sha256=sha256,
            attractions_url=attractions_url or None,
            attractions_sha256=attractions_sha256 or None,
        )

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
            or not any(
                host.endswith(allowed) if allowed.startswith(".") else host == allowed
                for allowed in self._allowed_hosts
            )
        ):
            raise ValueError("Adres publicznych danych nie znajduje się na dozwolonym hoście HTTPS.")

    def _download(self, url: str, *, maximum_bytes: int, expected_sha256: str) -> bytes:
        self._validate_url(url)
        request = Request(url, headers={"User-Agent": "WanderPlan/1.0"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URL is allowlisted above.
            final_url = response.geturl()
            self._validate_url(final_url)
            declared_length = response.headers.get("Content-Length")
            if declared_length:
                try:
                    length = int(declared_length)
                except ValueError as exc:
                    raise ValueError("Serwer publicznych danych zwrócił błędny rozmiar.") from exc
                if length < 0 or length > maximum_bytes:
                    raise ValueError("Publiczny plik przekracza dozwolony rozmiar.")
            content = response.read(maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise ValueError("Publiczny plik przekracza dozwolony rozmiar.")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("Suma kontrolna publicznego pliku jest nieprawidłowa.")
        return content
