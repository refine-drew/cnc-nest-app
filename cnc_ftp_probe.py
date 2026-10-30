#!/usr/bin/env python3
"""
Syntec FTP probe — standalone CLI, standard library only.

Answers the device questions the FTP upload feature is gated on
(`SS2_Layout_Tool_Syntec_FTP_Integration_Spec.md`, open items 1–3): where the
program root is, what the server does when a name already exists, and what a
broken upload leaves behind. Run it on CNC1_Desktop and paste the report back.

Usage:
    .venv\\Scripts\\python cnc_ftp_probe.py discover
    .venv\\Scripts\\python cnc_ftp_probe.py behave --parent /SOME/FOLDER
    .venv\\Scripts\\python cnc_ftp_probe.py poll --minutes 15

`discover` is read-only: it logs in and lists the starting folder and the
folders one level below it. Use it to find the folder the control's program
manager shows.

`behave` writes, but only inside a new folder it creates under `--parent`,
named `CNCNEST_PROBE_<MMDD-HHMM>`, and only dummy programs that contain
nothing but comments and `M30`. It never deletes or renames anything outside
that folder, and it deletes nothing at all — remove the folder at the control
when the report is in. Every test the upload design depends on runs against
files this script uploaded a moment earlier, never against a real program.

`poll` repeats the status check the app would run (log in, open the folder,
log out) every `--interval` seconds. Run it during an air cut and watch the
machine: the question is whether a login every 15 s disturbs a running job.

Each run prints to the screen and writes the same text to
`cnc_ftp_probe_<mode>_<MMDD-HHMM>.txt` in the current folder.
"""

import argparse
import ftplib
import io
import socket
import sys
import time
from datetime import datetime

DEFAULT_HOST = "192.168.50.91"
DEFAULT_PORT = 21
DEFAULT_TIMEOUT = 10.0

# Caps on `discover`, so a folder holding thousands of programs cannot flood the report.
MAX_ENTRIES = 40
MAX_SUBDIRS = 40


class Report:
    def __init__(self, mode):
        self.path = f"cnc_ftp_probe_{mode}_{datetime.now().strftime('%m%d-%H%M')}.txt"
        self._f = open(self.path, "w", encoding="utf-8")
        self.verdicts = []

    def line(self, text=""):
        print(text)
        self._f.write(text + "\n")
        self._f.flush()

    def head(self, text):
        self.line()
        self.line(f"== {text}")

    def verdict(self, key, value):
        self.verdicts.append((key, value))
        self.line(f"   -> {key}: {value}")

    def close(self):
        if self.verdicts:
            self.head("SUMMARY")
            width = max(len(k) for k, _ in self.verdicts)
            for k, v in self.verdicts:
                self.line(f"{k.ljust(width)}  {v}")
        self.line()
        self.line(f"Report written to {self.path}")
        self._f.close()


class RecordingFTP(ftplib.FTP):
    """Keeps every server reply, including the 1xx a transfer opens with.

    `storbinary` returns only the final reply, and a server that supports `STOU`
    usually names the file it chose in the 1xx — the one reply ftplib discards.
    """

    def __init__(self, *a, **kw):
        self.replies = []
        super().__init__(*a, **kw)

    def getmultiline(self):
        line = super().getmultiline()
        self.replies.append(line)
        return line


def connect(args):
    ftp = RecordingFTP(timeout=args.timeout)
    ftp.connect(args.host, args.port)
    ftp.login(args.user, args.password)
    return ftp


def binary(ftp):
    """Switch the session to binary before a raw upload or `SIZE`.

    ftplib's `nlst`/`retrlines` leave the session in ASCII, and the raw
    `transfercmd` uploads here do not set the type themselves. In ASCII a server
    may rewrite CRLF on the way in, and `SIZE` is commonly refused.
    """
    ftp.voidcmd("TYPE I")


def size(ftp, name):
    binary(ftp)
    return ftp.size(name)


