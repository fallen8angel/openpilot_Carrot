#!/usr/bin/env python3
"""Casper EV (Continental MRR-35) radar-track discovery helper.

Parses an openpilot rlog(.zst) WITHOUT the full openpilot runtime (works on
Windows where tools/lib/logreader.py fails on `import fcntl`). Loads the cereal
schema directly with pycapnp.

What it does:
  * inventory every CAN address per bus
  * flag "radar-track signatures" = runs of >=RUN consecutive addresses
    (Mando tracks = 32 consecutive 0x500-0x51F; group3 = 30 consecutive
     0x400-0x41D). A real radar object array shows up as such a block.
  * optionally DIFF two logs (normal vs radar-tracks-enabled) and print the
    addresses that appear ONLY in the second log -> prime radar-track candidates.

Usage:
  python casper_radar_scan.py <rlog.zst>
  python casper_radar_scan.py <normal_rlog.zst> <radar_enabled_rlog.zst>

Deps: pip install zstandard pycapnp numpy   (numpy optional)
"""
import sys
import collections

import zstandard
import capnp

# --- locate cereal schema (edit if your checkout differs) -------------------
CEREAL = r"d:/Carrot/openpilot/openpilot/cereal"
CARDIR = r"d:/Carrot/openpilot/opendbc_repo/opendbc/car"
RUN = 8  # minimum consecutive-address run to call a radar-track signature

capnp.remove_import_hook()
log_capnp = capnp.load(CEREAL + "/log.capnp", imports=[CEREAL, CARDIR])


def load_addr_by_bus(path):
    """Return {bus: Counter{address: count}} and metadata for one rlog."""
    with open(path, "rb") as f:
        dat = zstandard.ZstdDecompressor().stream_reader(f).read()
    addr_by_bus = collections.defaultdict(collections.Counter)
    car = None
    n = t0 = t1 = None
    for e in log_capnp.Event.read_multiple_bytes(dat):
        if t0 is None:
            t0 = e.logMonoTime
        t1 = e.logMonoTime
        w = e.which()
        if w == "can":
            for c in e.can:
                addr_by_bus[c.src][c.address] += 1
        elif w == "carParams" and car is None:
            car = e.carParams.carFingerprint
    dur = (t1 - t0) / 1e9 if t0 else 0.0
    return addr_by_bus, car, dur


def consecutive_runs(addrs, run=RUN):
    """Yield (lo, hi, length) for runs of >=run consecutive addresses."""
    addrs = sorted(addrs)
    if not addrs:
        return
    cur = [addrs[0]]
    for a in addrs[1:]:
        if a == cur[-1] + 1:
            cur.append(a)
        else:
            if len(cur) >= run:
                yield cur[0], cur[-1], len(cur)
            cur = [a]
    if len(cur) >= run:
        yield cur[0], cur[-1], len(cur)


def report(path):
    addr_by_bus, car, dur = load_addr_by_bus(path)
    print(f"\n### {path}")
    print(f"car={car}  duration={dur:.1f}s  buses={sorted(addr_by_bus)}")
    hit = False
    for bus in sorted(addr_by_bus):
        for lo, hi, ln in consecutive_runs(addr_by_bus[bus]):
            hit = True
            print(f"  [TRACK-SIGNATURE] bus {bus}: 0x{lo:x}-0x{hi:x} ({ln} consecutive)")
    if not hit:
        print("  no radar-track signature (no consecutive-address block >= "
              f"{RUN}) -> radar not broadcasting object array")
    return addr_by_bus


def diff(path_normal, path_enabled):
    a_norm = report(path_normal)
    a_en = report(path_enabled)
    print("\n### DIFF  (addresses present in ENABLED log but NOT in normal log)")
    all_buses = set(a_norm) | set(a_en)
    found = False
    for bus in sorted(all_buses):
        new = set(a_en.get(bus, {})) - set(a_norm.get(bus, {}))
        if new:
            found = True
            addrs = sorted(new)
            print(f"  bus {bus}: {len(addrs)} new -> " + " ".join(f"{a:x}" for a in addrs))
            for lo, hi, ln in consecutive_runs(new):
                print(f"     -> CANDIDATE radar-track block 0x{lo:x}-0x{hi:x} ({ln} consecutive)")
    if not found:
        print("  no new addresses -> enable command did NOT make the radar emit "
              "anything new. Try hyundai_enable_radar_points.py --read-only "
              "--scan-config-dids to probe the MRR-35 config DID instead.")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        report(sys.argv[1])
    elif len(sys.argv) == 3:
        diff(sys.argv[1], sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)
