#!/usr/bin/env python3
"""Casper EV (Continental MRR-35, FW 99110-GX000) UDS probe / config write helper.
Run ON THE COMMA DEVICE from /data/openpilot, openpilot STOPPED, car powered ON.
  --read            dump identification + config DIDs (safe)
  --write DID HEX   write one config DID (RISKY; prints revert cmd, asks WRITE)
MRR-35 lacks Mando's DID 0x0142; this probes/experiments to enable radar points.
USE AT YOUR OWN RISK: touches a safety-critical radar (AEB/FCW)."""
import sys
import argparse
from subprocess import check_output, CalledProcessError
from opendbc.car.uds import UdsClient, SESSION_TYPE, NegativeResponseError
from opendbc.car.structs import CarParams
from panda.python import Panda

RADAR_TX = 0x7D0
NRC = {0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
       0x13: "badLength/format", 0x22: "conditionsNotCorrect", 0x24: "requestSequenceError",
       0x31: "requestOutOfRange (DID absent)", 0x33: "securityAccessDenied (needs key)",
       0x35: "invalidKey", 0x72: "generalProgrammingFailure",
       0x7E: "subFuncNotSupportedInSession", 0x7F: "svcNotSupportedInSession"}


def nrc(e):
    c = getattr(e, "error_code", None)
    return f"NACK 0x{c:02x} = {NRC.get(c, '?')}" if c is not None else str(e)


def rd(uds, did):
    try:
        return uds.read_data_by_identifier(did), None
    except NegativeResponseError as e:
        return None, nrc(e)


def asc(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def do_read(uds):
    print("\n[ID DIDs 0xF180-0xF19F]")
    for did in range(0xF180, 0xF1A0):
        v, e = rd(uds, did)
        if v:
            print(f"  0x{did:04x}: {v.hex():<40} |{asc(v)}|")
    print("\n[EXTENDED SESSION 0x03]")
    try:
        uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        print("  ok")
    except NegativeResponseError as e:
        print(f"  fail: {nrc(e)}")
    print("\n[CONFIG DIDs 0x0100-0x01FF]")
    n = 0
    for did in range(0x100, 0x200):
        v, e = rd(uds, did)
        if v is not None:
            n += 1
            print(f"  0x{did:04x}: {v.hex():<24} |{asc(v)}|")
    print(f"  -> {n} readable config DIDs")


def scan_sessions(uds):
    # Which diagnostic sessions does the radar accept? Config WRITE (0x2E) needs the
    # right one (Mando used 0x07; MRR-35 rejected 0x07 and 0x03). Scan the reserved
    # low range + manufacturer/supplier ranges. Returns to DEFAULT after each hit.
    print("\n[SESSION SCAN] (looking for a session that might allow WriteDataByIdentifier)")
    accepted = []
    for st in list(range(0x01, 0x10)) + list(range(0x40, 0x50)) + list(range(0x60, 0x70)):
        try:
            uds.diagnostic_session_control(st)
            accepted.append(st)
            print(f"  session 0x{st:02x}: ACCEPTED")
            try:
                uds.diagnostic_session_control(SESSION_TYPE.DEFAULT)
            except NegativeResponseError:
                pass
        except NegativeResponseError as e:
            code = getattr(e, "error_code", None)
            if code not in (0x11, 0x12):  # hide "not supported" noise
                print(f"  session 0x{st:02x}: {nrc(e)}")
        except Exception as ex:
            print(f"  session 0x{st:02x}: no response ({type(ex).__name__}) -- radar may have dropped off bus")
    print(f"  -> accepted sessions: {[hex(s) for s in accepted]}")
    print("  next: retry --write inside an accepted non-default session (edit do_write), "
          "or if only 0x01/0x03 -> config write likely locked behind security/unknown session.")


def do_write(uds, did, nb, bus):
    print(f"\n[WRITE] DID 0x{did:04x} <- 0x{nb.hex()}")
    uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
    cur, e = rd(uds, did)
    if cur is None:
        print(f"  cannot read current value: {e} (aborting, nothing written)")
        return
    print(f"  current value: 0x{cur.hex()}")
    print(f"  >>> REVERT (save this!): python {sys.argv[0]} --write 0x{did:04x} {cur.hex()} --bus {bus}")
    if input("  type WRITE to proceed: ").strip() != "WRITE":
        print("  not confirmed, nothing written.")
        return
    try:
        uds.write_data_by_identifier(did, nb)
        print("  write request sent (positive response).")
    except NegativeResponseError as e:
        print(f"  write REJECTED: {nrc(e)}")
        return
    b, e = rd(uds, did)
    print(f"  read-back: {('0x' + b.hex()) if b is not None else e}")
    print("  NEXT: restart car, boot (RadarTracks=3), record log, then casper_radar_scan.py diff.")
    print("  If no new track block -> REVERT with the command above.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--read", action="store_true")
    p.add_argument("--scan-sessions", action="store_true", dest="scan_sessions")
    p.add_argument("--write", nargs=2, metavar=("DID", "HEX"))
    p.add_argument("--bus", type=int, default=2)
    a = p.parse_args()
    if not a.read and not a.write and not a.scan_sessions:
        p.print_help()
        sys.exit(1)
    try:
        check_output(["pidof", "pandad"])
        print("pandad running - stop openpilot first (tmux kill-session -t comma). aborted.")
        sys.exit(1)
    except CalledProcessError as ex:
        if ex.returncode != 1:
            raise
    if input("power on vehicle (READY) then type OK: ").upper().strip() != "OK":
        print("aborted.")
        sys.exit(0)
    panda = Panda()
    panda.set_safety_mode(CarParams.SafetyModel.elm327)
    uds = UdsClient(panda, RADAR_TX, bus=a.bus)
    print("\n[FW 0xF100]")
    v, e = rd(uds, 0xF100)
    print(f"  {v!r}" if v is not None else f"  {e}")
    if a.read:
        do_read(uds)
    if a.scan_sessions:
        scan_sessions(uds)
    if a.write:
        do_write(uds, int(a.write[0], 16), bytes.fromhex(a.write[1]), a.bus)
