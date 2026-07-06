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


# --- Continental "DaimlerStandardSecurity" seed->key (ported from jglim/UnlockECU) ---
# 8-byte seed -> 4-byte key. key = (i1 ^ i2 ^ cryptoKey) & 0xFFFFFFFF.
# EARS167 (Continental radar) = RefG with cryptoKey B3687D8B; whole B3687D8X family are radars.
M32 = 0xFFFFFFFF
# ranked candidate cryptoKeys (Continental radar family first)
DEFAULT_KEYS = ["B3687D8B", "B3687D83", "B3687D87", "B3687D89", "B3687D8D", "B3687D8F",
                "DEDE947A", "FFFFFFFF", "CEF734C3", "8B014327", "2B972AC5", "EA85C49B"]


def daimler_key(seed, cryptokey, variant="refG"):
    a = int.from_bytes(seed[0:4], "big")
    b = int.from_bytes(seed[4:8], "big")
    K = cryptokey & M32
    if variant == "refG":
        i1 = (3040238857 * a + 2094854071) & M32
        i2 = (4126034881 * b + 3555108353) & M32
    else:  # base: glibc LCG constants
        i1 = (1103515245 * a + 12345) & M32
        i2 = (1103515245 * b + 12345) & M32
    return ((i1 ^ i2 ^ K) & M32).to_bytes(4, "big")


def do_unlock(uds, session, keys, variant, level=0x11):
    """Enter session, try candidate cryptoKeys. On success LEAVE session unlocked
    (no session switch) and return the winning key; else None. Stops on lockout."""
    print(f"\n[UNLOCK] session 0x{session:02x} level 0x{level:02x} variant={variant} "
          f"({len(keys)} candidate keys)")
    try:
        uds.diagnostic_session_control(session)
    except NegativeResponseError as e:
        print(f"  cannot enter session 0x{session:02x}: {nrc(e)}")
        return None
    def lockout(remaining):
        print(f"  >>> LOCKOUT (too many attempts). Power-cycle car, rerun with:")
        print(f"      --keys {','.join(remaining)}")

    for i, ck_hex in enumerate(keys):
        ck = int(ck_hex, 16)
        try:
            seed = uds.security_access(level)
        except NegativeResponseError as e:
            code = getattr(e, "error_code", None)
            print(f"  requestSeed: {nrc(e)}")
            if code in (0x36, 0x37):
                lockout(keys[i:])
                return None
            try:  # recover sequence by re-entering session
                uds.diagnostic_session_control(session)
                seed = uds.security_access(level)
            except NegativeResponseError:
                return None
        if not any(seed):
            print("  seed is all-zero -> already unlocked")
            return "already"
        key = daimler_key(seed, ck, variant)
        try:
            uds.security_access(level + 1, key)
            print(f"  key {ck_hex}: seed={seed.hex()} -> key={key.hex()} -> *** UNLOCKED! ***")
            return ck_hex
        except NegativeResponseError as e:
            code = getattr(e, "error_code", None)
            print(f"  key {ck_hex}: seed={seed.hex()} key={key.hex()} -> {nrc(e)}")
            if code in (0x36, 0x37):
                lockout(keys[i + 1:])
                return None
    print("  no candidate cryptoKey unlocked.")
    return None


def do_enable(uds, keys, variant, did, value, session=0x05, level=0x11):
    win = do_unlock(uds, session, keys, variant, level)  # leaves 0x05 unlocked on success
    if not win:
        print("  not unlocked -> aborting write.")
        return
    print(f"  (unlocked with {win}) writing DID 0x{did:04x} = 0x{value.hex()} in session 0x{session:02x}...")
    try:
        uds.write_data_by_identifier(did, value)  # same unlocked session, no switch!
        print(f"  *** WRITE 0x{did:04x}=0x{value.hex()} POSITIVE RESPONSE! ***")
        print("  NEXT: restart car (READY), boot openpilot (RadarTracks=3), record log, scan diff.")
        print(f"  REVERT: --enable 0x{did:04x} 00 --bus <bus>  (writes back 00)")
    except NegativeResponseError as e:
        print(f"  write REJECTED: {nrc(e)}")