def attempt(fn, *a, **kw):
    """`(True, result)` or `(False, "ExceptionType: message")`."""
    try:
        return True, fn(*a, **kw)
    except (ftplib.all_errors + (UnicodeError,)) as e:
        return False, f"{type(e).__name__}: {e}"


def dummy_program(tag, pad=0):
    """A program that does nothing if loaded and run: comments, then M30.

    CRLF, because that is what the app writes on Windows and what the USB
    workflow already runs, so a byte-for-byte round trip of it is the
    line-ending test as well.
    """
    lines = ["%", f"(CNC NEST FTP PROBE {tag})", "(DUMMY FILE - DO NOT RUN)"]
    lines += [f"(PAD {i:06d})" for i in range(pad)]
    lines += ["M30", "%", ""]
    return "\r\n".join(lines).encode("ascii")


def put(ftp, name, data, cmd="STOR"):
    return attempt(ftp.storbinary, f"{cmd} {name}".rstrip(), io.BytesIO(data))


def get(ftp, name):
    buf = io.BytesIO()
    ok, res = attempt(ftp.retrbinary, f"RETR {name}", buf.write)
    return (True, buf.getvalue()) if ok else (False, res)


def names(ftp):
    ok, res = attempt(ftp.nlst)
    return res if ok else []


def describe(content, known):
    """Which of the known payloads `content` is, by its tag."""
    for label, data in known.items():
        if content == data:
            return label
    return f"unrecognised content ({len(content)} bytes)"


# ── discover ─────────────────────────────────────────────────────────────


def list_folder(rep, ftp, path):
    ok, res = attempt(ftp.cwd, path)
    if not ok:
        rep.line(f"   CWD {path}: {res}")
        return []
    rep.line(f"   PWD: {ftp.pwd()}")
    lines = []
    ok, res = attempt(ftp.retrlines, "LIST", lines.append)
    rep.line(f"   LIST: {'ok' if ok else res}")
    for ln in lines[:MAX_ENTRIES]:
        rep.line(f"     {ln}")
    if len(lines) > MAX_ENTRIES:
        rep.line(f"     ... {len(lines) - MAX_ENTRIES} more")
    entries = names(ftp)
    return [e for e in entries if e not in (".", "..")]


def run_discover(rep, args):
    rep.head(f"Connect {args.host}:{args.port} as {args.user!r}")
    ftp = connect(args)
    rep.line(f"   Welcome: {ftp.welcome}")
    for cmd in ("SYST", "FEAT", "HELP"):
        ok, res = attempt(ftp.sendcmd, cmd)
        rep.line(f"   {cmd}: {res}")
    start = args.path or ftp.pwd()
    rep.line(f"   Starting folder: {start}")

    rep.head(f"Listing formats in {start}")
    ftp.cwd(start)
    ok, res = attempt(ftp.nlst)
    rep.line(f"   NLST: {'ok, ' + str(len(res)) + ' names' if ok else res}")
    ok, res = attempt(lambda: list(ftp.mlsd()))
    rep.line(f"   MLSD: {'ok, ' + str(len(res)) + ' entries' if ok else res}")
    if ok:
        for name, facts in res[:5]:
            rep.line(f"     {name} {facts}")

    rep.head(f"Folder {start}")
    entries = list_folder(rep, ftp, start)

    # A name is a folder if CWD into it works. Read-only, and it does not depend
    # on a LIST format nobody has seen yet.
    subdirs = []
    for e in entries:
        ok, _ = attempt(ftp.cwd, _join(start, e))
        if ok:
            subdirs.append(e)
        ftp.cwd(start)
    for d in subdirs[:MAX_SUBDIRS]:
        rep.head(f"Folder {_join(start, d)}")
        list_folder(rep, ftp, _join(start, d))
    if len(subdirs) > MAX_SUBDIRS:
        rep.line(f"   ... {len(subdirs) - MAX_SUBDIRS} more folders not listed")
    attempt(ftp.quit)


