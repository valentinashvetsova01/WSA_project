"""
WSA Network Builder

Builds 3 graphs from scraped Telegram data:
  A: Forwarding Network (directed, weighted) — who forwards from whom
  B: URL Co-sharing Network (undirected, weighted) — channels sharing same URLs
  C: Temporal Co-posting Network (undirected, weighted) — channels posting similar URLs in time

Runs Louvain community detection and computes node-level SNA metrics.
Outputs:
  - {prefix}_forwarding.graphml  (for Gephi)
  - {prefix}_url_cosharing.graphml
  - {prefix}_temporal_coposting.graphml
  - {prefix}_node_metrics.csv     (node-level metrics + community labels)
  - {prefix}_summary.txt          (high-level summary)

Usage:
  python network_builder.py --db wsa_data.db --csv wsa_seed_channels.csv --out-prefix wsa
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import community as community_louvain   # python-louvain
import networkx as nx
import pandas as pd
from sklearn.metrics import normalized_mutual_info_score


# Domains we don't count for URL co-sharing (too generic / self-promotion)
URL_BLOCKLIST_DOMAINS = {
    "t.me", "telegram.me", "telegram.org",
    "youtu.be", "www.youtube.com", "youtube.com",
    "twitter.com", "x.com", "vk.com",
    "instagram.com", "www.instagram.com",
}


def load_seed_labels(csv_path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uname = (row.get("username") or "").strip().lstrip("@")
            if not uname:
                continue
            out[uname] = {
                "display_name": row.get("display_name", ""),
                "lean": row.get("lean", ""),
                "subcategory": row.get("subcategory", ""),
                "subscribers": row.get("subscribers", ""),
            }
    return out


def build_forwarding_graph(conn: sqlite3.Connection,
                           seed_usernames: set[str]) -> nx.DiGraph:
    """A → B = channel B forwarded a message ORIGINATING from channel A.
    Weight = number of such forwards."""
    g: nx.DiGraph = nx.DiGraph()
    for ch in seed_usernames:
        g.add_node(ch)
    # message rows where forwarded_from is set tell us: channel_username's post
    # is itself a re-broadcast from forwarded_from
    cur = conn.execute(
        """
        SELECT forwarded_from AS source, channel_username AS target, COUNT(*) AS w
        FROM messages
        WHERE forwarded_from IS NOT NULL AND forwarded_from != channel_username
        GROUP BY forwarded_from, channel_username
        """
    )
    edges_added = 0
    edges_external = 0
    for source, target, w in cur:
        # restrict to edges where BOTH endpoints are in our seed set (in-network)
        if source in seed_usernames and target in seed_usernames:
            g.add_edge(source, target, weight=int(w))
            edges_added += 1
        else:
            edges_external += 1
    print(f"  [forwarding] in-network edges: {edges_added}, external: {edges_external}")
    return g


def build_url_cosharing_graph(conn: sqlite3.Connection,
                              seed_usernames: set[str]) -> nx.Graph:
    """A — B if both channels shared the same URL (excluding social/t.me).
    Weight = number of shared URLs (counted as # of overlapping unique URLs)."""
    # channel -> set of URLs (normalized)
    ch_urls: dict[str, set[str]] = defaultdict(set)
    cur = conn.execute(
        """
        SELECT channel_username, url, domain FROM urls
        """
    )
    for ch, url, domain in cur:
        if ch not in seed_usernames:
            continue
        if not domain or domain.lower() in URL_BLOCKLIST_DOMAINS:
            continue
        # normalize: strip query params/fragments for matching
        norm = url.split("?")[0].split("#")[0].rstrip("/")
        ch_urls[ch].add(norm)

    # Build co-share map: for each URL, list of channels that shared it
    url_to_channels: dict[str, set[str]] = defaultdict(set)
    for ch, urls in ch_urls.items():
        for u in urls:
            url_to_channels[u].add(ch)

    # Co-share counts
    coshare = defaultdict(int)
    for u, chs in url_to_channels.items():
        if len(chs) < 2:
            continue
        chs_list = sorted(chs)
        for i in range(len(chs_list)):
            for j in range(i + 1, len(chs_list)):
                coshare[(chs_list[i], chs_list[j])] += 1

    g: nx.Graph = nx.Graph()
    for ch in seed_usernames:
        g.add_node(ch)
    for (a, b), w in coshare.items():
        g.add_edge(a, b, weight=w)
    print(f"  [url-coshare] edges: {g.number_of_edges()} (unique-URL based)")
    return g


def build_temporal_coposting_graph(conn: sqlite3.Connection,
                                   seed_usernames: set[str],
                                   window_minutes: int = 30) -> nx.Graph:
    """A — B if both channels shared the same URL within {window_minutes} of each other.
    Weight = number of such close-in-time co-shares.
    Much stronger coordination signal than plain co-sharing."""
    cur = conn.execute(
        """
        SELECT u.channel_username, u.url, u.domain, m.timestamp
        FROM urls u
        JOIN messages m ON u.channel_username = m.channel_username AND u.msg_id = m.msg_id
        """
    )
    # url -> list of (channel, ts_epoch)
    url_events: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for ch, url, domain, ts in cur:
        if ch not in seed_usernames:
            continue
        if not domain or domain.lower() in URL_BLOCKLIST_DOMAINS:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            epoch = dt.timestamp()
        except (ValueError, AttributeError):
            continue
        norm = url.split("?")[0].split("#")[0].rstrip("/")
        url_events[norm].append((ch, epoch))

    window_sec = window_minutes * 60
    coshare = defaultdict(int)
    for url, events in url_events.items():
        if len(events) < 2:
            continue
        events_sorted = sorted(events, key=lambda x: x[1])
        for i in range(len(events_sorted)):
            ch_i, t_i = events_sorted[i]
            for j in range(i + 1, len(events_sorted)):
                ch_j, t_j = events_sorted[j]
                if t_j - t_i > window_sec:
                    break
                if ch_i == ch_j:
                    continue
                pair = tuple(sorted([ch_i, ch_j]))
                coshare[pair] += 1

    g: nx.Graph = nx.Graph()
    for ch in seed_usernames:
        g.add_node(ch)
    for (a, b), w in coshare.items():
        g.add_edge(a, b, weight=w)
    print(f"  [temporal-copost] edges (window={window_minutes}min): {g.number_of_edges()}")
    return g


def annotate_with_metadata(g, seed_labels: dict[str, dict]) -> None:
    """Add display_name, lean, subcategory as node attributes."""
    for node in g.nodes():
        meta = seed_labels.get(node, {})
        g.nodes[node]["display_name"] = meta.get("display_name", node)
        g.nodes[node]["lean"] = meta.get("lean", "unknown")
        g.nodes[node]["subcategory"] = meta.get("subcategory", "")


def compute_node_metrics(g_directed: nx.DiGraph, g_url: nx.Graph,
                         g_temp: nx.Graph, seed_labels: dict[str, dict]) -> pd.DataFrame:
    nodes = sorted(set(g_directed.nodes()) | set(g_url.nodes()) | set(g_temp.nodes()))
    rows = []

    in_deg = dict(g_directed.in_degree(weight="weight"))
    out_deg = dict(g_directed.out_degree(weight="weight"))
    pr = nx.pagerank(g_directed, weight="weight") if g_directed.number_of_edges() else {}
    try:
        bet = nx.betweenness_centrality(g_directed, weight=None, normalized=True)
    except Exception:
        bet = {n: 0.0 for n in g_directed.nodes()}

    try:
        eig = nx.eigenvector_centrality_numpy(g_directed, weight="weight")
    except Exception:
        eig = {n: 0.0 for n in g_directed.nodes()}

    # Communities on undirected projection of forwarding
    fwd_undirected = g_directed.to_undirected()
    partition = community_louvain.best_partition(fwd_undirected, weight="weight",
                                                  random_state=42) if fwd_undirected.number_of_edges() else {}

    for n in nodes:
        meta = seed_labels.get(n, {})
        rows.append({
            "username": n,
            "display_name": meta.get("display_name", n),
            "lean_truth": meta.get("lean", ""),
            "subcategory": meta.get("subcategory", ""),
            "in_degree_w": in_deg.get(n, 0),
            "out_degree_w": out_deg.get(n, 0),
            "pagerank": pr.get(n, 0.0),
            "betweenness": bet.get(n, 0.0),
            "eigenvector": eig.get(n, 0.0),
            "url_coshare_degree": g_url.degree(n, weight="weight") if n in g_url else 0,
            "temporal_copost_degree": g_temp.degree(n, weight="weight") if n in g_temp else 0,
            "louvain_community": partition.get(n, -1),
        })
    df = pd.DataFrame(rows)
    return df


def compute_nmi_vs_truth(node_metrics: pd.DataFrame) -> float:
    """NMI between Louvain communities and lean_truth labels."""
    valid = node_metrics[node_metrics["lean_truth"].isin(["pro", "anti", "mixed", "neutral"])]
    valid = valid[valid["louvain_community"] >= 0]
    if len(valid) < 2:
        return float("nan")
    true_labels = valid["lean_truth"].tolist()
    pred_labels = valid["louvain_community"].tolist()
    return float(normalized_mutual_info_score(true_labels, pred_labels))


def write_summary(out: Path, g_fwd: nx.DiGraph, g_url: nx.Graph, g_temp: nx.Graph,
                  metrics: pd.DataFrame, nmi: float) -> None:
    lines = []
    lines.append(f"WSA Network Summary | generated {datetime.now(timezone.utc).isoformat()}\n")
    lines.append(f"\n== Forwarding network (A) ==")
    lines.append(f"  nodes: {g_fwd.number_of_nodes()}")
    lines.append(f"  edges: {g_fwd.number_of_edges()}")
    if g_fwd.number_of_edges():
        densities = nx.density(g_fwd)
        lines.append(f"  density: {densities:.4f}")
    lines.append(f"\n== URL co-sharing (B) ==")
    lines.append(f"  nodes: {g_url.number_of_nodes()}")
    lines.append(f"  edges: {g_url.number_of_edges()}")
    lines.append(f"\n== Temporal co-posting (C, 30-min window) ==")
    lines.append(f"  nodes: {g_temp.number_of_nodes()}")
    lines.append(f"  edges: {g_temp.number_of_edges()}")
    n_communities = metrics["louvain_community"].nunique() if "louvain_community" in metrics else 0
    lines.append(f"\n== Community detection (Louvain on forwarding undirected) ==")
    lines.append(f"  communities: {n_communities}")
    lines.append(f"  NMI vs ground-truth lean: {nmi:.4f}")
    lines.append(f"\n== Top 10 by PageRank ==")
    for _, r in metrics.nlargest(10, "pagerank").iterrows():
        lines.append(f"  {r['username']:<25} pr={r['pagerank']:.4f}  lean={r['lean_truth']}  comm={r['louvain_community']}")
    lines.append(f"\n== Top 10 by Betweenness (potential bridges) ==")
    for _, r in metrics.nlargest(10, "betweenness").iterrows():
        lines.append(f"  {r['username']:<25} bet={r['betweenness']:.4f}  lean={r['lean_truth']}  comm={r['louvain_community']}")
    lines.append(f"\n== Community composition ==")
    if n_communities:
        comp = metrics.groupby(["louvain_community", "lean_truth"]).size().unstack(fill_value=0)
        lines.append(comp.to_string())
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True, help="Seed channels CSV")
    p.add_argument("--out-prefix", default="wsa")
    p.add_argument("--out-dir", type=Path, default=Path("."))
    p.add_argument("--temporal-window-min", type=int, default=30)
    args = p.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    seed_labels = load_seed_labels(args.csv)
    seed_usernames = set(seed_labels.keys())
    print(f"Loaded {len(seed_usernames)} seed channels from CSV")

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = None

    print("\nBuilding graphs...")
    g_fwd = build_forwarding_graph(conn, seed_usernames)
    g_url = build_url_cosharing_graph(conn, seed_usernames)
    g_temp = build_temporal_coposting_graph(conn, seed_usernames, args.temporal_window_min)

    for g in (g_fwd, g_url, g_temp):
        annotate_with_metadata(g, seed_labels)

    print("\nComputing metrics...")
    metrics = compute_node_metrics(g_fwd, g_url, g_temp, seed_labels)
    nmi = compute_nmi_vs_truth(metrics)
    print(f"NMI(Louvain, lean_truth) = {nmi:.4f}")

    pref = args.out_dir / args.out_prefix
    nx.write_graphml(g_fwd, str(pref) + "_forwarding.graphml")
    nx.write_graphml(g_url, str(pref) + "_url_cosharing.graphml")
    nx.write_graphml(g_temp, str(pref) + "_temporal_coposting.graphml")
    metrics.to_csv(str(pref) + "_node_metrics.csv", index=False)
    write_summary(Path(str(pref) + "_summary.txt"), g_fwd, g_url, g_temp, metrics, nmi)

    print(f"\nWrote:")
    print(f"  {pref}_forwarding.graphml")
    print(f"  {pref}_url_cosharing.graphml")
    print(f"  {pref}_temporal_coposting.graphml")
    print(f"  {pref}_node_metrics.csv")
    print(f"  {pref}_summary.txt")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
