// netsim_algo.cpp
//
// This file contains ALL the algorithmic core of NetSim, written in C++:
//   - Graph representation
//   - Dijkstra's shortest path algorithm
//   - Bellman-Ford shortest path algorithm
//   - Circuit / Message / Packet switching delay simulation
//
// It is compiled into a Python-importable module (netsim_algo.so) using
// pybind11. The Python backend (server.py) imports this module and calls
// these functions directly, exactly like calling any other Python function
// — but the actual computation happens in compiled C++.
//
// Build: see build.sh in this folder, or the command in README.md

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <vector>
#include <queue>
#include <limits>
#include <string>
#include <unordered_map>
#include <algorithm>

namespace py = pybind11;
constexpr double INF = std::numeric_limits<double>::infinity();

// ---------------------------------------------------------------------------
// Graph model
// ---------------------------------------------------------------------------

struct Edge {
    std::string to;
    double bandwidthMbps;
    double latencyMs;
    double lossPercent;
};

struct Graph {
    std::unordered_map<std::string, std::vector<Edge>> adj;

    void addNode(const std::string& id) {
        if (adj.find(id) == adj.end()) adj[id] = {};
    }

    void addEdge(const std::string& a, const std::string& b,
                 double bw, double lat, double loss) {
        addNode(a);
        addNode(b);
        adj[a].push_back({b, bw, lat, loss});
        adj[b].push_back({a, bw, lat, loss});
    }
};

// Build a Graph from data coming out of Python:
//   nodes: list[str]
//   edges: list[dict] each with from/to/bandwidthMbps/latencyMs/lossPercent
Graph buildGraph(const std::vector<std::string>& nodes, const py::list& edges) {
    Graph g;
    for (auto& n : nodes) g.addNode(n);
    for (auto item : edges) {
        py::dict e = item.cast<py::dict>();
        std::string from = e["from"].cast<std::string>();
        std::string to = e["to"].cast<std::string>();
        double bw = e.contains("bandwidthMbps") ? e["bandwidthMbps"].cast<double>() : 10.0;
        double lat = e.contains("latencyMs") ? e["latencyMs"].cast<double>() : 5.0;
        double loss = e.contains("lossPercent") ? e["lossPercent"].cast<double>() : 0.0;
        g.addEdge(from, to, bw, lat, loss);
    }
    return g;
}

// ---------------------------------------------------------------------------
// Path result — returned to Python as a dict
// ---------------------------------------------------------------------------

struct PathResult {
    bool found = false;
    std::vector<std::string> path;
    double totalLatencyMs = 0;
    double bottleneckBandwidthMbps = 0;
    double cumulativeLossPercent = 0;
};

static void computePathMetrics(const Graph& g, PathResult& res) {
    double bottleneck = INF;
    double survival = 1.0;
    for (size_t i = 0; i + 1 < res.path.size(); i++) {
        for (auto& e : g.adj.at(res.path[i])) {
            if (e.to == res.path[i + 1]) {
                bottleneck = std::min(bottleneck, e.bandwidthMbps);
                survival *= (1.0 - e.lossPercent / 100.0);
                break;
            }
        }
    }
    res.bottleneckBandwidthMbps = (bottleneck == INF) ? 0.0 : bottleneck;
    res.cumulativeLossPercent = (1.0 - survival) * 100.0;
}

// ---------------------------------------------------------------------------
// Dijkstra
// ---------------------------------------------------------------------------