def _join(parent, name):
    return parent.rstrip("/") + "/" + name if parent not in ("", "/") else "/" + name


# ── behave ───────────────────────────────────────────────────────────────


def run_behave(rep, args):
    ftp = connect(args)
    rep.head(f"Scratch folder under {args.parent}")
    ftp.cwd(args.parent)
    scratch = f"CNCNEST_PROBE_{datetime.now().strftime('%m%d-%H%M')}"
    ok, res = attempt(ftp.mkd, scratch)
    rep.line(f"   MKD {scratch}: {res}")
    if not ok:
        rep.line("   Could not create the scratch folder. Stopping; nothing was written.")
        return
    scratch_path = _join(args.parent, scratch)

    ok, res = attempt(ftp.mkd, scratch)
    rep.line(f"   MKD {scratch} again: {res}")
    rep.verdict("MKD on an existing folder", "refused" if not ok else "ACCEPTED (no error)")
    ok, res = attempt(ftp.mkd, scratch.lower())
    rep.line(f"   MKD {scratch.lower()}: {res}")
    rep.verdict("Folder names", "case-insensitive" if not ok else "case-sensitive")
    ftp.cwd(scratch_path)

    A, B, C = dummy_program("A"), dummy_program("B", 3), dummy_program("C", 6)
    known = {"A": A, "B": B, "C": C}

    rep.head("T1 upload a new file, read it back")
    ok, res = put(ftp, "PROBE1.nc", A)
    rep.line(f"   STOR PROBE1.nc: {res}")
    ok, back = get(ftp, "PROBE1.nc")
    rep.verdict("Binary round trip (CRLF)", "byte-identical" if ok and back == A
                else f"DIFFERS: {back if not ok else describe(back, known)}")
    ok, res = attempt(size, ftp, "PROBE1.nc")
    rep.verdict("SIZE", f"supported ({res} bytes, sent {len(A)})" if ok else f"not supported: {res}")
    ok, res = attempt(ftp.sendcmd, "MDTM PROBE1.nc")
    rep.verdict("MDTM", f"supported ({res})" if ok else f"not supported: {res}")
    lines = []
    attempt(ftp.retrlines, "LIST", lines.append)
    rep.line("   LIST of scratch folder:")
    for ln in lines:
        rep.line(f"     {ln}")
    ok, res = attempt(lambda: list(ftp.mlsd()))
    rep.verdict("MLSD", "supported" if ok else f"not supported: {res}")
    if ok:
        for name, facts in res:
            rep.line(f"     {name} {facts}")

    rep.head("T2 STOR onto a name that exists")
    ok, res = put(ftp, "PROBE1.nc", B)
    rep.line(f"   STOR PROBE1.nc (content B): {res}")
    ok2, back = get(ftp, "PROBE1.nc")
    after = describe(back, known) if ok2 else back
    rep.line(f"   PROBE1.nc now holds: {after}")
    rep.verdict("STOR onto existing name", {"A": "refused, original kept",
                                           "B": "OVERWRITES"}.get(after, after))

    rep.head("T3 same name, different case")
    ok, res = put(ftp, "probe1.NC", C)
    rep.line(f"   STOR probe1.NC (content C): {res}")
    ok2, back = get(ftp, "PROBE1.nc")
    after = describe(back, known) if ok2 else back
    rep.line(f"   PROBE1.nc now holds: {after}")
    rep.line(f"   Names in folder: {names(ftp)}")
    rep.verdict("File names", "case-insensitive (probe1.NC hit PROBE1.nc)" if after == "C"
                else "case-sensitive or refused; see names above")

    rep.head("T4 STOU (server picks a unique name)")
    for cmd, arg in (("STOU", ""), ("STOU", "PROBE1.nc")):
        before = set(names(ftp))
        ftp.replies.clear()
        ok, res = put(ftp, arg, dummy_program("STOU"), cmd=cmd)
        rep.line(f"   {cmd} {arg}".rstrip() + f": {res}")
        rep.line(f"     replies: {ftp.replies}")
        rep.line(f"     new names: {sorted(set(names(ftp)) - before)}")
        if arg:
            ok2, back = get(ftp, arg)
            rep.line(f"     {arg} now holds: {describe(back, known) if ok2 else back}")
        rep.verdict(f"{cmd} {arg}".rstrip(), "supported" if ok else "not supported")

    rep.head("T5 rename a temp file to a free name")
    D, E = dummy_program("D"), dummy_program("E", 4)
    known.update(D=D, E=E)
    put(ftp, "TMP1.nc", D)
    ok, res = attempt(ftp.rename, "TMP1.nc", "PROBE2.nc")
    rep.line(f"   RNFR TMP1.nc / RNTO PROBE2.nc: {res}")
    rep.verdict("RNFR/RNTO to a free name", "works" if ok else f"fails: {res}")

    rep.head("T6 rename a temp file onto a name that exists")
    put(ftp, "TMP2.nc", E)
    ok, res = attempt(ftp.rename, "TMP2.nc", "PROBE2.nc")
    rep.line(f"   RNFR TMP2.nc / RNTO PROBE2.nc: {res}")
    ok2, back = get(ftp, "PROBE2.nc")
    after = describe(back, known) if ok2 else back
    rep.line(f"   PROBE2.nc now holds: {after}")
    rep.line(f"   Names in folder: {names(ftp)}")
    rep.verdict("RNTO onto existing name", {"D": "refused, original kept",
                                           "E": "OVERWRITES"}.get(after, after))
    attempt(ftp.quit)

    rep.head("T7 upload cut off part way")
    big = dummy_program("PARTIAL", 20000)
    ftp = connect(args)
    ftp.cwd(scratch_path)
    ok, res = attempt(_cut_off_upload, ftp, "PARTIAL.nc", big, len(big) // 3)
    rep.line(f"   Sent {len(big) // 3} of {len(big)} bytes, then closed the data connection: {res}")
    attempt(ftp.close)
    time.sleep(2)
    ftp = connect(args)
    ftp.cwd(scratch_path)
    present = "PARTIAL.nc" in names(ftp)
    ok, res = attempt(size, ftp, "PARTIAL.nc")
    rep.line(f"   After reconnect: present={present}, SIZE={res}")
    rep.verdict("Interrupted upload", f"LEAVES A PARTIAL FILE ({res} of {len(big)} bytes)"
                if present else "leaves nothing")

    rep.head("T8 status check during an upload (two connections)")
    ok, res = attempt(_upload_with_second_login, args, ftp, "CONCURRENT.nc", dummy_program("CONC", 2000))
    rep.line(f"   {res}")
    ok2, back = get(ftp, "CONCURRENT.nc")
    rep.verdict("Second login during an upload",
                f"{res}; upload {'intact' if ok2 and back == dummy_program('CONC', 2000) else 'DAMAGED'}"
                if ok else res)

    rep.head("T9 file names")
    candidates = ["0820-1430.nc", "0820-1430b.nc", "T24 Test.nc", "[9] 18G Test.nc",
                  "Mixed_Case-name.nc", "two.dots.nc", "Café.nc"]
    candidates += ["L" * n + ".nc" for n in (8, 16, 28, 29, 32, 48, 64, 100, 200)]
    longest = 0
    for i, name in enumerate(candidates):
        ok, res = put(ftp, name, dummy_program(f"NAME{i}"))
        listed = name in names(ftp)
        rep.line(f"   {name!r} ({len(name)} chars): {'ok' if ok else res}; listed as sent: {listed}")
        if ok and listed and name.startswith("L"):
            longest = max(longest, len(name))
    rep.line(f"   Names in folder: {names(ftp)}")
    rep.verdict("Longest name accepted and listed", f"{longest} chars" if longest else "none of the length tests")

    rep.head("T10 folder names and depth")
    for name in ("New Folder", "Sub_Folder-1"):
        ok, res = attempt(ftp.mkd, name)
        rep.line(f"   MKD {name!r}: {res}")
    depth = 0
    for d in range(1, 11):
        ok, res = attempt(ftp.mkd, f"D{d}")
        if not ok or not attempt(ftp.cwd, f"D{d}")[0]:
            rep.line(f"   MKD D{d}: {res}")
            break
        depth = d
    rep.verdict("Nested folders created", f"{depth} deep (stopped at 10)" if depth == 10 else f"{depth} deep")
    attempt(ftp.quit)

    rep.line()
    rep.line(f"Scratch folder left in place: {scratch_path}")
    rep.line("Delete it at the control once the report is in.")


def _cut_off_upload(ftp, name, data, cut_at):
    binary(ftp)
    conn = ftp.transfercmd(f"STOR {name}")
    conn.sendall(data[:cut_at])
    conn.close()
    ok, res = attempt(ftp.voidresp)
    return f"server then replied: {res}"


def _upload_with_second_login(args, ftp, name, data):
    half = len(data) // 2
    binary(ftp)
    conn = ftp.transfercmd(f"STOR {name}")
    conn.sendall(data[:half])
    t0 = time.monotonic()
    ok, res = attempt(_status_check, args, None)
    elapsed = time.monotonic() - t0
    conn.sendall(data[half:])
    conn.close()
    final = ftp.voidresp()
    second = f"ok in {elapsed * 1000:.0f} ms" if ok else f"failed: {res}"
    return f"second login {second}; upload finished: {final}"


# ── poll ─────────────────────────────────────────────────────────────────


def _status_check(args, path):
    ftp = connect(args)
    try:
        if path:
            ftp.cwd(path)
        return ftp.pwd()
    finally:
        attempt(ftp.quit)


def run_poll(rep, args):
    rep.head(f"Status check every {args.interval:g} s for {args.minutes:g} min "
             f"(login, CWD {args.path or '(start folder)'}, logout)")
    rep.line("   Ctrl-C stops early and still writes the summary.")
    end = time.monotonic() + args.minutes * 60
    times, failures = [], 0
    try:
        while time.monotonic() < end:
            t0 = time.monotonic()
            ok, res = attempt(_status_check, args, args.path)
            ms = (time.monotonic() - t0) * 1000
            stamp = datetime.now().strftime("%H:%M:%S")
            if ok:
                times.append(ms)
                rep.line(f"   {stamp}  ok    {ms:6.0f} ms")
            else:
                failures += 1
                rep.line(f"   {stamp}  FAIL  {ms:6.0f} ms  {res}")
            time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        rep.line("   stopped")
    total = len(times) + failures
    rep.verdict("Checks", f"{total}, {failures} failed")
    if times:
        times.sort()
        rep.verdict("Check time", f"median {times[len(times) // 2]:.0f} ms, worst {times[-1]:.0f} ms")
    rep.line("   Note anything the machine did during the run (pause, stutter, alarm) beside this report.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("discover", "behave", "poll"))
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--user", default="anonymous")
    ap.add_argument("--password", default="")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--path", help="discover: folder to start from; poll: folder to open")
    ap.add_argument("--parent", help="behave: folder to create the scratch folder in")
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--minutes", type=float, default=15.0)
    args = ap.parse_args(argv)

    if args.mode == "behave" and not args.parent:
        ap.error("behave needs --parent: the folder to create the scratch folder in. "
                 "Run discover first to find it.")

    socket.setdefaulttimeout(args.timeout)
    rep = Report(args.mode)
    rep.line(f"cnc_ftp_probe {args.mode}  {datetime.now():%Y-%m-%d %H:%M:%S}  host {args.host}:{args.port}")
    try:
        {"discover": run_discover, "behave": run_behave, "poll": run_poll}[args.mode](rep, args)
    except ftplib.all_errors as e:
        rep.line()
        rep.line(f"STOPPED: {type(e).__name__}: {e}")
        return 1
    finally:
        rep.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
