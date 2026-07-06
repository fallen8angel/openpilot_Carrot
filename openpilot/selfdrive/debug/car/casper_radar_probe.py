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
from opendbc.car.uds import UdsClient, SESSION_TYPE, ACCESS_TYPE, NegativeResponseError
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


def request_seed(uds, session):
    # Is config-write locked behind SecurityAccess? Enter a session, ask for a seed.
    #  seed returned      -> radar IS security-gated (need key algorithm = firmware RE)
    #  0x7F/0x31/0x12     -> no security in this session (try another) / not supported
    #  0x22               -> preconditions unmet (maybe just ignition state)
    print(f"\n[SECURITY-ACCESS PROBE] session 0x{session:02x}, requestSeed level 1 (0x27 01)")
    try:
        uds.diagnostic_session_control(session)
        print(f"  entered session 0x{session:02x}")
    except NegativeResponseError as e:
        print(f"  cannot enter session 0x{session:02x}: {nrc(e)} (so cannot probe seed here)")
        return
    try:
        seed = uds.security_access(ACCESS_TYPE.REQUEST_SEED)
        nz = any(seed)
        print(f"  SEED = 0x{seed.hex()} (len {len(seed)}) -> radar IS security-gated"
              f"{' (all-zero = already unlocked!)' if not nz else '; need seed->key algorithm'}")
    except NegativeResponseError as e:
        print(f"  requestSeed rejected: {nrc(e)}")
        print("  0x7F=security in another session | 0x31/0x12=no security here | 0x22=preconditions")


def _to_default(uds):
    try:
        uds.diagnostic_session_control(SESSION_TYPE.DEFAULT)
    except NegativeResponseError:
        pass


def try_session(uds, st):
    print(f"\n[TRY SESSION 0x{st:02x}]")
    # (1) direct from default session
    try:
        uds.diagnostic_session_control(st)
        print(f"  direct: ACCEPTED  <-- write may be possible here")
        _to_default(uds)
        return
    except NegativeResponseError as e:
        print(f"  direct: {nrc(e)}")
    # (2) chained: default -> extended(0x03) -> target (many ECUs require extended first)
    _to_default(uds)
    try:
        uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        uds.diagnostic_session_control(st)
        print(f"  via 0x03: ACCEPTED  <-- needed extended-first! write may work here")
        _to_default(uds)
        return
    except NegativeResponseError as e:
        print(f"  via 0x03: {nrc(e)}")
    _to_default(uds)
    print("  -> still blocked. If 0x22: try car in ON-not-READY (start x2, no brake). "
          "If 0x33: security-gated.")


def do_write(uds, did, nb, bus, session=0x02):
    # NOTE: on MRR-35, ReadDataByIdentifier works in EXTENDED (0x03) but not in
    # PROGRAMMING (0x02); WriteDataByIdentifier needs 0x02. So: read via 0x03,
    # write via `session`, read-back via 0x03.
    print(f"\n[WRITE] DID 0x{did:04x} <- 0x{nb.hex()}  (write-session 0x{session:02x})")
    cur = None
    try:
        uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        cur, _ = rd(uds, did)
    except NegativeResponseError:
        pass
    if cur is not None:
        print(f"  current value (read via 0x03): 0x{cur.hex()}")
        print(f"  >>> REVERT (save!): python {sys.argv[0]} --write 0x{did:04x} {cur.hex()} "
              f"--session 0x{session:02x} --bus {bus}")
    else:
        print("  (could not pre-read; be sure you know the revert value!)")
    try:
        uds.diagnostic_session_control(session)
        print(f"  entered write-session 0x{session:02x}")
    except NegativeResponseError as e:
        print(f"  cannot enter session 0x{session:02x}: {nrc(e)} (aborting)")
        return
    if input("  type WRITE to proceed: ").strip() != "WRITE":
        print("  not confirmed, nothing written.")
        return
    try:
        uds.write_data_by_identifier(did, nb)
        print("  *** write POSITIVE response! ***")
    except NegativeResponseError as e:
        print(f"  write REJECTED: {nrc(e)}")
        return
    try:
        uds.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        b, _ = rd(uds, did)
        print(f"  read-back (via 0x03): {('0x' + b.hex()) if b is not None else 'unreadable'}")
    except NegativeResponseError:
        pass
    print("  NEXT: restart car (READY), boot (RadarTracks=3), record log, casper_radar_scan.py diff.")
    print("  If no new track block -> REVERT with the command above.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--read", action="store_true")
    p.add_argument("--scan-sessions", action="store_true", dest="scan_sessions")
    p.add_argument("--try-session", metavar="HH", dest="try_session",
                   help="attempt to enter one session, e.g. --try-session 0x02")
    p.add_argument("--request-seed", metavar="HH", dest="request_seed",
                   help="probe SecurityAccess seed in given session, e.g. --request-seed 0x03")
    p.add_argument("--write", nargs=2, metavar=("DID", "HEX"))
    p.add_argument("--session", default="0x03", help="session for --write (default 0x03)")
    p.add_argument("--bus", type=int, default=2)
    a = p.parse_args()
    if not any([a.read, a.write, a.scan_sessions, a.try_session, a.request_seed]):
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
    if a.try_session:
        try_session(uds, int(a.try_session, 16))
    if a.request_seed:
        request_seed(uds, int(a.request_seed, 16))
    if a.write:
        do_write(uds, int(a.write[0], 16), bytes.fromhex(a.write[1]), a.bus, int(a.session, 16))