PathResult dijkstraImpl(const Graph& g, const std::string& src, const std::string& dst) {
    std::unordered_map<std::string, double> dist;
    std::unordered_map<std::string, std::string> prev;
    for (auto& [node, _] : g.adj) dist[node] = INF;

    PathResult res;
    if (dist.find(src) == dist.end() || dist.find(dst) == dist.end()) return res; // unknown node

    dist[src] = 0;
    using PQItem = std::pair<double, std::string>;
    std::priority_queue<PQItem, std::vector<PQItem>, std::greater<>> pq;
    pq.push({0, src});

    while (!pq.empty()) {
        auto [d, u] = pq.top(); pq.pop();
        if (d > dist[u]) continue;
        for (auto& e : g.adj.at(u)) {
            double nd = d + e.latencyMs;
            if (nd < dist[e.to]) {
                dist[e.to] = nd;
                prev[e.to] = u;
                pq.push({nd, e.to});
            }
        }
    }

    if (dist[dst] == INF) return res;

    std::vector<std::string> path;
    std::string cur = dst;
    while (cur != src) {
        path.push_back(cur);
        if (prev.find(cur) == prev.end()) return res;
        cur = prev[cur];
    }
    path.push_back(src);
    std::reverse(path.begin(), path.end());

    res.found = true;
    res.path = path;
    res.totalLatencyMs = dist[dst];
    computePathMetrics(g, res);
    return res;
}

// ---------------------------------------------------------------------------
// Bellman-Ford
// ---------------------------------------------------------------------------

PathResult bellmanFordImpl(const Graph& g, const std::string& src, const std::string& dst) {
    std::unordered_map<std::string, double> dist;
    std::unordered_map<std::string, std::string> prev;
    for (auto& [node, _] : g.adj) dist[node] = INF;

    PathResult res;
    if (dist.find(src) == dist.end() || dist.find(dst) == dist.end()) return res;

    dist[src] = 0;
    size_t n = g.adj.size();
    for (size_t i = 0; i + 1 < n; i++) {
        bool relaxed = false;
        for (auto& [u, edges] : g.adj) {
            if (dist[u] == INF) continue;
            for (auto& e : edges) {
                double nd = dist[u] + e.latencyMs;
                if (nd < dist[e.to]) {
                    dist[e.to] = nd;
                    prev[e.to] = u;
                    relaxed = true;
                }
            }
        }
        if (!relaxed) break;
    }

    if (dist[dst] == INF) return res;

    std::vector<std::string> path;
    std::string cur = dst;
    while (cur != src) {
        path.push_back(cur);
        if (prev.find(cur) == prev.end()) return res;
        cur = prev[cur];
    }
    path.push_back(src);
    std::reverse(path.begin(), path.end());

    res.found = true;
    res.path = path;
    res.totalLatencyMs = dist[dst];
    computePathMetrics(g, res);
    return res;
}

// ---------------------------------------------------------------------------
// Switching mode simulation
// ---------------------------------------------------------------------------

struct SwitchingResult {
    std::string mode;
    double setupDelayMs = 0;
    double transmissionDelayMs = 0;
    double propagationDelayMs = 0;
    double totalDelayMs = 0;
    double linkUtilizationPercent = 0;
};

SwitchingResult simulateSwitchingImpl(const std::string& mode, const PathResult& path,
                                       double messageSizeKb, int numPacketsIfPacketMode) {
    SwitchingResult r;
    r.mode = mode;
    int hops = (int)path.path.size() - 1;
    if (hops < 1) hops = 1;

    double bwMbps = (path.bottleneckBandwidthMbps <= 0) ? 10.0 : path.bottleneckBandwidthMbps;
    double messageSizeMb = (messageSizeKb * 8.0) / 1000.0;

    if (mode == "circuit") {
        r.setupDelayMs = path.totalLatencyMs * 2;
        r.transmissionDelayMs = (messageSizeMb / bwMbps) * 1000.0;
        r.propagationDelayMs = path.totalLatencyMs;
        r.totalDelayMs = r.setupDelayMs + r.transmissionDelayMs + r.propagationDelayMs;

    } else if (mode == "message") {
        double perHopTx = (messageSizeMb / bwMbps) * 1000.0;
        r.setupDelayMs = 0;
        r.transmissionDelayMs = perHopTx * hops;
        r.propagationDelayMs = path.totalLatencyMs;
        r.totalDelayMs = r.transmissionDelayMs + r.propagationDelayMs;

    } else { // "packet"
        int packets = std::max(1, numPacketsIfPacketMode);
        double packetSizeMb = messageSizeMb / packets;
        double perHopTxPerPacket = (packetSizeMb / bwMbps) * 1000.0;
        r.setupDelayMs = 0;
        r.transmissionDelayMs = perHopTxPerPacket * (packets + hops - 1);
        r.propagationDelayMs = path.totalLatencyMs;
        r.totalDelayMs = r.transmissionDelayMs + r.propagationDelayMs;
    }

    // Estimated link utilization for this transfer: the fraction of the
    // end-to-end simulation time spent actively transmitting data. This is
    // based on the simulated workload rather than a hardcoded mode constant.
    // A true time-window utilization needs a background traffic/load model.
    if (r.totalDelayMs > 0.0) {
        r.linkUtilizationPercent = std::clamp(
            (r.transmissionDelayMs / r.totalDelayMs) * 100.0, 0.0, 100.0);
    } else {
        r.linkUtilizationPercent = 0.0;
    }

    return r;
}

