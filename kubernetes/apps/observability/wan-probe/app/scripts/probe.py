#!/usr/bin/env python3
"""Exports the quality of each internet connection, measured with call-like bursts.

Each connection has anchor addresses that UniFi Traffic Routes send out that
connection only, so probing an anchor measures that provider's own path. Every
round sends a burst of ICMP echoes at the pace and size of a real-time call to
every anchor, and the loss, round-trip time and jitter of the latest burst are
served as Prometheus metrics. It only observes; it never changes the network.
"""
import json
import os
import select
import socket
import statistics
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ANCHORS = json.loads(
    os.environ.get(
        "ANCHORS",
        '{"internet-1": ["9.9.9.10", "9.9.9.11"], "internet-2": ["149.112.112.10", "149.112.112.11"]}',
    )
)
PACKETS = int(os.environ.get("PACKETS", "20"))
SIZE = int(os.environ.get("PACKET_SIZE", "1200"))
INTERVAL = float(os.environ.get("PACKET_INTERVAL", "0.05"))
ROUND_SECONDS = float(os.environ.get("ROUND_SECONDS", "30"))
PORT = int(os.environ.get("PORT", "9101"))

latest = {}
lock = threading.Lock()


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def probe(anchor):
    """Returns the round-trip times, in seconds, of the echoes that came back."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    sock.setblocking(False)
    padding = b"x" * (SIZE - 8)
    sent, rtts = {}, []

    def drain(wait):
        ready, _, _ = select.select([sock], [], [], wait)
        while ready:
            try:
                data, _ = sock.recvfrom(4096)
            except BlockingIOError:
                break
            now = time.perf_counter()
            if data and data[0] >> 4 == 4:  # some systems include the IP header
                data = data[(data[0] & 0x0F) * 4 :]
            if len(data) >= 8 and data[0] == 0:  # echo reply
                seq = struct.unpack("!H", data[6:8])[0]
                if seq in sent:
                    rtts.append(now - sent.pop(seq))
            ready, _, _ = select.select([sock], [], [], 0)

    for seq in range(PACKETS):
        packet = struct.pack("!BBHHH", 8, 0, 0, 0, seq) + padding
        packet = struct.pack("!BBHHH", 8, 0, checksum(packet), 0, seq) + padding
        sent[seq] = time.perf_counter()
        try:
            sock.sendto(packet, (anchor, 0))
        except OSError:
            pass
        drain(INTERVAL)
    drain(1.0)
    sock.close()
    return rtts


def measure():
    while True:
        started = time.time()
        for wan, anchors in ANCHORS.items():
            for anchor in anchors:
                rtts = probe(anchor)
                stats = {"loss": (PACKETS - len(rtts)) / PACKETS, "at": time.time()}
                if rtts:
                    ordered = sorted(rtts)
                    deltas = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
                    stats.update(
                        p50=statistics.median(ordered),
                        p95=ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
                        max=ordered[-1],
                        jitter=statistics.fmean(deltas) if deltas else 0.0,
                    )
                with lock:
                    latest[(wan, anchor)] = stats
        time.sleep(max(1.0, ROUND_SECONDS - (time.time() - started)))


def render():
    lines = []

    def family(name, help_text):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")

    with lock:
        snapshot = dict(latest)
    family("wan_probe_loss_ratio", "Share of echoes without a reply in the latest burst.")
    for (wan, anchor), s in snapshot.items():
        lines.append(f'wan_probe_loss_ratio{{wan="{wan}",anchor="{anchor}"}} {s["loss"]}')
    for metric, key, help_text in (
        ("wan_probe_rtt_p50_seconds", "p50", "Median round-trip time of the latest burst."),
        ("wan_probe_rtt_p95_seconds", "p95", "95th percentile round-trip time of the latest burst."),
        ("wan_probe_rtt_max_seconds", "max", "Slowest round trip of the latest burst."),
        ("wan_probe_jitter_seconds", "jitter", "Mean change between consecutive round trips in the latest burst."),
    ):
        family(metric, help_text)
        for (wan, anchor), s in snapshot.items():
            if key in s:
                lines.append(f'{metric}{{wan="{wan}",anchor="{anchor}"}} {s[key]}')
    family("wan_probe_last_run_timestamp_seconds", "When the latest burst finished.")
    for (wan, anchor), s in snapshot.items():
        lines.append(f'wan_probe_last_run_timestamp_seconds{{wan="{wan}",anchor="{anchor}"}} {s["at"]}')
    return ("\n".join(lines) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body, kind = b"ok\n", "text/plain"
        elif self.path == "/metrics":
            body, kind = render(), "text/plain; version=0.0.4"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=measure, daemon=True).start()
    HTTPServer(("", PORT), Handler).serve_forever()