def probe_keylen(uds, session, level=0x11):
    # Our 4-byte key gave 0x13 (badLength). Find the expected key length: send a dummy
    # zero-key of each length; wrong length -> 0x13, CORRECT length -> 0x35 (invalidKey).
    # Only the correct length increments the failed-attempt counter, so this is safe.
    print(f"\n[KEY-LENGTH PROBE] session 0x{session:02x} level 0x{level:02x}")
    print("  0x13=wrong length | 0x35=CORRECT length(wrong value) | positive=zero-key unlocked")
    try:
        uds.diagnostic_session_control(session)
    except NegativeResponseError as e:
        print(f"  cannot enter session 0x{session:02x}: {nrc(e)}")
        return
    for L in range(1, 17):
        try:
            uds.security_access(level)  # request+discard seed
        except NegativeResponseError as e:
            if getattr(e, "error_code", None) == 0x24:  # sequence -> re-enter session
                try:
                    uds.diagnostic_session_control(session)
                    uds.security_access(level)
                except NegativeResponseError:
                    print(f"  len {L}: requestSeed re-enter failed")
                    return
            else:
                print(f"  len {L}: requestSeed {nrc(e)}")
                if getattr(e, "error_code", None) in (0x36, 0x37):
                    return
                continue
        try:
            uds.security_access(level + 1, bytes(L))
            print(f"  len {L}: *** POSITIVE (zero key accepted!) ***")
            return
        except NegativeResponseError as e:
            code = getattr(e, "error_code", None)
            tag = "  <== CORRECT KEY LENGTH" if code == 0x35 else ""
            print(f"  len {L}: {nrc(e)}{tag}")
            if code in (0x36, 0x37):
                print("  lockout - power cycle & rerun")
                return


def scan_security(uds, session):
    # write in 0x05 needs SecurityAccess but level 1 (0x27 01) is subFunctionNotSupported.
    # Scan all requestSeed sub-functions (odd) to find which level the radar uses.
    # requestSeed is read-only (no key sent) -> does not trip the failed-attempt counter.
    print(f"\n[SECURITY-LEVEL SCAN] session 0x{session:02x} (requestSeed 0x27 01..7F odd)")
    try:
        uds.diagnostic_session_control(session)
        print(f"  entered session 0x{session:02x}")
    except NegativeResponseError as e:
        print(f"  cannot enter session 0x{session:02x}: {nrc(e)} (abort)")
        return
    found = False
    for lvl in range(0x01, 0x80, 2):
        try:
            seed = uds.security_access(lvl)
            found = True
            allzero = not any(seed)
            print(f"  level 0x{lvl:02x}: SEED = 0x{seed.hex()} (len {len(seed)})"
                  f"{'  <-- ALL ZERO = already unlocked' if allzero else '  <-- USE THIS LEVEL'}")
        except NegativeResponseError as e:
            code = getattr(e, "error_code", None)
            if code not in (0x11, 0x12):
                print(f"  level 0x{lvl:02x}: {nrc(e)}")
        except Exception as ex:
            print(f"  level 0x{lvl:02x}: no response ({type(ex).__name__})")
    if not found:
        print("  no requestSeed level returned a seed here.")


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
                   help="probe SecurityAccess seed (level 1) in given session, e.g. --request-seed 0x03")
    p.add_argument("--scan-security", metavar="HH", dest="scan_security",
                   help="scan all requestSeed levels in a session, e.g. --scan-security 0x05")
    p.add_argument("--probe-keylen", action="store_true", dest="probe_keylen",
                   help="find expected sendKey length in session 0x05 level 0x11")
    p.add_argument("--unlock", action="store_true",
                   help="try candidate cryptoKeys to unlock SecurityAccess (session 0x05 lvl 0x11)")
    p.add_argument("--enable", nargs=2, metavar=("DID", "HEX"),
                   help="unlock then write DID in the SAME session, e.g. --enable 0x0126 01")
    p.add_argument("--keys", help="comma-separated candidate cryptoKeys (hex); default=radar family")
    p.add_argument("--variant", default="refG", help="key algorithm variant: refG (default) or base")
    p.add_argument("--write", nargs=2, metavar=("DID", "HEX"))
    p.add_argument("--session", default="0x03", help="session for --write (default 0x03)")
    p.add_argument("--bus", type=int, default=2)
    a = p.parse_args()
    if not any([a.read, a.write, a.scan_sessions, a.try_session, a.request_seed, a.scan_security,
                a.probe_keylen, a.unlock, a.enable]):
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
    if a.scan_security:
        scan_security(uds, int(a.scan_security, 16))
    if a.probe_keylen:
        probe_keylen(uds, 0x05)
    keys = [k.strip() for k in a.keys.split(",")] if a.keys else DEFAULT_KEYS
    unlock_session = 0x05  # SecurityAccess-gated write session on MRR-35
    if a.unlock:
        do_unlock(uds, unlock_session, keys, a.variant)
    if a.enable:
        do_enable(uds, keys, a.variant, int(a.enable[0], 16), bytes.fromhex(a.enable[1]),
                  session=unlock_session)
    if a.write:
        do_write(uds, int(a.write[0], 16), bytes.fromhex(a.write[1]), a.bus, int(a.session, 16))