// ---------------------------------------------------------------------------
// Python-facing wrapper functions — these take/return plain Python dicts
// so server.py doesn't need to know about any C++ types.
// ---------------------------------------------------------------------------

py::dict pathResultToDict(const PathResult& r) {
    py::dict d;
    d["found"] = r.found;
    d["path"] = r.path;
    d["totalLatencyMs"] = r.totalLatencyMs;
    d["bottleneckBandwidthMbps"] = r.bottleneckBandwidthMbps;
    d["cumulativeLossPercent"] = r.cumulativeLossPercent;
    return d;
}

py::dict switchingResultToDict(const SwitchingResult& r) {
    py::dict d;
    d["mode"] = r.mode;
    d["setupDelayMs"] = r.setupDelayMs;
    d["transmissionDelayMs"] = r.transmissionDelayMs;
    d["propagationDelayMs"] = r.propagationDelayMs;
    d["totalDelayMs"] = r.totalDelayMs;
    d["linkUtilizationPercent"] = r.linkUtilizationPercent;
    return d;
}

py::dict dijkstra(const std::vector<std::string>& nodes, const py::list& edges,
                   const std::string& src, const std::string& dst) {
    Graph g = buildGraph(nodes, edges);
    return pathResultToDict(dijkstraImpl(g, src, dst));
}

py::dict bellman_ford(const std::vector<std::string>& nodes, const py::list& edges,
                       const std::string& src, const std::string& dst) {
    Graph g = buildGraph(nodes, edges);
    return pathResultToDict(bellmanFordImpl(g, src, dst));
}

// Takes a path result dict (as produced by dijkstra()/bellman_ford() above)
// so Python can compute a route once and simulate multiple switching modes
// against it without recomputing the graph each time.
py::dict simulate_switching(const std::string& mode, const py::dict& routeDict,
                             double messageSizeKb, int numPackets) {
    PathResult pr;
    pr.found = routeDict["found"].cast<bool>();
    pr.path = routeDict["path"].cast<std::vector<std::string>>();
    pr.totalLatencyMs = routeDict["totalLatencyMs"].cast<double>();
    pr.bottleneckBandwidthMbps = routeDict["bottleneckBandwidthMbps"].cast<double>();
    pr.cumulativeLossPercent = routeDict["cumulativeLossPercent"].cast<double>();

    SwitchingResult sr = simulateSwitchingImpl(mode, pr, messageSizeKb, numPackets);
    return switchingResultToDict(sr);
}

// ---------------------------------------------------------------------------
// pybind11 module definition — this is what makes `import netsim_algo` work
// from Python after compiling.
// ---------------------------------------------------------------------------

PYBIND11_MODULE(netsim_algo, m) {
    m.doc() = "NetSim C++ algorithm core: Dijkstra, Bellman-Ford, switching simulation";

    m.def("dijkstra", &dijkstra,
          "Compute shortest path using Dijkstra's algorithm",
          py::arg("nodes"), py::arg("edges"), py::arg("source"), py::arg("destination"));

    m.def("bellman_ford", &bellman_ford,
          "Compute shortest path using Bellman-Ford algorithm",
          py::arg("nodes"), py::arg("edges"), py::arg("source"), py::arg("destination"));

    m.def("simulate_switching", &simulate_switching,
          "Simulate circuit/message/packet switching delay over a computed route",
          py::arg("mode"), py::arg("route"), py::arg("message_size_kb"), py::arg("num_packets"));
}
