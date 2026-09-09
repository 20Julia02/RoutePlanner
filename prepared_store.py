"""Persistent JSON storage for reusable prepared walking networks."""

from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from geojson_utils import to_xy
from models import CostMatrix, Edge, Graph, Node
from network_preparation import (
    list_prepared_attractions,
    recalculate_attraction_nodes,
    update_prepared_attractions,
)
from planning_models import EdgeGeometry, PlanningError, PreparedNetwork
from shapely.geometry import LineString


NETWORK_ID = re.compile(r"^[0-9a-f]{32}$")


class PreparedNetworkStore:
    """Persist prepared networks as JSON with small metadata sidecars."""

    def __init__(self, root: Path, *, read_only: bool = False, cache_size: int = 2):
        self.root = root
        self.read_only = bool(read_only)
        self.cache_size = max(1, int(cache_size))
        self._cache: OrderedDict[str, PreparedNetwork] = OrderedDict()

    def _ensure_writable(self) -> None:
        if self.read_only:
            raise PlanningError("Publiczny katalog zestawów jest tylko do odczytu.")

    def _remember(self, network_id: str, network: PreparedNetwork) -> None:
        self._cache.pop(network_id, None)
        self._cache[network_id] = network
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def _path(self, network_id: str) -> Path:
        return self.root / f"{self._validated_id(network_id)}.json"

    def _metadata_path(self, network_id: str) -> Path:
        return self.root / f"{self._validated_id(network_id)}.meta.json"

    def _attractions_path(self, network_id: str) -> Path:
        return self.root / f"{self._validated_id(network_id)}.attractions.json"

    @staticmethod
    def _validated_id(network_id: str) -> str:
        network_id = str(network_id)
        if not NETWORK_ID.fullmatch(str(network_id)):
            raise PlanningError("Nieprawidłowy identyfikator przygotowanego zestawu.")
        return network_id

    @staticmethod
    def _write_json(target: Path, value: Dict[str, Any]) -> None:
        """Atomically replace a JSON file after writing a sibling temporary file."""

        temporary = target.with_name(f".{target.name}.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(target)

    def _write_metadata(self, metadata: Dict[str, Any]) -> None:
        self._write_json(self._metadata_path(str(metadata["id"])), metadata)

    def _write_attractions(self, network_id: str, items: List[Dict[str, Any]]) -> None:
        self._write_json(
            self._attractions_path(network_id),
            {"version": 1, "network_id": network_id, "items": items},
        )

    def _read_attractions(self, network_id: str) -> List[Dict[str, Any]]:
        """Read and validate the lightweight attraction-editor sidecar."""

        path = self._attractions_path(network_id)
        try:
            with path.open("r", encoding="utf-8") as stream:
                document = json.load(stream)
            if (
                document.get("version") != 1
                or document.get("network_id") != network_id
                or not isinstance(document.get("items"), list)
            ):
                raise PlanningError("Plik ustawień atrakcji jest uszkodzony lub niekompletny.")
            items = document["items"]
            required = {
                "id",
                "name",
                "duration_seconds",
                "weight",
                "enabled",
                "vertex_id",
            }
            if any(not isinstance(item, dict) or not required.issubset(item) for item in items):
                raise PlanningError("Plik ustawień atrakcji jest uszkodzony lub niekompletny.")
            return items
        except PlanningError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise PlanningError("Plik ustawień atrakcji jest uszkodzony lub niekompletny.") from exc

    def save(self, name: str, network: PreparedNetwork) -> Dict[str, Any]:
        """Save a new prepared network and return its summary metadata."""

        self._ensure_writable()
        clean_name = str(name or "").strip()
        if not clean_name:
            raise PlanningError("Podaj nazwę przygotowanego zestawu.")
        if len(clean_name) > 100:
            raise PlanningError("Nazwa przygotowanego zestawu może mieć maksymalnie 100 znaków.")

        network_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        document = self._serialize(network)
        document["metadata"] = {
            "id": network_id,
            "name": clean_name,
            "created_at": created_at,
            **self._summary(network),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(network_id)
        self._write_json(target, document)
        self._write_metadata(document["metadata"])
        self._write_attractions(network_id, list_prepared_attractions(network))
        self._remember(network_id, network)
        return document["metadata"]

    def list(self) -> List[Dict[str, Any]]:
        """List valid metadata without loading full graph documents."""

        if not self.root.exists():
            return []
        result = []
        for path in self.root.glob("*.json"):
            if path.name.endswith(".meta.json") or not NETWORK_ID.fullmatch(path.stem):
                continue
            try:
                metadata_path = self._metadata_path(path.stem)
                if metadata_path.exists():
                    with metadata_path.open("r", encoding="utf-8") as stream:
                        metadata = json.load(stream)
                else:
                    # Jednorazowa migracja starszego zestawu. Następne
                    # listowania czytają już tylko mały plik *.meta.json.
                    with path.open("r", encoding="utf-8") as stream:
                        metadata = (json.load(stream).get("metadata") or {})
                    if (
                        not self.read_only
                        and metadata.get("id")
                        and metadata.get("name")
                    ):
                        self._write_metadata(metadata)
                if metadata.get("id") and metadata.get("name"):
                    result.append(metadata)
            except (OSError, ValueError, TypeError):
                continue
        return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)

    def replace(self, network_id: str, network: PreparedNetwork) -> Dict[str, Any]:
        """Replace a saved network while preserving its identity and name."""

        self._ensure_writable()
        target = self._path(network_id)
        if not target.exists():
            raise PlanningError("Nie znaleziono przygotowanego zestawu danych.")
        with target.open("r", encoding="utf-8") as stream:
            metadata = (json.load(stream).get("metadata") or {})
        metadata.update(self._summary(network))
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        document = self._serialize(network)
        document["metadata"] = metadata
        self._write_json(target, document)
        self._write_metadata(metadata)
        self._write_attractions(network_id, list_prepared_attractions(network))
        self._remember(network_id, network)
        return metadata

    def list_attractions(self, network_id: str) -> List[Dict[str, Any]]:
        """List editable attractions without loading the large graph document."""

        network_id = self._validated_id(network_id)
        if not self._path(network_id).exists():
            raise PlanningError("Nie znaleziono przygotowanego zestawu danych.")
        if self._attractions_path(network_id).exists():
            return self._read_attractions(network_id)

        # Jednorazowa migracja zestawów zapisanych przed dodaniem sidecara.
        network = self.load(network_id)
        items = list_prepared_attractions(network)
        if not self.read_only:
            self._write_attractions(network_id, items)
        return items

    @staticmethod
    def _updated_attraction_rows(
        items: List[Dict[str, Any]], edits: Any
    ) -> List[Dict[str, Any]]:
        """Validate editor input and apply it to lightweight rows."""

        if not isinstance(edits, list) or not edits:
            raise PlanningError("Nie przekazano zmian atrakcji do zapisania.")
        rows_by_id = {str(item["id"]): dict(item) for item in items}
        unknown_ids = []
        for edit in edits:
            if not isinstance(edit, dict):
                raise PlanningError("Nieprawidłowy format zmian atrakcji.")
            attraction_id = str(edit.get("id") or "")
            row = rows_by_id.get(attraction_id)
            if row is None:
                unknown_ids.append(attraction_id)
                continue
            try:
                duration = float(edit.get("duration_seconds"))
            except (TypeError, ValueError) as exc:
                raise PlanningError("Czas zwiedzania musi być liczbą.") from exc
            if duration < 0 or not math.isfinite(duration):
                raise PlanningError("Czas zwiedzania musi być nieujemną, skończoną liczbą.")
            enabled = edit.get("enabled")
            if not isinstance(enabled, bool):
                raise PlanningError("Pole aktywności atrakcji musi mieć wartość logiczną.")
            row["duration_seconds"] = round(duration, 2)
            row["enabled"] = enabled
        if unknown_ids:
            raise PlanningError(f"Nie znaleziono atrakcji: {', '.join(unknown_ids)}.")
        return sorted(
            rows_by_id.values(),
            key=lambda item: (-float(item["weight"]), str(item["name"]).casefold()),
        )

    def update_attractions(
        self, network_id: str, edits: Any
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Persist editor changes in the sidecar and refresh an active cache."""

        self._ensure_writable()
        network_id = self._validated_id(network_id)
        items = self._updated_attraction_rows(self.list_attractions(network_id), edits)
        self._write_attractions(network_id, items)

        cached = self._cache.get(network_id)
        if cached is not None:
            try:
                update_prepared_attractions(cached, edits)
            except PlanningError:
                self._cache.pop(network_id, None)
                raise

        metadata_path = self._metadata_path(network_id)
        try:
            with metadata_path.open("r", encoding="utf-8") as stream:
                metadata = json.load(stream)
        except (OSError, ValueError, TypeError) as exc:
            raise PlanningError("Metadane przygotowanego zestawu są uszkodzone lub niekompletne.") from exc

        weights_by_vertex: Dict[int, float] = {}
        for item in items:
            if item["enabled"]:
                vertex_id = int(item["vertex_id"])
                weights_by_vertex[vertex_id] = weights_by_vertex.get(
                    vertex_id, 0.0
                ) + float(item["weight"])
        metadata["attraction_vertices"] = sum(
            weight > 0 for weight in weights_by_vertex.values()
        )
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_metadata(metadata)
        return items, metadata

    def load(self, network_id: str) -> PreparedNetwork:
        """Load and cache a prepared network, validating its persisted shape."""

        network_id = self._validated_id(network_id)
        path = self._path(network_id)
        if network_id in self._cache:
            network = self._cache.pop(network_id)
            self._cache[network_id] = network
            return network
        if not path.exists():
            raise PlanningError("Nie znaleziono przygotowanego zestawu danych.")
        try:
            with path.open("r", encoding="utf-8") as stream:
                document = json.load(stream)
            network = self._deserialize(document)
            if self._attractions_path(network_id).exists():
                sidecar_items = self._read_attractions(network_id)
                update_prepared_attractions(
                    network,
                    [
                        {
                            "id": item["id"],
                            "duration_seconds": item["duration_seconds"],
                            "enabled": item["enabled"],
                        }
                        for item in sidecar_items
                    ],
                )
            elif not self.read_only:
                self._write_attractions(network_id, list_prepared_attractions(network))
            self._remember(network_id, network)
            return network
        except PlanningError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise PlanningError("Przygotowany zestaw jest uszkodzony lub niekompletny.") from exc

    @staticmethod
    def _summary(network: PreparedNetwork) -> Dict[str, Any]:
        bounds = None
        min_lon = min_lat = float("inf")
        max_lon = max_lat = -float("inf")
        for geometry in network.edges.values():
            for coordinate in geometry.coordinates:
                if len(coordinate) < 2:
                    continue
                min_lon, min_lat = min(min_lon, coordinate[0]), min(min_lat, coordinate[1])
                max_lon, max_lat = max(max_lon, coordinate[0]), max(max_lat, coordinate[1])
        if min_lon != float("inf"):
            bounds = [min_lon, min_lat, max_lon, max_lat]
        return {
            "nodes": len(network.graph.nodes),
            "edges": len(network.edges),
            "attraction_vertices": len(network.graph.attractions),
            "cost_matrix_entries": len(network.cost_matrix),
            "bounds": bounds,
        }

    @staticmethod
    def _serialize(network: PreparedNetwork) -> Dict[str, Any]:
        return {
            "version": 1,
            "reference_latitude": network.reference_latitude,
            "warnings": network.warnings,
            "name_field": network.name_field,
            "duration_field": network.duration_field,
            "duration_multiplier": network.duration_multiplier,
            "weight_field": network.weight_field,
            "image_field": network.image_field,
            "description_field": network.description_field,
            "nodes": [
                {"id": node.id, "x": node.x, "y": node.y, "weight": node.weight, "time": node.time}
                for node in network.graph.nodes.values()
            ],
            "edges": [
                {
                    "id": edge_id,
                    "start_id": geometry.start_id,
                    "end_id": geometry.end_id,
                    "length": network.graph.edges[edge_id].length,
                    "time": network.graph.edges[edge_id].time,
                    "coordinates": geometry.coordinates,
                    "source_id": geometry.source_id,
                    "properties": geometry.properties,
                }
                for edge_id, geometry in network.edges.items()
            ],
            "attractions_by_vertex": network.attractions_by_vertex,
            "cost_matrix": [
                [source_id, target_id, cost]
                for (source_id, target_id), cost in network.cost_matrix.items()
            ],
        }

    @staticmethod
    def _deserialize(document: Dict[str, Any]) -> PreparedNetwork:
        if document.get("version") != 1:
            raise PlanningError("Nieobsługiwana wersja przygotowanego zestawu.")
        graph = Graph()
        for value in document["nodes"]:
            graph.add_node(Node(**value))

        reference_latitude = float(document["reference_latitude"])
        edges = {}
        for value in document["edges"]:
            edge_id = int(value["id"])
            start_id, end_id = int(value["start_id"]), int(value["end_id"])
            graph.add_edge(
                Edge(
                    id=edge_id,
                    start=graph.nodes[start_id],
                    end=graph.nodes[end_id],
                    length=float(value["length"]),
                    time=float(value["time"]),
                )
            )
            coordinates = value["coordinates"]
            projected = [to_xy(point, reference_latitude) for point in coordinates]
            edges[edge_id] = EdgeGeometry(
                coordinates=coordinates,
                line_xy=LineString(projected),
                start_id=start_id,
                end_id=end_id,
                source_id=value.get("source_id"),
                properties=value.get("properties") or {},
            )

        matrix = CostMatrix()
        for source_id, target_id, cost in document.get("cost_matrix", []):
            matrix.add_cost(int(source_id), int(target_id), float(cost))
        sample_properties = next(
            (
                feature.get("properties") or {}
                for features in document.get("attractions_by_vertex", {}).values()
                for feature in features
            ),
            {},
        )
        duration_field = document.get("duration_field")
        if not duration_field:
            duration_field = next(
                (candidate for candidate in ("duration", "time", "czas", "visit_time") if candidate in sample_properties),
                "duration",
            )
        weight_field = document.get("weight_field")
        if not weight_field:
            weight_field = next(
                (candidate for candidate in ("weight", "views", "waga", "rating") if candidate in sample_properties),
                "weight",
            )
        network = PreparedNetwork(
            graph=graph,
            edges=edges,
            attractions_by_vertex={int(key): value for key, value in document["attractions_by_vertex"].items()},
            reference_latitude=reference_latitude,
            warnings=list(document.get("warnings") or []),
            cost_matrix=matrix,
            name_field=str(document.get("name_field", "name")),
            duration_field=str(duration_field),
            duration_multiplier=float(document.get("duration_multiplier", 60.0)),
            weight_field=str(weight_field),
            image_field=str(document.get("image_field", "image")),
            description_field=str(document.get("description_field", "description")),
        )
        recalculate_attraction_nodes(network)
        return network
