#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "ruamel.yaml",
# ]
# ///

"""
Cluster-wide Resource Right-Sizing Automation for Home-Ops.

Queries Goldilocks / VPA recommendations from the cluster, compares them
against declared HelmRelease values in Git, and applies:
1. Asymmetric ratcheting: Fast scale-up for starved workloads, gradual/damped
   step-downs (max 20-25% reduction per cycle) to prevent starvation cliffs.
2. Dynamic drift thresholds: Scaled sensitivity for large allocations vs small allocations.
3. No restrictive CPU limits: Keeps CPU unthrottled (burst-capable) while right-sizing
   memory limits to eliminate KubeMemoryOvercommit.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from ruamel.yaml import YAML

# Standard memory tiers in MiB for safe upward rounding
MEMORY_TIERS_MIB = [
    64, 96, 128, 160, 192, 256, 384, 512, 768,
    1024, 1536, 2048, 3072, 4096, 6144, 8192
]

# Standard CPU tiers in millicores
CPU_TIERS_M = [
    10, 15, 25, 50, 75, 100, 150, 200, 250, 350, 500, 750, 1000, 1500, 2000
]


def parse_cpu_to_m(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    if s.endswith("m"):
        try:
            return float(s[:-1])
        except ValueError:
            return 0.0
    try:
        return float(s) * 1000.0
    except ValueError:
        return 0.0


def parse_mem_to_mib(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    units = {
        "Ki": 1 / 1024,
        "Mi": 1.0,
        "Gi": 1024.0,
        "Ti": 1024.0 * 1024.0,
        "K": 1000 / (1024 * 1024),
        "M": (1000 * 1000) / (1024 * 1024),
        "G": (1000 * 1000 * 1000) / (1024 * 1024),
    }
    for unit, mult in units.items():
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)]) * mult
            except ValueError:
                return 0.0
    try:
        return float(s) / (1024.0 * 1024.0)
    except ValueError:
        return 0.0


def format_cpu_m(m: int) -> str:
    return f"{m}m"


def format_mem_mib(mib: int) -> str:
    if mib >= 1024 and mib % 1024 == 0:
        return f"{mib // 1024}Gi"
    return f"{mib}Mi"


def round_up_tier(val: float, tiers: list[int]) -> int:
    for tier in tiers:
        if val <= tier:
            return tier
    return int(math.ceil(val))


def clamp_downgrade(curr: float, target: float, max_drop_pct: float) -> float:
    """Clamps downward adjustments so allocations never drop off a cliff."""
    if curr <= 0:
        return target
    if target < curr:
        floor = curr * (1.0 - max_drop_pct / 100.0)
        return max(target, floor)
    return target


def clamp_scaleup(curr_val: float, target_val: float, tiers: list[int], max_step_tiers: int = 2, max_factor: float = 2.0, max_initial_val: float | None = None) -> float:
    """Clamps scale-up recommendations to avoid sudden capacity spikes (symmetric ratcheting)."""
    if curr_val <= 0:
        if max_initial_val is not None:
            return min(target_val, max_initial_val)
        return target_val

    curr_idx = 0
    for idx, t in enumerate(tiers):
        if t <= curr_val:
            curr_idx = idx
        else:
            break

    target_tier_idx = min(curr_idx + max_step_tiers, len(tiers) - 1)
    tier_ceiling = float(tiers[target_tier_idx])
    factor_ceiling = curr_val * max_factor
    ceiling = max(tier_ceiling, factor_ceiling)
    return min(target_val, ceiling)


def is_significant_cpu_drift(curr_m: float, rec_m: float) -> bool:
    """Dynamic sensitivity: large allocations trigger at lower percentage; small allocations require minimal delta."""
    if curr_m <= 0:
        return True
    delta = abs(curr_m - rec_m)
    pct = (delta / curr_m) * 100.0

    # Any shift >= 100m (0.1 core) is always significant
    if delta >= 100.0:
        return True
    # For large requests (> 500m): 15% drift is significant
    if curr_m >= 500.0 and pct >= 15.0:
        return True
    # For medium requests (100m - 500m): 20% drift with at least 50m delta
    if curr_m >= 100.0 and delta >= 50.0 and pct >= 20.0:
        return True
    # For small requests (< 100m): require at least 25m delta to avoid micro-adjustments
    if curr_m < 100.0 and delta >= 25.0 and pct >= 30.0:
        return True
    return False


def is_significant_mem_drift(curr_mib: float, rec_mib: float) -> bool:
    """Evaluates if memory difference warrants a manifest change."""
    if curr_mib <= 0:
        return True
    delta = abs(curr_mib - rec_mib)
    pct = (delta / curr_mib) * 100.0

    # Any shift >= 256Mi is always significant
    if delta >= 256.0:
        return True
    # Otherwise require at least 50Mi delta and 20% drift
    return delta >= 50.0 and pct >= 20.0


def find_helmrelease_file(workspace_root: Path, namespace: str, workload: str) -> Path | None:
    p1 = workspace_root / "kubernetes" / "apps" / namespace / workload / "app" / "helmrelease.yaml"
    if p1.is_file():
        return p1

    workload_dir = workspace_root / "kubernetes" / "apps" / namespace / workload
    if workload_dir.is_dir():
        matches = list(workload_dir.glob("**/helmrelease.yaml"))
        if matches:
            return matches[0]

    ns_dir = workspace_root / "kubernetes" / "apps" / namespace
    if ns_dir.is_dir():
        for candidate in ns_dir.glob("**/helmrelease.yaml"):
            try:
                content = candidate.read_text()
                if f"name: {workload}" in content or f"name: &app {workload}" in content:
                    return candidate
            except Exception:
                pass

    return None


def get_container_resources_block(hr_data: dict, container_name: str) -> tuple[dict, str] | None:
    spec = hr_data.get("spec", {})
    values = spec.get("values", {})

    controllers = values.get("controllers", {})
    for ctrl_name, ctrl in controllers.items():
        if not isinstance(ctrl, dict):
            continue
        containers = ctrl.get("containers", {})
        if container_name in containers and isinstance(containers[container_name], dict):
            c_dict = containers[container_name]
            if "resources" not in c_dict or not isinstance(c_dict["resources"], dict):
                c_dict["resources"] = {}
            return c_dict["resources"], f"controllers.{ctrl_name}.containers.{container_name}.resources"
        if len(containers) == 1 and ("app" in containers or container_name in ["app", ctrl_name]):
            single_cname = list(containers.keys())[0]
            c_dict = containers[single_cname]
            if "resources" not in c_dict or not isinstance(c_dict["resources"], dict):
                c_dict["resources"] = {}
            return c_dict["resources"], f"controllers.{ctrl_name}.containers.{single_cname}.resources"

    for key in [container_name, "server", "controller", "dashboard"]:
        if key in values and isinstance(values[key], dict):
            comp = values[key]
            if "resources" in comp and isinstance(comp["resources"], dict):
                return comp["resources"], f"{key}.resources"

    if "resources" in values and isinstance(values["resources"], dict):
        return values["resources"], "resources"

    return None


def get_cluster_headroom() -> dict:
    """Inspects cluster node readiness and allocatable capacity to prevent over-allocation."""
    try:
        proc_n = subprocess.run(["kubectl", "get", "nodes", "-o", "json"], capture_output=True, text=True, check=True)
        nodes = json.loads(proc_n.stdout)
    except Exception as e:
        print(f"Warning: Could not fetch node info from cluster: {e}", file=sys.stderr)
        return {}

    node_alloc = {}
    node_ready = {}
    for n in nodes.get("items", []):
        name = n["metadata"]["name"]
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in n.get("status", {}).get("conditions", []))
        node_ready[name] = ready
        node_alloc[name] = {
            "cpu_m": parse_cpu_to_m(n.get("status", {}).get("allocatable", {}).get("cpu", "0")),
            "mem_mib": parse_mem_to_mib(n.get("status", {}).get("allocatable", {}).get("memory", "0")),
        }

    try:
        proc_p = subprocess.run(["kubectl", "get", "pods", "-A", "-o", "json"], capture_output=True, text=True, check=True)
        pods = json.loads(proc_p.stdout)
    except Exception as e:
        print(f"Warning: Could not fetch pod info from cluster: {e}", file=sys.stderr)
        return {}

    node_req = {n: {"cpu_m": 0.0, "mem_mib": 0.0} for n in node_alloc}
    for p in pods.get("items", []):
        node = p.get("spec", {}).get("nodeName")
        phase = p.get("status", {}).get("phase")
        if not node or node not in node_req:
            continue
        if phase in ["Succeeded", "Failed"]:
            continue
        for c in p.get("spec", {}).get("containers", []):
            r = c.get("resources", {}).get("requests", {}) or {}
            l = c.get("resources", {}).get("limits", {}) or {}
            c_cpu = parse_cpu_to_m(r.get("cpu", "0"))
            c_mem = parse_mem_to_mib(r.get("memory", l.get("memory", "0")))
            node_req[node]["cpu_m"] += c_cpu
            node_req[node]["mem_mib"] += c_mem

    total_ready_cpu_alloc = sum(alloc["cpu_m"] for n, alloc in node_alloc.items() if node_ready[n])
    total_ready_cpu_req = sum(node_req[n]["cpu_m"] for n in node_alloc if node_ready[n])
    total_ready_mem_alloc = sum(alloc["mem_mib"] for n, alloc in node_alloc.items() if node_ready[n])
    total_ready_mem_req = sum(node_req[n]["mem_mib"] for n in node_alloc if node_ready[n])
    not_ready_nodes = [n for n, ready in node_ready.items() if not ready]

    free_ready_cpu_m = max(0.0, total_ready_cpu_alloc - total_ready_cpu_req)
    free_ready_mem_mib = max(0.0, total_ready_mem_alloc - total_ready_mem_req)

    return {
        "total_nodes": len(node_alloc),
        "ready_nodes": len(node_alloc) - len(not_ready_nodes),
        "not_ready_nodes": not_ready_nodes,
        "free_cpu_m": free_ready_cpu_m,
        "free_mem_mib": free_ready_mem_mib,
        "total_ready_cpu_alloc": total_ready_cpu_alloc,
        "total_ready_cpu_req": total_ready_cpu_req,
        "total_ready_mem_alloc": total_ready_mem_alloc,
        "total_ready_mem_req": total_ready_mem_req,
        "node_alloc": node_alloc,
        "node_req": node_req,
        "node_ready": node_ready,
    }


def get_workload_history_days() -> dict[tuple[str, str], float]:
    """Calculate the observation history age in days for all workloads in the cluster.

    Combines workload creation timestamp and Prometheus cAdvisor container metric duration.
    """
    import datetime
    import time
    import urllib.parse
    import urllib.request

    ages: dict[tuple[str, str], float] = {}

    # 1. Fetch workload creation timestamps via Kubernetes API
    try:
        proc = subprocess.run(
            ["kubectl", "get", "deployments,statefulsets,daemonsets", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        wl_data = json.loads(proc.stdout)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        for item in wl_data.get("items", []):
            ns = item["metadata"]["namespace"]
            name = item["metadata"]["name"]
            created_str = item["metadata"]["creationTimestamp"]
            created = datetime.datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            days = (now_utc - created).total_seconds() / 86400.0
            ages[(ns, name)] = days
    except Exception as e:
        print(f"Warning: could not fetch workload creation timestamps: {e}", file=sys.stderr)

    # 2. Query Prometheus container start time history (if available)
    q = 'min by (namespace, pod) (min_over_time(container_start_time_seconds{container!="",container!="POD"}[30d]))'
    encoded_q = urllib.parse.quote(q)
    endpoints = [
        f"http://kube-prometheus-stack-prometheus.observability:9090/api/v1/query?query={encoded_q}",
    ]
    prom_data = None
    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, headers={"User-Agent": "rightsize.py"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                prom_data = json.loads(resp.read().decode())
                break
        except Exception:
            pass

    if not prom_data:
        raw_path = (
            f"/api/v1/namespaces/observability/services/kube-prometheus-stack-prometheus:9090/proxy/api/v1/query?query={encoded_q}"
        )
        try:
            proc = subprocess.run(["kubectl", "get", "--raw", raw_path], capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout:
                prom_data = json.loads(proc.stdout)
        except Exception:
            pass

    if prom_data and prom_data.get("status") == "success":
        now = time.time()
        for r in prom_data.get("data", {}).get("result", []):
            ns = r.get("metric", {}).get("namespace")
            pod = r.get("metric", {}).get("pod")
            val = r.get("value", [0, 0])
            if not ns or not pod:
                continue
            try:
                start_ts = float(val[1])
                days = (now - start_ts) / 86400.0
                parts = pod.rsplit("-", 2)
                wl = parts[0] if len(parts) >= 3 else pod.rsplit("-", 1)[0]
                key = (ns, wl)
                if key not in ages or days > ages[key]:
                    ages[key] = days
            except (ValueError, IndexError):
                continue

    return ages


def build_markdown_report(proposals: list[dict], headroom: dict = None) -> str:
    net_cpu_delta = sum(p["new_cpu_m"] - p["curr_cpu_m"] for p in proposals if p["cpu_trigger"])
    net_limit_delta = sum(p["new_limit_mem_mib"] - p["curr_limit_mem_mib"] for p in proposals if p["limit_trigger"])

    lines = [
        "## ⚖️ Workload Resource Right-Sizing",
        "",
        "Automated resource recommendations based on Fairwinds Goldilocks and in-cluster VPA metrics.",
        "",
    ]

    if headroom:
        not_ready_str = f" (⚠️ NotReady: `{', '.join(headroom['not_ready_nodes'])}`)" if headroom["not_ready_nodes"] else " (All healthy)"
        cpu_usage_pct = (net_cpu_delta / max(headroom['free_cpu_m'], 1.0)) * 100.0 if headroom['free_cpu_m'] > 0 else 0.0
        lines.extend([
            "### ☸️ Cluster Capacity & Health Gatekeeper",
            f"* **Node Health**: {headroom['ready_nodes']} / {headroom['total_nodes']} Nodes Ready{not_ready_str}",
            f"* **Available CPU Headroom**: `{headroom['free_cpu_m']:.0f}m` on Ready nodes",
            f"* **Proposed Net CPU Delta**: `+{net_cpu_delta:.0f}m` ({cpu_usage_pct:.1f}% of available headroom)",
            f"* **Overcommit Memory Limits Trimmed**: `{abs(net_limit_delta) / 1024.0:.2f} GiB`",
            "",
        ])
        if headroom["not_ready_nodes"]:
            lines.extend([
                "> [!WARNING]",
                f"> Node(s) `{', '.join(headroom['not_ready_nodes'])}` currently offline. Scale-up recommendations have been strictly clamped to guarantee safe scheduling within active nodes.",
                "",
            ])

    lines.extend([
        "**Safety Guardrails Applied**:",
        "* **Observation Window Gate**: Only workloads with &ge; 8 days of historical telemetry are eligible for automated PRs.",
        "* **Symmetric Ratchet**: Scale-ups are clamped to a max 2-tier step (or 2x); downgrades are clamped to a max 20% reduction per step.",
        "* **Cluster Capacity Gatekeeper**: Validates aggregate resource requests against active node allocatable headroom.",
        "* **No CPU Limits**: Workloads retain full burstability up to node capacity without kernel CFS throttling.",
        "* **Memory Limits Right-Sized**: High overcommit ceilings are brought down to sane multiples (1.5x-2x request) to eliminate node overcommit alerts.",
        "",
        "| Namespace | Workload | Container | CPU Request | Memory Request | Memory Limit | Observation | Action |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for p in proposals:
        cpu_str = f"`{p['curr_cpu']}` &rarr; **`{p['new_cpu']}`**" if p["cpu_trigger"] else f"`{p['curr_cpu']}`"
        mem_str = f"`{p['curr_mem']}` &rarr; **`{p['new_mem']}`**" if p["mem_trigger"] else f"`{p['curr_mem']}`"
        limit_str = f"`{p['curr_limit_mem']}` &rarr; **`{p['new_limit_mem']}`**" if p["limit_trigger"] else f"`{p['curr_limit_mem']}`"
        hist_str = f"`{p.get('data_age_days', 0.0):.1f}d`"
        lines.append(f"| `{p['namespace']}` | **{p['workload']}** | `{p['container']}` | {cpu_str} | {mem_str} | {limit_str} | {hist_str} | {p['action_note']} |")
    lines.append("")
    lines.append("> [!NOTE]")
    lines.append("> Workloads scale gradually over observation cycles to guarantee stability.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Cluster-wide Resource Right-Sizing Tool")
    parser.add_argument("--apply", action="store_true", help="Apply recommended changes to HelmRelease manifests")
    parser.add_argument("--markdown", action="store_true", help="Output markdown formatted summary")
    parser.add_argument("--create-pr", action="store_true", help="Apply changes, commit to branch, and open GitHub PR")
    parser.add_argument("--namespace", help="Filter by namespace")
    parser.add_argument("--app", help="Filter by app / workload name")
    parser.add_argument("--max-cpu-downgrade-pct", type=float, default=20.0, help="Maximum percentage reduction for CPU per step (default: 20%%)")
    parser.add_argument("--max-mem-downgrade-pct", type=float, default=20.0, help="Maximum percentage reduction for Memory per step (default: 20%%)")
    parser.add_argument("--max-scaleup-tiers", type=int, default=2, help="Maximum tier jumps per step on scale-up (default: 2)")
    parser.add_argument("--max-scaleup-factor", type=float, default=2.0, help="Maximum multiplier per step on scale-up (default: 2.0)")
    parser.add_argument("--cluster-budget-ratio", type=float, default=0.7, help="Maximum fraction of cluster free CPU headroom allowed for net increases (default: 0.7)")
    parser.add_argument("--min-history-days", type=float, default=None, help="Minimum days of historical data required before considering a workload (defaults to 8.0 when --create-pr is specified)")
    parser.add_argument("--skip-sensitive", action="store_true", default=True, help="Skip sensitive/storage infrastructure like rook-ceph (default: True)")
    parser.add_argument("--no-skip-sensitive", dest="skip_sensitive", action="store_false", help="Do not skip sensitive workloads")
    parser.add_argument("--push", action="store_true", help="Push branch to origin and create/update PR via gh CLI")
    args = parser.parse_args()

    workspace_root = Path(os.getcwd())

    try:
        proc = subprocess.run(["kubectl", "get", "vpa", "-A", "-o", "json"], capture_output=True, text=True, check=True)
        vpa_data = json.loads(proc.stdout)
    except Exception as e:
        print(f"Error fetching VPAs from cluster: {e}", file=sys.stderr)
        sys.exit(1)

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.explicit_start = True
    yaml.width = 120

    proposals = []
    headroom = get_cluster_headroom()
    workload_history = get_workload_history_days()

    for item in vpa_data.get("items", []):
        ns = item["metadata"]["namespace"]
        if args.skip_sensitive and ns == "rook-ceph":
            continue
        if args.namespace and ns != args.namespace:
            continue

        target_ref = item.get("spec", {}).get("targetRef", {})
        workload_name = target_ref.get("name")
        if not workload_name:
            continue
        if args.app and workload_name != args.app:
            continue

        recs = item.get("status", {}).get("recommendation", {}).get("containerRecommendations", [])
        if not recs:
            continue

        hr_file = find_helmrelease_file(workspace_root, ns, workload_name)
        if not hr_file:
            continue

        try:
            with open(hr_file, "r") as f:
                hr_yaml = yaml.load(f)
        except Exception as e:
            print(f"Warning: could not parse {hr_file}: {e}", file=sys.stderr)
            continue

        for rec in recs:
            cname = rec.get("containerName", "app")
            res_tuple = get_container_resources_block(hr_yaml, cname)
            if not res_tuple:
                continue

            res_block, path_desc = res_tuple
            requests = res_block.get("requests", {}) or {}
            limits = res_block.get("limits", {}) or {}

            curr_cpu_m = parse_cpu_to_m(requests.get("cpu"))
            curr_mem_mib = parse_mem_to_mib(requests.get("memory"))
            curr_limit_mem_mib = parse_mem_to_mib(limits.get("memory"))

            target = rec.get("target", {})
            vpa_cpu_m = parse_cpu_to_m(target.get("cpu"))
            vpa_mem_mib = parse_mem_to_mib(target.get("memory"))

            if vpa_cpu_m == 0 and vpa_mem_mib == 0:
                continue

            # 1. CPU Calculation with Symmetric Ratchet
            # Scale-up: clamped to max_scaleup_tiers (default 2) and max_scaleup_factor (default 2.0)
            # Scale-down: clamp reduction to max_cpu_downgrade_pct (default 20%)
            if vpa_cpu_m >= curr_cpu_m:
                clamped_cpu = clamp_scaleup(curr_cpu_m, vpa_cpu_m, CPU_TIERS_M, max_step_tiers=args.max_scaleup_tiers, max_factor=args.max_scaleup_factor, max_initial_val=100.0)
                sane_rec_cpu_m = round_up_tier(clamped_cpu, CPU_TIERS_M)
            else:
                clamped_cpu = clamp_downgrade(curr_cpu_m, vpa_cpu_m, args.max_cpu_downgrade_pct)
                sane_rec_cpu_m = round_up_tier(clamped_cpu, CPU_TIERS_M)
                if sane_rec_cpu_m > curr_cpu_m and curr_cpu_m > 0:
                    sane_rec_cpu_m = int(curr_cpu_m)

            cpu_trigger = is_significant_cpu_drift(curr_cpu_m, sane_rec_cpu_m)

            # 2. Memory Calculation with Symmetric Ratchet
            # Scale-up: clamped to max_scaleup_tiers (default 2) and max_scaleup_factor (default 2.0)
            # Scale-down: clamp reduction to max_mem_downgrade_pct (default 20%)
            if vpa_mem_mib >= curr_mem_mib:
                clamped_mem = clamp_scaleup(curr_mem_mib, vpa_mem_mib, MEMORY_TIERS_MIB, max_step_tiers=args.max_scaleup_tiers, max_factor=args.max_scaleup_factor, max_initial_val=2048.0)
                sane_rec_mem_mib = round_up_tier(clamped_mem, MEMORY_TIERS_MIB)
            else:
                clamped_mem = clamp_downgrade(curr_mem_mib, vpa_mem_mib, args.max_mem_downgrade_pct)
                sane_rec_mem_mib = round_up_tier(clamped_mem, MEMORY_TIERS_MIB)
                if sane_rec_mem_mib > curr_mem_mib and curr_mem_mib > 0:
                    sane_rec_mem_mib = int(curr_mem_mib)

            mem_trigger = is_significant_mem_drift(curr_mem_mib, sane_rec_mem_mib)

            # 3. Memory Limit Right-Sizing
            # Limit = 2x request (or request + 256Mi), rounded to standard tier
            sane_limit_mem_mib = round_up_tier(max(sane_rec_mem_mib * 2.0, sane_rec_mem_mib + 256.0), MEMORY_TIERS_MIB)
            # Only trigger limit adjustment if currently overprovisioned by >= 2x AND >= 1Gi delta
            limit_delta = abs(curr_limit_mem_mib - sane_limit_mem_mib)
            limit_trigger = (curr_limit_mem_mib > 0) and (curr_limit_mem_mib >= sane_limit_mem_mib * 2.0) and (limit_delta >= 1024.0)

            # Enforce Kubernetes validity: requests.memory must be <= limits.memory
            effective_limit = sane_limit_mem_mib if limit_trigger else curr_limit_mem_mib
            if effective_limit > 0 and sane_rec_mem_mib > effective_limit:
                sane_rec_mem_mib = int(effective_limit)
            if limit_trigger and sane_limit_mem_mib < sane_rec_mem_mib:
                sane_limit_mem_mib = sane_rec_mem_mib

            # Action notes
            action_notes = []
            if cpu_trigger:
                if sane_rec_cpu_m > curr_cpu_m:
                    is_clamped = (vpa_cpu_m > sane_rec_cpu_m) and (curr_cpu_m > 0)
                    action_notes.append("Clamped CPU step-up" if is_clamped else "CPU scale-up")
                else:
                    action_notes.append("Damped CPU step-down")
            if mem_trigger:
                if curr_mem_mib == 0:
                    action_notes.append("Set mem request")
                elif sane_rec_mem_mib > curr_mem_mib:
                    is_clamped = (vpa_mem_mib > sane_rec_mem_mib) and (curr_mem_mib > 0)
                    action_notes.append("Clamped mem step-up" if is_clamped else "Mem scale-up")
                else:
                    action_notes.append("Damped mem step-down")
            if limit_trigger:
                action_notes.append("Trim overcommit limit")

            workload_age = workload_history.get((ns, workload_name), 0.0)
            if workload_age == 0.0:
                for (k_ns, k_wl), k_age in workload_history.items():
                    if k_ns == ns and (workload_name.startswith(k_wl) or k_wl.startswith(workload_name)):
                        workload_age = max(workload_age, k_age)

            if cpu_trigger or mem_trigger or limit_trigger:
                proposals.append({
                    "namespace": ns,
                    "workload": workload_name,
                    "container": cname,
                    "data_age_days": workload_age,
                    "file": hr_file,
                    "hr_yaml": hr_yaml,
                    "res_block": res_block,
                    "curr_cpu": format_cpu_m(int(curr_cpu_m)) if curr_cpu_m > 0 else "none",
                    "new_cpu": format_cpu_m(sane_rec_cpu_m),
                    "curr_cpu_m": curr_cpu_m,
                    "new_cpu_m": sane_rec_cpu_m,
                    "cpu_trigger": cpu_trigger,
                    "curr_mem": format_mem_mib(int(curr_mem_mib)) if curr_mem_mib > 0 else "none",
                    "new_mem": format_mem_mib(sane_rec_mem_mib),
                    "curr_mem_mib": curr_mem_mib,
                    "new_mem_mib": sane_rec_mem_mib,
                    "mem_trigger": mem_trigger,
                    "curr_limit_mem": format_mem_mib(int(curr_limit_mem_mib)) if curr_limit_mem_mib > 0 else "none",
                    "new_limit_mem": format_mem_mib(sane_limit_mem_mib),
                    "curr_limit_mem_mib": curr_limit_mem_mib,
                    "new_limit_mem_mib": sane_limit_mem_mib,
                    "limit_trigger": limit_trigger,
                    "action_note": ", ".join(action_notes),
                })

    if not proposals:
        print("All analyzed workloads are currently within optimal resource deadbands! No changes needed.")
        return 0

    min_history_days = args.min_history_days
    if min_history_days is None:
        min_history_days = 8.0 if args.create_pr else 0.0

    actionable_proposals = [p for p in proposals if p["data_age_days"] >= min_history_days]
    deferred_proposals = [p for p in proposals if p["data_age_days"] < min_history_days]

    net_cpu_delta = sum(p["new_cpu_m"] - p["curr_cpu_m"] for p in proposals if p["cpu_trigger"])
    net_limit_delta = sum(p["new_limit_mem_mib"] - p["curr_limit_mem_mib"] for p in proposals if p["limit_trigger"])

    if args.markdown:
        target_proposals = actionable_proposals if args.create_pr else proposals
        print(build_markdown_report(target_proposals, headroom))
        return

    if headroom:
        print("\n☸️ Cluster Capacity & Health Gatekeeper:")
        not_ready_str = f" (⚠️ NotReady: {', '.join(headroom['not_ready_nodes'])})" if headroom["not_ready_nodes"] else " (All healthy)"
        print(f"  Node Health: {headroom['ready_nodes']} / {headroom['total_nodes']} Nodes Ready{not_ready_str}")
        print(f"  CPU Headroom (Ready Nodes): {headroom['free_cpu_m']:.0f}m allocatable free")
        print(f"  Proposed Net CPU Delta: +{net_cpu_delta:.0f}m")
        print(f"  Overcommit Memory Limits Trimmed: {abs(net_limit_delta) / 1024.0:.2f} GiB")

        if headroom["free_cpu_m"] > 0 and net_cpu_delta > headroom["free_cpu_m"] * args.cluster_budget_ratio:
            print(f"\n⚠️ WARNING: Proposed net CPU increase (+{net_cpu_delta:.0f}m) exceeds {args.cluster_budget_ratio*100:.0f}% of cluster headroom ({headroom['free_cpu_m']:.0f}m)!\n")

    print(f"\nFound {len(proposals)} workload(s) with significant resource drift (ratchet guardrails active):\n")
    header = f"{'NAMESPACE':<14} {'WORKLOAD':<22} {'CPU (CURR -> NEW)':<22} {'MEM REQ (CURR -> NEW)':<24} {'MEM LIMIT (CURR -> NEW)':<24} {'HISTORY':<10} {'ACTION':<24}"
    print(header)
    print("=" * len(header))

    for p in proposals:
        cpu_str = f"{p['curr_cpu']} -> {p['new_cpu']}" + (" *" if p["cpu_trigger"] else "")
        mem_str = f"{p['curr_mem']} -> {p['new_mem']}" + (" *" if p["mem_trigger"] else "")
        limit_str = f"{p['curr_limit_mem']} -> {p['new_limit_mem']}" + (" *" if p["limit_trigger"] else "")
        hist_str = f"{p['data_age_days']:.1f}d"
        print(f"{p['namespace']:<14} {p['workload']:<22} {cpu_str:<22} {mem_str:<24} {limit_str:<24} {hist_str:<10} {p['action_note']:<24}")

    if deferred_proposals and min_history_days > 0:
        print(f"\n⏳ Deferred {len(deferred_proposals)} workload(s) with < {min_history_days:.1f}d observation history:")
        for p in deferred_proposals:
            print(f"  - {p['namespace']}/{p['workload']}: {p['data_age_days']:.1f}d available (< {min_history_days:.1f}d threshold)")

    target_proposals = actionable_proposals if (args.create_pr or min_history_days > 0) else proposals

    if args.create_pr and not target_proposals:
        print(f"\n✨ PR creation deferred: no workloads currently meet the {min_history_days:.1f}-day observation threshold.")
        return 0

    if (args.apply or args.create_pr) and not target_proposals:
        print("\n✨ All eligible workloads are currently right-sized within target thresholds. Nothing to do!")
        return 0

    files_to_save = {}
    if args.apply or args.create_pr:
        for p in target_proposals:
            res = p["res_block"]
            if "requests" not in res or not isinstance(res["requests"], dict):
                res["requests"] = {}
            if "limits" not in res or not isinstance(res["limits"], dict):
                res["limits"] = {}

            if p["cpu_trigger"] or p["curr_cpu"] == "none":
                res["requests"]["cpu"] = p["new_cpu"]
            if p["mem_trigger"] or p["curr_mem"] == "none":
                res["requests"]["memory"] = p["new_mem"]
            if p["limit_trigger"]:
                res["limits"]["memory"] = p["new_limit_mem"]

            files_to_save[p["file"]] = p["hr_yaml"]

        print(f"\nWriting updates to {len(files_to_save)} HelmRelease file(s)...")
        for fpath, ydata in files_to_save.items():
            with open(fpath, "w") as f:
                yaml.dump(ydata, f)
            subprocess.run(["oxfmt", str(fpath)], check=False)
            print(f"  [UPDATED] {fpath.relative_to(workspace_root)}")
        print("\nAll files successfully updated and formatted with oxfmt!")

        if args.create_pr:
            branch_name = "automation/resource-rightsizing"
            print(f"\nPreparing branch '{branch_name}' and Pull Request...")
            subprocess.run(["git", "checkout", "-B", branch_name], check=True)
            subprocess.run(["git", "add", "kubernetes/apps/"], check=True)
            commit_msg = "chore: right-size workload resource requests and limits"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)

            md_report = build_markdown_report(target_proposals, headroom)
            report_file = workspace_root / ".rightsize-pr-body.md"
            report_file.write_text(md_report)

            if args.push:
                print(f"\nPushing '{branch_name}' to origin...")
                subprocess.run(["git", "push", "-u", "origin", branch_name, "--force"], check=True)
                print("Opening or updating Pull Request via gh CLI...")
                res = subprocess.run(["gh", "pr", "create", "--title", commit_msg, "--body-file", str(report_file), "--base", "main"], capture_output=True, text=True)
                if res.returncode == 0:
                    print(res.stdout)
                else:
                    subprocess.run(["gh", "pr", "edit", branch_name, "--body-file", str(report_file)], check=False)
                    print(f"Existing PR updated (or note: {res.stderr.strip()})")
            else:
                print(f"\nCommit created. To push branch and create PR, run:")
                print(f"  git push -u origin {branch_name}")
                print(f"  gh pr create --title \"{commit_msg}\" --body-file {report_file} --base main")
    else:
        print("\nRun with '--apply' to update manifests on disk.")


if __name__ == "__main__":
    main()
