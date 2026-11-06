# NetSim Backend Server (Python / FastAPI + C++ algorithm core)
#
# This server handles HTTP, request validation, and orchestration in Python.
# All graph algorithms — Dijkstra, Bellman-Ford, and switching-mode delay
# simulation — are implemented in the C++ module (netsim_algo).
#
# Run:  python server.py
# API docs: http://localhost:8000/docs

import math
import os
import sys
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# Make the compiled C++ module importable regardless of the directory
# from which this script is launched.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "algo"))

try:
    import netsim_algo
except ImportError as e:
    raise ImportError(
        "Could not import 'netsim_algo'. Compile the C++ algorithm module "
        "first (see algo/build.sh / README.md)."
    ) from e


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class SimRequest(BaseModel):
    nodes: List[str]
    # Each edge: from, to, bandwidthMbps, latencyMs, lossPercent
    edges: List[dict]
    source: str
    destination: str
    algorithm: str = "dijkstra"
    switchingMode: str = "packet"
    messageSizeKb: float = 100.0
    numPackets: int = 10
    disabledNodes: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

ALLOWED_ALGORITHMS = {"dijkstra", "bellman-ford"}
ALLOWED_SWITCHING_MODES = {"circuit", "message", "packet"}
REQUIRED_EDGE_FIELDS = ("from", "to", "bandwidthMbps", "latencyMs", "lossPercent")


def _as_finite_number(value, field_name: str, edge_index: int) -> float:
    """Convert an edge value to a finite float or raise a useful API error."""
    if isinstance(value, bool):
        raise HTTPException(
            status_code=400,
            detail=f"Edge {edge_index}: '{field_name}' must be a number.",
        )

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"Edge {edge_index}: '{field_name}' must be a number.",
        )

    if not math.isfinite(number):
        raise HTTPException(
            status_code=400,
            detail=f"Edge {edge_index}: '{field_name}' must be a finite number.",
        )
    return number


def validate_request(body: SimRequest) -> None:
    """Validate all values that will be passed to the C++ simulation core."""

    # Nodes must be present, non-empty, and unique.
    if not body.nodes:
        raise HTTPException(status_code=400, detail="Add at least one node to the topology.")

    if any(not isinstance(node, str) or not node.strip() for node in body.nodes):
        raise HTTPException(status_code=400, detail="Node names must be non-empty strings.")

    if len(set(body.nodes)) != len(body.nodes):
        raise HTTPException(status_code=400, detail="Node names must be unique.")

    if any(node not in body.nodes for node in body.disabledNodes):
        raise HTTPException(status_code=400, detail="A disabled router does not exist in the topology.")

    if body.source not in body.nodes:
        raise HTTPException(status_code=400, detail="Source node does not exist in the topology.")

    if body.destination not in body.nodes:
        raise HTTPException(status_code=400, detail="Destination node does not exist in the topology.")

    if body.source == body.destination:
        raise HTTPException(status_code=400, detail="Source and destination must be different nodes.")

    # Do not silently fall back to Dijkstra for an unsupported algorithm.
    if body.algorithm not in ALLOWED_ALGORITHMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported algorithm '{body.algorithm}'. Use dijkstra or bellman-ford.",
        )

    if body.switchingMode not in ALLOWED_SWITCHING_MODES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported switchingMode. Use circuit, message, or packet.",
        )

    if not math.isfinite(body.messageSizeKb) or body.messageSizeKb <= 0:
        raise HTTPException(status_code=400, detail="messageSizeKb must be a finite number greater than 0.")

    if body.numPackets < 1:
        raise HTTPException(status_code=400, detail="numPackets must be at least 1.")

    # Validate edge structure and physical values before passing them to C++.
    for index, edge in enumerate(body.edges, start=1):
        if not isinstance(edge, dict):
            raise HTTPException(status_code=400, detail=f"Edge {index} must be an object.")

        missing = [field for field in REQUIRED_EDGE_FIELDS if field not in edge]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Edge {index} is missing required field(s): {', '.join(missing)}.",
            )

        start_node = edge["from"]
        end_node = edge["to"]

        if start_node not in body.nodes or end_node not in body.nodes:
            raise HTTPException(
                status_code=400,
                detail=f"Edge {index} refers to a node that does not exist in the topology.",
            )

        bandwidth = _as_finite_number(edge["bandwidthMbps"], "bandwidthMbps", index)
        latency = _as_finite_number(edge["latencyMs"], "latencyMs", index)
        loss = _as_finite_number(edge["lossPercent"], "lossPercent", index)

        if bandwidth <= 0:
            raise HTTPException(status_code=400, detail=f"Edge {index}: bandwidthMbps must be greater than 0.")
        if latency < 0:
            raise HTTPException(status_code=400, detail=f"Edge {index}: latencyMs cannot be negative.")
        if not 0 <= loss <= 100:
            raise HTTPException(status_code=400, detail=f"Edge {index}: lossPercent must be between 0 and 100.")


