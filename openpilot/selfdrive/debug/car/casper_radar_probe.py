#!/usr/bin/env python3
"""Casper EV (Continental MRR-35, radar FW 99110-GX000) UDS probe / config helper.

MRR-35 does NOT have Mando's enable DID 0x0142, so hyundai_enable_radar_points.py
cannot turn on tracks. This script (a) dumps all identification + config DIDs as
evidence, and (b) optionally writes a single config DID (guarded) to experiment
with enabling radar-point output, always printing the exact revert command first.

RUN ON THE COMMA DEVICE, openpilot STOPPED, car powered ON (engine/READY).
  cd /data/openpilot
  python openpilot/selfdrive/debug/car/casper_radar_probe.py --read --bus 2
  python openpilot/selfdrive/debug/car/casper_radar_probe.py --write 0x0126 01 --bus 2
  python openpilot/selfdrive/debug/car/casper_radar_probe.py --write 0x0126 00 --bus 2   # revert

⚠️ WRITE MODE TOUCHES A SAFETY-CRITICAL RADAR (AEB/FCW). Do it stationary in a
safe place, keep the printed revert command, and test braking carefully after.
USE AT YOUR OWN RISK.
"""
import sys
import argparse
from subprocess import check_output, CalledProcessError

from opendbc.car.uds import UdsClient, SESSION_TYPE, DATA_IDENTIFIER_TYPE, NegativeResponseError
from opendbc.car.structs import CarParams
from panda.python import Panda

RADAR_TX = 0x7D0

NRC = {
    0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength/format", 0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError", 0x31: "requestOutOfRange (DID absent/not allowed)",
    0x33: "securityAccessDenied (needs 0x27 seed/key)", 0x35: "invalidKey",
    0x72: "generalProgrammingFailure", 0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


def nrc_str(e):
    code = getattr(e, "error_code", None)
    return f"NACK 0x{code:02x} = {NRC.get(code, '?')}" if code is not None else str(e)


def try_read(uds, did):
    try:
        return uds.read_data_by_identifier(did), None
    except NegativeResponseError as e:
        return None, nrc_str(e)


def ascii_of(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def do_read(uds):
    print("\n[IDENTIFICATION DIDs 0xF180-0xF19F]")
    for did in range(0xF180, 0xF1A0):
        val, err = try_read(uds, did)
        if val is not None and len(val):
            print(f"  0x{did:04x}: {val.hex():<40} |{ascii_of(val)}|")

    print("\n[ENTER EXTENDED DIAGNOSTIC SESSION 0x03]")
    try:
        uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        print("  extended session OK")
    except NegativeResponseError as e:
        print(f"  extended session failed: {nrc_str(e)}")

    print("\n[CONFIG DIDs 0x0100-0x01FF]")
    found = 0
    for did in range(0x0100, 0x0200):
        val, err = try_read(uds, did)
        if val is not None:
            found += 1
            print(f"  0x{did:04x}: {val.hex():<24} |{ascii_of(val)}|")
    print(f"  -> {found} readable config DIDs")


def do_write(uds, did, new_bytes):
    print(f"\n[WRITE EXPERIMENT] DID 0x{did:04x} <- 0x{new_bytes.hex()}")
    print("[ENTER EXTENDED DIAGNOSTIC SESSION 0x03]")
    uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)

    cur, err = try_read(uds, did)
    if cur is None:
        print(f"  cannot read current value: {err} (aborting, nothing written)")
        return
    print(f"  current value: 0x{cur.hex()}")
    print(f"  >>> REVERT COMMAND (save this!):")
    print(f"      python {sys.argv[0]} --write 0x{did:04x} {cur.hex()} --bus {ARGS.bus}")

    ok = input(f"\n  write 0x{new_bytes.hex()} to DID 0x{did:04x}? type WRITE to proceed: ").strip()
    if ok != "WRITE":
        print("  not confirmed, nothing written.")
        return
    try:
        uds.write_data_by_identifier(did, new_bytes)
        print("  write request sent (positive response).")
    except NegativeResponseError as e:
        print(f"  write REJECTED: {nrc_str(e)}")
        return
    back, err = try_read(uds, did)
    print(f"  read-back: {('0x'+back.hex()) if back is not None else err}")
    print("\n  NEXT: restart the car, boot openpilot (RadarTracks=3), record a log, then:")
    print("    python openpilot/selfdrive/debug/car/casper_radar_scan.py <normal.zst> <new.zst>")
    print("  If no new track-block appears, REVERT with the command printed above.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--read", action="store_true", help="dump identification + config DIDs (safe)")
    p.add_argument("--write", nargs=2, metavar=("DID", "HEXVALUE"),
                   help="write one config DID, e.g. --write 0x0126 01  (RISKY)")
    p.add_argument("--bus", type=int, default=2, help="CAN bus (Casper radar answered on bus 2)")
    ARGS = p.parse_args()
    if not ARGS.read and not ARGS.write:
        p.print_help(); sys.exit(1)

    try:
        check_output(["pidof", "pandad"])
        print("pandad is running — stop openpilot first (tmux kill-session -t comma). aborted.")
        sys.exit(1)
    except CalledProcessError as e:
        if e.returncode != 1:
            raise

    confirm = input("power on the vehicle (READY, engine/motor on) then type OK: ").upper().strip()
    if confirm != "OK":
        print("aborted."); sys.exit(0)

    panda = Panda()
    panda.set_safety_mode(CarParams.SafetyModel.elm327)
    uds = UdsClient(panda, RADAR_TX, bus=ARGS.bus)

    print("\n[HW/SW VERSION 0xF100]")
    v, err = try_read(uds, 0xF100)
    print(f"  {v!r}" if v is not None else f"  {err}")

    if ARGS.read:
        do_read(uds)
    if ARGS.write:
        did = int(ARGS.write[0], 16)
        new_bytes = bytes.fromhex(ARGS.write[1])
        do_write(uds, did, new_bytes)