# ---------------------------------------------------------------------------
# Thin wrappers around the C++ module
# ---------------------------------------------------------------------------

def active_topology(body: SimRequest, apply_failures: bool = True) -> tuple[list[str], list[dict]]:
    disabled_nodes = set(body.disabledNodes) if apply_failures else set()
    nodes = [node for node in body.nodes if node not in disabled_nodes]
    edges = []
    for edge in body.edges:
        if edge["from"] in disabled_nodes or edge["to"] in disabled_nodes:
            continue
        if apply_failures and (edge.get("enabled", True) is False or edge.get("disabled", False) is True):
            continue
        edges.append(edge)
    return nodes, edges


def compute_route(body: SimRequest, apply_failures: bool = True, algorithm: str | None = None) -> dict:
    nodes, edges = active_topology(body, apply_failures=apply_failures)
    algorithm = algorithm or body.algorithm
    if body.source not in nodes or body.destination not in nodes:
        return {
            "found": False,
            "path": [],
            "totalLatencyMs": 0.0,
            "bottleneckBandwidthMbps": 0.0,
            "cumulativeLossPercent": 0.0,
            "error": "Source or destination router is unavailable",
        }
    if algorithm == "bellman-ford":
        return netsim_algo.bellman_ford(nodes, edges, body.source, body.destination)
    return netsim_algo.dijkstra(nodes, edges, body.source, body.destination)


def compute_switching(mode: str, route: dict, message_size_kb: float, num_packets: int) -> dict:
    return netsim_algo.simulate_switching(mode, route, message_size_kb, num_packets)


# ---------------------------------------------------------------------------
# FastAPI app + routes
# ---------------------------------------------------------------------------

app = FastAPI(title="NetSim Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "engine": "netsim_algo (C++)"}


@app.post("/api/route")
def route(body: SimRequest):
    validate_request(body)
    return compute_route(body)


@app.post("/api/simulate")
def simulate(body: SimRequest):
    validate_request(body)
    path = compute_route(body)

    if not path["found"]:
        return {
            "route": path,
            "switching": None,
            "error": "No path between source and destination",
        }

    switching = compute_switching(
        body.switchingMode,
        path,
        body.messageSizeKb,
        body.numPackets,
    )
    return {"route": path, "switching": switching}


@app.post("/api/failure-analysis")
def failure_analysis(body: SimRequest):
    validate_request(body)
    original = compute_route(body, apply_failures=False)
    after_failure = compute_route(body, apply_failures=True)

    latency_change = None
    if original["found"] and after_failure["found"]:
        latency_change = after_failure["totalLatencyMs"] - original["totalLatencyMs"]

    return {
        "originalRoute": original,
        "routeAfterFailure": after_failure,
        "reachableAfterFailure": after_failure["found"],
        "latencyChangeMs": latency_change,
    }


@app.post("/api/compare")
def compare(body: SimRequest):
    validate_request(body)

    # Same rule as the failure-analysis screen: endpoints must be operational.
    if body.source in body.disabledNodes or body.destination in body.disabledNodes:
        raise HTTPException(
            status_code=400,
            detail="Source and destination routers must remain operational.",
        )

    # Both algorithms run on the active topology, so disabled routers and
    # failed links are excluded, consistent with /api/route and /api/simulate.
    dj = compute_route(body, algorithm="dijkstra")
    bf = compute_route(body, algorithm="bellman-ford")

    # Response shape is unchanged.
    out = {"algorithms": {"dijkstra": dj, "bellman-ford": bf}}

    if dj["found"]:
        out["switchingModes"] = {
            mode: compute_switching(mode, dj, body.messageSizeKb, body.numPackets)
            for mode in ("circuit", "message", "packet")
        }
    return out


if __name__ == "__main__":
    print("NetSim backend running at http://localhost:8000")
    print("Algorithm engine: netsim_algo (C++)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
