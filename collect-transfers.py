#!/usr/bin/env python3
"""
Ultra-fast, robust background transfer collector for Omarchy.
Discovers active file transfers, local syncs, downloads, and disk operations.
Reports live metrics: current file, bytes/progress, instantaneous speed, and system impact.
"""

import os
import re
import sys
import time
import json
import stat
import fcntl
import select
import signal
import hashlib
import subprocess

TRANSFER_COMMANDS = {
    "rsync", "rclone", "cp", "mv", "dd", "pv", "curl", "wget", "aria2c", "yt-dlp",
    "git", "scp", "sftp", "idevicebackup2", "restic", "borg", "btrfs", "pacman", "yay", "paru", "tar", "zstd", "ffmpeg"
}

# Checksum tools: with -c they verify a list of files ("verification"); without
# it they hash files (a manifest being built). They read files, they copy nothing.
CHECKSUM_COMMANDS = {
    "sha256sum", "sha1sum", "sha224sum", "sha384sum", "sha512sum", "md5sum", "b2sum", "cksum",
    "b3sum", "xxhsum", "xxh32sum", "xxh64sum", "xxh128sum",
}

DISCOVERY_COMMANDS = TRANSFER_COMMANDS | CHECKSUM_COMMANDS

# A tool started by one of these is followed through it: the item is the xargs
# process, whose I/O counters keep what its finished children did.
XARGS_COMMANDS = {"xargs", "parallel"}

# Tools whose folder sources are sized with du to get an overall total.
SIZED_TOOLS = {"cp", "mv", "tar", "rsync", "rclone"}

MIB = 1024 * 1024

CURRENT_UID = os.getuid()


def runtime_dir():
    """Per-user runtime directory, same rule as runtime_dir() in collect-details.sh."""
    d = os.environ.get("XDG_RUNTIME_DIR", "")
    if d and os.path.isdir(d) and os.access(d, os.W_OK):
        return d
    d = f"/run/user/{CURRENT_UID}"
    if os.path.isdir(d) and os.access(d, os.W_OK):
        return d
    d = f"/tmp/gusthx-resources-{CURRENT_UID}"
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        st = os.lstat(d)
        if stat.S_ISDIR(st.st_mode) and st.st_uid == CURRENT_UID:
            return d
    except OSError:
        pass
    return None


def runtime_file(name):
    d = runtime_dir()
    return os.path.join(d, name) if d else None


# Speeds are deltas between runs, so the previous run is kept here (per user).
STATE_FILE_NAME = "gusthx-resources-transfers.json"
# Written by collect-details.sh: one DISK line per connected iPhone/iPad.
IOS_CACHE_NAME = "gusthx-resources-ios.tsv"

def get_process_tree(root_pid):
    """Recursively collect all descendant PIDs of a root process."""
    pids = [root_pid]
    idx = 0
    while idx < len(pids):
        p = pids[idx]
        idx += 1
        child_path = f"/proc/{p}/task/{p}/children"
        if os.path.exists(child_path):
            try:
                with open(child_path, "r") as f:
                    for cp_str in f.read().split():
                        cp = int(cp_str)
                        if cp not in pids:
                            pids.append(cp)
            except Exception:
                pass
    return pids

def read_proc_io(pid):
    """The I/O counters of one process (rchar, wchar, read_bytes, write_bytes);
    zeros when it is gone. A process's counters include its reaped children."""
    out = {"rchar": 0, "wchar": 0, "read_bytes": 0, "write_bytes": 0}
    try:
        with open(f"/proc/{pid}/io", "r") as f:
            for line in f:
                name, _, value = line.partition(":")
                if name in out:
                    out[name] = int(value.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return out


def read_comm(pid):
    try:
        with open(f"/proc/{pid}/comm", "r") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_ppid(pid):
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            data = f.read()
        return int(data[data.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def read_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def read_start_ticks(pid):
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            data = f.read()
        return int(data[data.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return -1


def read_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a for a in f.read().decode("utf-8", "replace").split("\x00") if a]
    except OSError:
        return []


def process_alive(pid, start_ticks):
    """True while the process that had this pid and start time still runs."""
    return bool(pid) and pid > 0 and read_start_ticks(pid) == start_ticks


def tree_proc_io(pids):
    """Sum of read_proc_io() over a process tree."""
    out = {"rchar": 0, "wchar": 0, "read_bytes": 0, "write_bytes": 0}
    for p in pids:
        io = read_proc_io(p)
        for k in out:
            out[k] += io[k]
    return out


def get_tree_io_and_stat(pids):
    """Aggregate read_bytes, write_bytes, and CPU/RSS across a process tree."""
    total_rb = 0
    total_wb = 0
    total_rss_pages = 0
    utime_total = 0
    stime_total = 0
    state_set = set()

    for p in pids:
        io = read_proc_io(p)
        total_rb += io["read_bytes"]
        total_wb += io["write_bytes"]

        # Read Stat
        try:
            with open(f"/proc/{p}/stat", "r") as f:
                data = f.read()
            parts = data[data.rindex(")") + 2:].split()
            # parts[0] is state: R, S, D, T, Z
            state_set.add(parts[0])
            utime_total += int(parts[11])
            stime_total += int(parts[12])
            total_rss_pages += int(parts[21])
        except Exception:
            pass

    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    rss_bytes = total_rss_pages * page_size

    # Summarize state
    if "D" in state_set:
        effective_state = "disk_wait"
    elif "T" in state_set:
        effective_state = "paused"
    elif "R" in state_set:
        effective_state = "running"
    elif "S" in state_set:
        effective_state = "sleeping"
    else:
        effective_state = "running"

    return total_rb, total_wb, rss_bytes, effective_state

def get_active_file_info(pids, source_hint="", dest_hint="", exclude_paths=None, skip_writing=False):
    """
    Inspect open file descriptors across all processes in the tree.
    Finds the regular file currently being read or written.
    `source_hint` may be one path or a list of them (every source of the job).
    `exclude_paths` are never the active file (a checksum list being read), and
    `skip_writing` ignores files open for writing (the output of a hashing step).
    Files open for appending (logs) only win when nothing else is open.
    """
    candidates = []
    hints = [source_hint] if isinstance(source_hint, str) else list(source_hint or [])
    hints = [h for h in hints + [dest_hint] if h]
    exclude_paths = exclude_paths or set()

    for p in pids:
        fd_dir = f"/proc/{p}/fd"
        if not os.path.isdir(fd_dir):
            continue
        try:
            entries = os.listdir(fd_dir)
        except Exception:
            continue

        for fd in entries:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
                # Exclude pseudo files, sockets, pipes, device nodes, libraries
                if (not target.startswith("/") or
                    target.startswith("/proc") or
                    target.startswith("/dev") or
                    target.startswith("/sys") or
                    target.startswith("/usr/lib") or
                    target.startswith("/lib")):
                    continue
                if target in exclude_paths:
                    continue

                try:
                    st = os.stat(target)
                except Exception:
                    continue

                if not stat.S_ISREG(st.st_mode):
                    continue

                # Read current file offset from fdinfo
                pos = 0
                flags = 0
                fdi_path = f"/proc/{p}/fdinfo/{fd}"
                if os.path.exists(fdi_path):
                    with open(fdi_path, "r") as fdi:
                        for l in fdi:
                            if l.startswith("pos:"):
                                pos = int(l.split()[1])
                            elif l.startswith("flags:"):
                                flags = int(l.split()[1], 8)
                                break
                # A file open for writing grows as it is written: its size is
                # not a total, so it has no percentage.
                writing = (flags & 3) != 0
                appending = bool(flags & os.O_APPEND)
                if writing and skip_writing:
                    continue

                # Calculate hints
                is_src_or_dst = any(target.startswith(h) for h in hints)

                raw_name = os.path.basename(target)
                pct = round((pos / st.st_size * 100), 1) if st.st_size > 0 else 0.0
                pct = 0.0 if writing else max(0.0, min(100.0, pct))

                candidates.append({
                    "pid": p,
                    "fd": fd,
                    "path": target,
                    "name": raw_name,
                    "dir": os.path.dirname(target),
                    "size": st.st_size,
                    "pos": pos,
                    "is_hinted": is_src_or_dst,
                    "writing": writing,
                    "appending": appending,
                    "pct": pct
                })
            except Exception:
                pass

    if not candidates:
        return None

    # Filter out 0-byte or tiny log files if larger files exist
    significant = [c for c in candidates if c["size"] > 1024 * 10] or candidates

    def file_rank(c):
        # 0. Not a log being appended to
        not_log = 0 if c["appending"] else 1
        # 1. Matches source or dest hints
        is_hinted = 1 if c["is_hinted"] else 0
        # 2. Currently actively moving (0 < pos < size)
        is_in_flight = 1 if (0 < c["pos"] < c["size"] and not c["writing"]) else 0
        # 3. Clean real filename rather than rsync temp file (.filename.suffix)
        is_clean = 1 if not c["name"].startswith(".") else 0
        # 4. pos > 0
        has_pos = 1 if c["pos"] > 0 else 0
        # 5. size
        return (not_log, is_hinted, is_in_flight, is_clean, has_pos, c["size"])

    significant.sort(key=file_rank, reverse=True)
    best = significant[0]
    best.pop("appending", None)

    # Clean up display name if it's an rsync hidden temp file (.file.ext.xxxxxx)
    display_name = best["name"]
    if display_name.startswith(".") and len(display_name) > 8:
        parts = display_name.split(".")
        if len(parts) >= 3:
            display_name = ".".join(parts[1:-1])
        else:
            display_name = display_name.lstrip(".")
    best["name"] = display_name
    return best

# The rclone commands that move or read file contents.
RCLONE_TRANSFER_COMMANDS = {"copy", "copyto", "sync", "move", "moveto", "bisync", "copyurl",
                            "check", "cryptcheck", "rcat", "cat"}


def rclone_subcommand(args):
    """The command of an rclone command line: its first word that is not an option."""
    for a in args[1:]:
        if a and not a.startswith("-"):
            return a
    return ""


SCP_OPTS_WITH_ARG = {"-P", "-i", "-o", "-F", "-c", "-l", "-J", "-S", "-D", "-X"}


def transfer_direction(tool, src, dst):
    """"upload" when the destination is on another machine or service, "download"
    when the source is, "" for a transfer inside this computer."""
    dst_remote = bool(dst) and not is_local_path(dst, tool)
    src_remote = bool(src) and not is_local_path(src, tool)
    if dst_remote and not src_remote:
        return "upload"
    if src_remote:
        return "download"
    return ""


def get_tree_char_io(pids):
    """Bytes read and written through any file descriptor (rchar/wchar), sockets
    and cached files included. See network_progress() for what each one means."""
    rc = wc = 0
    for p in pids:
        try:
            with open(f"/proc/{p}/io", "r") as f:
                for line in f:
                    if line.startswith("rchar:"):
                        rc += int(line.split()[1])
                    elif line.startswith("wchar:"):
                        wc += int(line.split()[1])
        except (OSError, ValueError):
            pass
    return rc, wc


def network_progress(direction, rb, wb, rchar, wchar):
    """(bytes done, network byte counter) of a transfer to or from another machine.
    An upload reads the source (rchar) and writes to a socket (wchar); a download
    reads a socket and writes the file. The local side says how far the transfer
    is; the socket side says how fast data is really leaving or arriving (tools
    such as rclone read ahead in big chunks, and may send a chunk again)."""
    if direction == "upload":
        return max(rb, rchar), wchar
    return max(wb, wchar), rchar


# Programs a transfer tool forks to carry its data to another machine. Everything
# the tool sends or receives also goes through them, so summing their counters
# with the tool's would count every byte twice.
NETWORK_PIPES = {"ssh", "dbclient"}


def network_counters(direction, tree_pids, comm_of=None, io_of=None):
    """(bytes done, network byte counter) of a transfer to or from another
    machine (rsync push/pull, scp, sftp, rclone, curl...). The progress comes
    from the tool's own processes only (an ssh child reads, or writes, every byte
    the tool pipes to it); the network counter is the ssh child's socket side
    (what it writes for an upload, reads for a download), or the tool's own
    when it talks to the network itself."""
    comm_of = comm_of or read_comm
    io_of = io_of or read_proc_io
    tool, pipes = {"rchar": 0, "wchar": 0, "read_bytes": 0, "write_bytes": 0}, None
    for p in tree_pids:
        io = io_of(p)
        if comm_of(p) in NETWORK_PIPES:
            if pipes is None:
                pipes = {"rchar": 0, "wchar": 0}
            pipes["rchar"] += io["rchar"]
            pipes["wchar"] += io["wchar"]
        else:
            for k in tool:
                tool[k] += io[k]
    if direction == "upload":
        moved = max(tool["read_bytes"], tool["rchar"])
        wire = pipes["wchar"] if pipes is not None else tool["wchar"]
    else:
        moved = max(tool["write_bytes"], tool["wchar"])
        wire = pipes["rchar"] if pipes is not None else tool["rchar"]
    return moved, wire


def local_progress(base_tool, args, cwd, tree_pids, root_pid, io_of=None, fds_of=None):
    """(bytes, counter) of a copy inside this computer, measured in the logical
    bytes its total is given in (write_bytes is page-rounded and counts
    metadata, read_bytes misses cached files):
    - tar -x/-t: the read position in the archive (the total is the archive's
      size; what it writes is the uncompressed contents). None when the archive
      is not open at this instant.
    - tar -c: what tar itself read (its compressor child is not counted).
    - cp/mv: what they wrote (copy_file_range counts there too).
    (None, "") for other tools: their disk counters are used."""
    io_of = io_of or read_proc_io
    if base_tool == "tar":
        mode, archive, _members, _directory = parse_tar_args(args, cwd)
        if mode in ("x", "t") and archive and archive != "-":
            path = os.path.normpath(os.path.join(cwd, archive)) if cwd else archive
            fds_of = fds_of or _fd_targets
            best = -1
            for p in tree_pids:
                for _fd, target, pos, flags in fds_of(p):
                    if target == path and (flags & 3) != os.O_WRONLY:
                        best = max(best, pos)
            return (best if best >= 0 else None), "archive_read"
        if mode == "c":
            return io_of(root_pid)["rchar"], "source_read"
        return None, ""
    if base_tool in ("cp", "mv"):
        return sum(io_of(p)["wchar"] for p in tree_pids), "logical_write"
    return None, ""


def is_local_path(path, tool=""):
    """False for URLs and for remotes (rsync `host:dir`, rclone `remote:dir`)."""
    if not path or "://" in path:
        return False
    if path.startswith(("/", "./", "../", "~")):
        return True
    base = tool.split()[0] if tool else ""
    if base in ("rsync", "rclone", "git", "scp", "sftp") and ":" in path.split("/", 1)[0]:
        return False
    return True


def absolute_path(path, cwd, tool=""):
    """A relative local path resolved against the process's working directory, so
    the panel shows where the files really are. Remotes and URLs stay as typed."""
    if not path or not cwd or not is_local_path(path, tool) or path.startswith("~"):
        return path
    if os.path.isabs(path) and "/." not in path:
        return path
    keep_slash = path.endswith("/")
    out = os.path.normpath(os.path.join(cwd, path))
    return out + "/" if keep_slash and out != "/" else out


TAR_OPTS_WITH_ARG = {
    "-C", "--directory", "-T", "--files-from", "-X", "--exclude-from", "--exclude",
    "-I", "--use-compress-program", "-b", "--blocking-factor", "-L", "--tape-length",
    "-N", "--newer", "--after-date", "-g", "--listed-incremental", "-H", "--format",
    "-V", "--label", "--transform", "--xform", "--strip-components", "--owner", "--group",
    "--mode", "--mtime", "--checkpoint-action", "--sort", "-K", "--starting-file",
}


def parse_tar_args(args, cwd):
    """Return (mode, archive, members, directory) of a tar command line: mode is
    "c" (create/append), "x" (extract) or "t" (list). Handles bundled short
    options (`-czf a.tgz dir`), the old style without a dash (`cf a.tar dir`),
    `--file=a.tar` and `-C dir`."""
    mode, archive, directory, members = "", "", "", []
    rest = list(args[1:])
    queue = []  # values still owed to bundled short options, in order
    if rest and not rest[0].startswith("-") and all(ch.isalnum() for ch in rest[0]):
        rest[0] = "-" + rest[0]  # old style: the first word is the option bundle
        old_style = True
    else:
        old_style = False
    i = 0
    first = True
    while i < len(rest):
        a = rest[i]
        i += 1
        if a == "--":
            members.extend(rest[i:])
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in ("--create", "--append", "--update"):
                mode = "c"
            elif name in ("--extract", "--get"):
                mode = "x"
            elif name == "--list":
                mode = "t"
            elif name == "--file":
                if eq:
                    archive = value
                elif i < len(rest):
                    archive = rest[i]
                    i += 1
            elif name == "--directory":
                if eq:
                    directory = value
                elif i < len(rest):
                    directory = rest[i]
                    i += 1
            elif name in TAR_OPTS_WITH_ARG and not eq:
                i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            letters = a[1:]
            j = 0
            while j < len(letters):
                ch = letters[j]
                j += 1
                if ch in "cru":
                    mode = "c"
                elif ch == "x":
                    mode = "x"
                elif ch == "t":
                    mode = "t"
                elif ch == "f" or ("-" + ch) in TAR_OPTS_WITH_ARG:
                    # The value is the rest of this word, or the next word (old
                    # style: the next words, in the order of the letters).
                    if letters[j:] and not (old_style and first):
                        value = letters[j:]
                        j = len(letters)
                    elif i < len(rest):
                        value = rest[i]
                        i += 1
                    else:
                        value = ""
                    if ch == "f":
                        archive = value
                    elif ch == "C":
                        directory = value
            first = False
            continue
        first = False
        members.append(a)
    return mode, archive, members, directory


# rsync options that take a value (`--opt=VAL` or `--opt VAL`), from `rsync --help`
# of rsync 3.5.1 plus the documented aliases (--cc, --zc, --zl, --zt, --log-format,
# --time-limit) and the daemon options. A test checks this list against the
# installed rsync, so a value is never taken for a source or a flag.
RSYNC_LONG_WITH_ARG = frozenset("""
--info --debug --stderr --backup-dir --suffix --confine-root --chmod --checksum-choice --cc
--block-size --rsh --rsync-path --max-delete --max-size --min-size --max-alloc --partial-dir
--usermap --groupmap --chown --timeout --contimeout --modify-window --temp-dir --compare-dest
--copy-dest --link-dest --compress-choice --zc --compress-level --zl --compress-threads --zt
--skip-compress --filter --exclude --exclude-from --include --include-from --files-from
--copy-as --address --port --sockopts --outbuf --remote-option --out-format --log-format
--log-file --log-file-format --password-file --early-input --bwlimit --stop-after --time-limit
--stop-at --write-batch --only-write-batch --read-batch --protocol --iconv --checksum-seed
--config --dparam
""".split())

# Short options that take a value: the rest of the word, or the next word.
# (-e --rsh, -T --temp-dir, -B --block-size, -f --filter, -M --remote-option,
# -@ --modify-window). -F takes none.
RSYNC_SHORT_WITH_ARG = frozenset("eTBfM@")


def _rsync_tokens(args):
    """Split an rsync command line (args[0] is the program) into tokens:
    {"t": "long", "name", "val" (None for a flag), "words"},
    {"t": "short", "letters": [(letter, value or None)], "words"},
    {"t": "operand", "val", "words"} and {"t": "end", "words"} for `--`.
    "words" are the argv words the token was made of, so a command line can be
    rebuilt without some of its options."""
    toks = []
    i = 1
    rest = False
    while i < len(args):
        a = args[i]
        i += 1
        if rest or a == "-" or not a.startswith("-"):
            toks.append({"t": "operand", "val": a, "words": [a]})
            continue
        if a == "--":
            rest = True
            toks.append({"t": "end", "words": [a]})
            continue
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            words = [a]
            if name in RSYNC_LONG_WITH_ARG:
                if not eq:
                    val = args[i] if i < len(args) else ""
                    if i < len(args):
                        words.append(args[i])
                    i += 1
                toks.append({"t": "long", "name": name, "val": val, "words": words})
            else:
                toks.append({"t": "long", "name": name, "val": None, "words": words})
            continue
        letters = []
        words = [a]
        j = 1
        while j < len(a):
            ch = a[j]
            j += 1
            if ch in RSYNC_SHORT_WITH_ARG:
                if j < len(a):
                    val = a[j:]
                else:
                    val = args[i] if i < len(args) else ""
                    if i < len(args):
                        words.append(args[i])
                    i += 1
                letters.append((ch, val))
                break
            letters.append((ch, None))
        toks.append({"t": "short", "letters": letters, "words": words})
    return toks


def _rsync_operand_remote(p):
    return not is_local_path(p, "rsync")


def _rsync_stdin_filter(rule):
    """True for a filter rule that merges a file read from stdin (`merge -`, `. -`)."""
    parts = (rule or "").strip().split(None, 1)
    if len(parts) != 2 or parts[1].strip() != "-":
        return False
    head = parts[0].split(",")[0]
    return head.startswith("merge") or head.startswith(".")


def parse_rsync_args(args):
    """Everything the collector needs from an rsync command line.
    Option values are never taken for operands or flags (`-e "ssh -n"`,
    `--exclude=-n`, `--exclude -n`, `-f'- n'` are not a dry run), grouped short
    options are split (`-rltn` is a dry run), and later options win over earlier
    ones (`-n --no-dry-run`)."""
    operands, flags, values = [], set(), {}
    dry = listing = checksum = False
    for tok in _rsync_tokens(args):
        t = tok["t"]
        if t == "operand":
            operands.append(tok["val"])
        elif t == "long":
            name = tok["name"]
            if tok["val"] is not None:
                values.setdefault(name, []).append(tok["val"])
                continue
            flags.add(name)
            if name == "--dry-run":
                dry = True
            elif name in ("--no-dry-run", "--no-n"):
                dry = False
            elif name == "--list-only":
                listing = True
            elif name == "--no-list-only":
                listing = False
            elif name == "--checksum":
                checksum = True
            elif name in ("--no-checksum", "--no-c"):
                checksum = False
        elif t == "short":
            for ch, val in tok["letters"]:
                if val is not None:
                    values.setdefault("-" + ch, []).append(val)
                    continue
                flags.add("-" + ch)
                if ch == "n":
                    dry = True
                elif ch == "c":
                    checksum = True
    list_only = listing or len(operands) == 1
    if list_only:
        sources, destination = list(operands), ""
    else:
        sources = operands[:-1]
        destination = operands[-1] if len(operands) >= 2 else ""
    remote_mode = "local"
    if any(_rsync_operand_remote(s) for s in sources):
        remote_mode = "pull"
    elif destination and _rsync_operand_remote(destination):
        remote_mode = "push"
    stdin_lists = any(v == "-" for name in ("--files-from", "--exclude-from", "--include-from")
                      for v in values.get(name, []))
    stdin_lists = stdin_lists or any(_rsync_stdin_filter(v) for name in ("--filter", "-f")
                                     for v in values.get(name, []))
    return {
        "operands": operands,
        "sources": sources,
        "destination": destination,
        "dry_run": dry,
        "list_only": list_only,
        "server": "--server" in flags or "--sender" in flags,
        "daemon": "--daemon" in flags,
        "remote_mode": remote_mode,
        "checksum": checksum,
        "stdin_lists": stdin_lists,
        "batch_mode": any(n in values for n in ("--write-batch", "--only-write-batch", "--read-batch")),
        "flags": flags,
        "values": values,
    }


def classify_kind(comm, args):
    """(kind, verify_mode) of a command: kind is "transfer", "simulation" (rsync
    dry run), "listing" (rsync --list-only, or one operand) or "verification"
    (a checksum tool); verify_mode is "check" (-c), "hash" or ""."""
    comm = os.path.basename(comm or (args[0] if args else ""))
    if comm == "rsync":
        p = parse_rsync_args(args)
        if p["dry_run"]:
            return "simulation", ""
        if p["list_only"]:
            return "listing", ""
        return "transfer", ""
    if comm in CHECKSUM_COMMANDS:
        return "verification", ("check" if checksum_args(comm, args)["check"] else "hash")
    return "transfer", ""


# Options of the checksum tools that take a value (b2sum -l, b3sum --num-threads,
# cksum -a ...): their value is never a file to hash.
CHECKSUM_OPTS_WITH_ARG = {"-l", "--length", "--num-threads", "-a", "--algorithm"}


def checksum_args(comm, args, cwd=""):
    """{check, lists, files} of a checksum command line: with -c/--check the
    operands are lists to verify, otherwise files to hash. Operands are
    resolved against cwd; "-" is stdin (also when no operand is given)."""
    check = False
    ops = []
    i = 1
    rest = False
    while i < len(args):
        a = args[i]
        i += 1
        if rest or a == "-" or not a.startswith("-"):
            ops.append(a)
            continue
        if a == "--":
            rest = True
            continue
        if a.startswith("--"):
            name, eq, _val = a.partition("=")
            if name == "--check":
                check = True
            elif name in CHECKSUM_OPTS_WITH_ARG and not eq:
                i += 1
            continue
        j = 1
        while j < len(a):
            ch = a[j]
            j += 1
            if ch == "c":
                check = True
            elif "-" + ch in CHECKSUM_OPTS_WITH_ARG:
                if j >= len(a):
                    i += 1
                break
    resolved = [o if o == "-" or os.path.isabs(o) or not cwd else os.path.normpath(os.path.join(cwd, o))
                for o in ops]
    if check:
        return {"check": True, "lists": resolved or ["-"], "files": []}
    return {"check": False, "lists": [], "files": resolved or ["-"]}


def rsync_roles(root_pid, tree_pids, remote_mode, comm_of=None, ppid_of=None, cwd_of=None):
    """The processes of an rsync run: {"sender", "generator", "receiver"}.
    A local copy forks twice: the root is the sender, its rsync child the
    generator, and the grandchild the receiver, which writes the files
    (generator and receiver both run in the destination). A pull runs the
    generator as the root and the receiver as its child; a push only has the
    sender here. A missing role (just forked) is None."""
    comm_of = comm_of or read_comm
    ppid_of = ppid_of or read_ppid
    cwd_of = cwd_of or read_cwd
    chain = [root_pid]
    cur = root_pid
    while len(chain) < 3:
        kids = [p for p in tree_pids if p != cur and p not in chain
                and ppid_of(p) == cur and comm_of(p) == "rsync"]
        if len(kids) != 1:
            break
        cur = kids[0]
        chain.append(cur)
    roles = {"sender": None, "generator": None, "receiver": None}
    if remote_mode == "push":
        roles["sender"] = root_pid
    elif remote_mode == "pull":
        roles["generator"] = root_pid
        if len(chain) >= 2:
            roles["receiver"] = chain[1]
    else:
        roles["sender"] = root_pid
        if len(chain) >= 2:
            roles["generator"] = chain[1]
        if len(chain) >= 3:
            c1, c2 = cwd_of(chain[1]), cwd_of(chain[2])
            if c1 and c1 == c2:
                roles["receiver"] = chain[2]
    return roles


def writing_position(pid):
    """Bytes already written into the files a process has open for writing
    (rsync's receiver: its temporary file), logs opened for appending and
    stdio left out."""
    total = 0
    fd_dir = f"/proc/{pid}/fd"
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return 0
    for fd in entries:
        if fd in ("0", "1", "2"):
            continue
        try:
            target = os.readlink(f"{fd_dir}/{fd}")
            if not target.startswith("/") or target.startswith(("/proc", "/dev", "/sys")):
                continue
            pos = flags = 0
            with open(f"/proc/{pid}/fdinfo/{fd}") as fh:
                for line in fh:
                    if line.startswith("pos:"):
                        pos = int(line.split()[1])
                    elif line.startswith("flags:"):
                        flags = int(line.split()[1], 8)
            if (flags & 3) == 0 or flags & os.O_APPEND:
                continue
            if not stat.S_ISREG(os.stat(target).st_mode):
                continue
            total += pos
        except (OSError, ValueError, IndexError):
            continue
    return total


# ---------------------------------------------------------------------------
# The size of what an rsync run still has to copy: a background dry run.

# The helper's own rsync carries this in its command line (rsync forks its
# children without exec, so all three processes do): it is never listed.
SIZER_MARKER = "--out-format=gusthx-resources-size:"
SIZER_LINE = "gusthx-resources-size:"
SIZER_OUT_FORMAT = SIZER_MARKER + "%i|%l|%n"
HELPER_ENV = "GUSTHX_RESOURCES_HELPER"

# Options the dry run does without: logs, progress and output that would change
# what it prints, -c (the dry run must not read file contents) and anything
# that could switch the dry run off again.
RSYNC_SIZING_STRIP_LONG = {
    "--log-file", "--log-file-format", "--info", "--debug", "--out-format", "--log-format",
    "--stats", "--progress", "--outbuf", "--stderr", "--verbose", "--quiet", "--itemize-changes",
    "--human-readable", "--checksum", "--no-dry-run", "--no-n", "--msgs2stderr",
}
RSYNC_SIZING_STRIP_SHORT = set("vqihPc")


def is_sizing_helper(args):
    return any(a.startswith(SIZER_MARKER) for a in args)


def _rsync_strip_for_sizing(args):
    """args[1:] without the options in RSYNC_SIZING_STRIP_*; option values are
    left alone (`--exclude -v` keeps its value)."""
    out = []
    for tok in _rsync_tokens(args):
        if tok["t"] == "long":
            if tok["name"] in RSYNC_SIZING_STRIP_LONG:
                continue
            out.extend(tok["words"])
        elif tok["t"] == "short":
            kept = [(ch, val) for ch, val in tok["letters"]
                    if val is not None or ch not in RSYNC_SIZING_STRIP_SHORT]
            if not kept:
                continue
            last_ch, last_val = kept[-1]
            if last_val is None:
                out.append("-" + "".join(ch for ch, _v in kept))
            elif len(tok["words"]) == 1:  # value glued to the letter: -T/tmp
                out.append("-" + "".join(ch for ch, _v in kept) + last_val)
            else:
                out.append("-" + "".join(ch for ch, _v in kept))
                out.extend(tok["words"][1:])
        else:
            out.extend(tok["words"])
    return out


def rsync_sizing_argv(args, cwd=""):
    """(argv, skip_reason) of the dry run that sizes an rsync run, or (None,
    reason) when it cannot be sized that way: "remote", "stdin_list",
    "batch_mode", "copy_as", "server" or "listing". The dry run keeps every
    option that decides what is copied (excludes, filters, --files-from,
    --relative, --link-dest, every source, the destination), so it sees what
    the copy sees."""
    if not args:
        return None, "error"
    p = parse_rsync_args(args)
    if p["server"] or p["daemon"]:
        return None, "server"
    if p["list_only"] or not p["destination"]:
        return None, "listing"
    if p["remote_mode"] != "local":
        return None, "remote"
    if p["stdin_lists"]:
        return None, "stdin_list"
    for name in ("--files-from", "--exclude-from", "--include-from"):
        for v in p["values"].get(name, []):
            # A pipe or device (`<(...)`, /dev/stdin) was read by the copy
            # already: a second reader would block or take its data.
            path = v if os.path.isabs(v) or not cwd else os.path.join(cwd, v)
            try:
                if not stat.S_ISREG(os.stat(path).st_mode):
                    return None, "stdin_list"
            except OSError:
                pass
    if p["batch_mode"]:
        return None, "batch_mode"
    if "--copy-as" in p["values"]:
        return None, "copy_as"
    prog = args[0]
    if "/" in prog and not os.path.isabs(prog) and cwd:
        prog = os.path.normpath(os.path.join(cwd, prog))
    argv = [prog, "--dry-run", "--stats", "--no-human-readable", SIZER_OUT_FORMAT]
    return argv + _rsync_strip_for_sizing(args), ""


def rsync_size_key(cwd, args):
    """Cache key of a dry run: the working directory and the command line
    without the options that do not change what is copied (logs, verbosity)."""
    normalized = _rsync_strip_for_sizing(args)
    h = hashlib.sha256((cwd + "\0" + "\0".join(normalized)).encode("utf-8", "replace")).hexdigest()
    return "rs-" + h[:16]


def rsync_job_key(cwd, args, pid, start):
    """The size-job key of one rsync instance: what it copies (rsync_size_key)
    and which process it is (a result depends on the destination's state when
    that instance started, so it is never shared)."""
    return f"{rsync_size_key(cwd, args)}-{int(pid)}-{int(start)}"


def du_size_key(tool, paths):
    """Cache key of a du run: what is sized, not which process asked, so a new
    transfer over the same folders reuses a recent answer."""
    h = hashlib.sha256((tool + "\0" + "\0".join(sorted(paths))).encode("utf-8", "replace")).hexdigest()
    return "du-" + h[:16]


RSYNC_STATS_PATTERNS = {
    "files_total": re.compile(r"^Number of files: ([\d,.]+)", re.M),
    "files_to_transfer": re.compile(r"^Number of regular files transferred: ([\d,.]+)", re.M),
    "total_file_size": re.compile(r"^Total file size: ([\d,.]+) bytes", re.M),
    "transfer_size": re.compile(r"^Total transferred file size: ([\d,.]+) bytes", re.M),
}
RSYNC_FILES_REG = re.compile(r"^Number of files: [\d,.]+ \(reg: ([\d,.]+)", re.M)


def parse_rsync_stats(text):
    """The numbers of an `rsync --stats` block (missing ones are left out)."""
    out = {}
    for name, rx in RSYNC_STATS_PATTERNS.items():
        m = rx.search(text or "")
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                out[name] = int(digits)
    m = RSYNC_FILES_REG.search(text or "")
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if digits:
            out["files_reg"] = int(digits)
    return out


def parse_command_details(args, cwd):
    """(tool, source, destination) of a command line; see parse_command()."""
    info = parse_command(args, cwd)
    return info["tool"], info["source"], info["destination"]


def parse_command(args, cwd):
    """Tool name, source and destination of a transfer command. Local paths come
    back absolute (resolved against `cwd`); `sources` lists every local source,
    for sizing the whole job."""
    tool, src, dst, sources = _parse_command_raw(args, cwd)
    if sources is None:
        sources = [src] if src else []
    return {
        "tool": tool,
        "source": absolute_path(src, cwd, tool),
        "destination": absolute_path(dst, cwd, tool),
        "sources": [absolute_path(p, cwd, tool) for p in sources if is_local_path(p, tool)],
        # Every source operand, local ones absolute and remotes as typed.
        "all_sources": [absolute_path(p, cwd, tool) for p in sources if p],
    }


def _parse_command_raw(args, cwd):
    tool, src, dst = _parse_tool_source_destination(args, cwd)
    comm = os.path.basename(args[0]) if args else ""
    sources = None
    if comm == "rsync":
        sources = parse_rsync_args(args)["sources"]
    elif comm == "tar":
        mode, archive, members, directory = parse_tar_args(args, cwd)
        base = os.path.join(cwd, directory) if directory and cwd else (directory or cwd)
        if mode == "c":
            members = [os.path.join(base, m) if base else m for m in members]
            src = members[0] if members else ""
            dst = archive if archive and archive != "-" else ""
            sources = members
        elif mode in ("x", "t"):
            src = archive if archive and archive != "-" else ""
            dst = base if mode == "x" else ""
            sources = [src] if src else []
        else:
            src, dst, sources = "", "", []
    elif comm in ("cp", "mv") and src:
        sources = _cp_sources(args)
    return tool, src, dst, sources


def _cp_sources(args):
    """Every source of a cp/mv command line (all operands but the target)."""
    target_dir = False
    pos_args = []
    i = 1
    while i < len(args):
        arg = args[i]
        if arg in ("-t", "--target-directory") and i + 1 < len(args):
            target_dir = True
            i += 2
            continue
        if arg.startswith("--target-directory="):
            target_dir = True
        elif not arg.startswith("-"):
            pos_args.append(arg)
        i += 1
    return pos_args if target_dir else pos_args[:-1]


def _parse_tool_source_destination(args, cwd):
    if not args:
        return "transfer", "", ""
    comm = os.path.basename(args[0])

    if comm == "rsync":
        parsed = parse_rsync_args(args)
        return "rsync", (parsed["sources"][0] if parsed["sources"] else ""), parsed["destination"]

    elif comm in ("cp", "mv"):
        target_dir = None
        pos_args = []
        i = 1
        while i < len(args):
            arg = args[i]
            if arg in ("-t", "--target-directory") and i + 1 < len(args):
                target_dir = args[i + 1]
                i += 2
                continue
            if arg.startswith("--target-directory="):
                target_dir = arg.split("=", 1)[1]
                i += 1
                continue
            if arg.startswith("-"):
                i += 1
                continue
            pos_args.append(arg)
            i += 1

        if target_dir:
            src = pos_args[0] if pos_args else ""
            return comm, src, target_dir
        if len(pos_args) >= 2:
            return comm, pos_args[-2], pos_args[-1]
        elif pos_args:
            return comm, pos_args[0], ""
        return comm, "", ""

    elif comm == "dd":
        src, dst = "", ""
        for a in args[1:]:
            if a.startswith("if="):
                src = a[3:]
            elif a.startswith("of="):
                dst = a[3:]
        return "dd", src, dst

    elif comm in ("scp", "sftp"):
        pos_args = []
        i = 1
        while i < len(args):
            if args[i] in SCP_OPTS_WITH_ARG:
                i += 2
                continue
            if not args[i].startswith("-"):
                pos_args.append(args[i])
            i += 1
        if comm == "sftp":
            return comm, (pos_args[0] if pos_args else ""), cwd
        return comm, (pos_args[0] if pos_args else ""), (pos_args[-1] if len(pos_args) > 1 else "")

    elif comm == "rclone":
        sub = "copy"
        pos_args = []
        opts_with_arg = {"--transfers", "--checkers", "--drive-chunk-size", "--stats", "--bwlimit",
                         "--config", "--include", "--exclude", "--filter", "--include-from",
                         "--exclude-from", "--files-from", "--log-file", "--log-level",
                         "--max-size", "--min-size", "--max-age", "--min-age", "--tpslimit",
                         "--backup-dir", "--retries", "--buffer-size", "--max-transfer"}
        skip = False
        for a in args[1:]:
            if skip:
                skip = False
                continue
            if a in opts_with_arg:
                skip = True
                continue
            if not a.startswith("-") and not pos_args:
                sub = a
                pos_args.append(a)
            elif not a.startswith("-"):
                pos_args.append(a)
        if len(pos_args) >= 3:
            return f"rclone {sub}", pos_args[1], pos_args[2]
        elif len(pos_args) == 2:
            return f"rclone {sub}", pos_args[1], ""
        return f"rclone {sub}", "", ""

    elif comm in ("curl", "wget", "aria2c", "yt-dlp"):
        urls = [a for a in args[1:] if not a.startswith("-") and ("://" in a or a.startswith("http"))]
        dst = ""
        i = 1
        while i < len(args):
            a = args[i]
            if a in ("-o", "--output") and i + 1 < len(args):
                dst = args[i + 1]
                i += 2
                continue
            if a == "-O" and comm == "curl":
                if urls:
                    dst = os.path.join(cwd, os.path.basename(urls[0].split("?")[0]))
                i += 1
                continue
            if a in ("-d", "--dir") and i + 1 < len(args) and comm == "aria2c":
                dst = args[i + 1]
                i += 2
                continue
            i += 1
        return comm, (urls[0] if urls else ""), (dst or cwd)

    elif comm == "git":
        sub = args[1] if len(args) > 1 else ""
        opts_with_arg = {"--depth", "-b", "--branch", "--reference", "-o", "--origin", "-u", "--upload-pack"}
        pos_args = []
        i = 2
        while i < len(args):
            a = args[i]
            if a in opts_with_arg and i + 1 < len(args):
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            pos_args.append(a)
            i += 1
        return f"git {sub}", (pos_args[0] if pos_args else ""), (pos_args[1] if len(pos_args) > 1 else cwd)

    elif comm == "idevicebackup2":
        sub, _udid, dst = parse_idevicebackup2_args(args, cwd)
        return f"idevicebackup2 {sub}", "", dst

    elif comm in ("pacman", "yay", "paru"):
        pkg_targets = [a for a in args[1:] if not a.startswith("-")]
        pkg_desc = ", ".join(pkg_targets[:3]) if pkg_targets else "Packages"
        if len(pkg_targets) > 3:
            pkg_desc += f" (+{len(pkg_targets)-3})"
        return comm, f"Arch Repos ({pkg_desc})", "/var/cache/pacman/pkg"

    # Anything else: the first and last operands, never an option such as `-cf`.
    operands = [a for a in args[1:] if not a.startswith("-")]
    return comm, (operands[0] if operands else ""), (operands[-1] if len(operands) > 1 else "")

# idevicebackup2 [OPTIONS] CMD [CMDOPTIONS] DIRECTORY
IDEVICEBACKUP2_OPTS_WITH_ARG = {"-u", "--udid", "-s", "--source", "--password"}


def parse_idevicebackup2_args(args, cwd):
    """Return (subcommand, udid, backup directory) of an idevicebackup2 command line.
    Values of options such as `-u UDID` are skipped, so they are never taken
    for the subcommand or the directory."""
    pos_args = []
    udid = ""
    i = 1
    while i < len(args):
        a = args[i]
        if "=" in a and a.split("=", 1)[0] in IDEVICEBACKUP2_OPTS_WITH_ARG:
            if a.startswith(("-u=", "--udid=")):
                udid = a.split("=", 1)[1]
            i += 1
            continue
        if a in IDEVICEBACKUP2_OPTS_WITH_ARG:
            if a in ("-u", "--udid") and i + 1 < len(args):
                udid = args[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        pos_args.append(a)
        i += 1
    sub = pos_args[0] if pos_args else "backup"
    dst = pos_args[-1] if len(pos_args) > 1 else cwd
    return sub, udid, dst


def ios_used_bytes(udid=""):
    """Space used on a connected iPhone/iPad: the closest thing to the size of a
    full backup that is known up front (it is only an estimate). Read from the
    cache collect-details.sh keeps; when that is missing, ask ideviceinfo once."""
    rows = []
    path = runtime_file(IOS_CACHE_NAME)
    try:
        with open(path) as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 6 and p[0] == "DISK":
                    rows.append((p[8] if len(p) > 8 else "", int(float(p[5]))))
    except (OSError, TypeError, ValueError):
        rows = []
    if udid:
        for row_udid, used in rows:
            if row_udid == udid:
                return used
    elif rows:
        return rows[0][1]
    try:
        cmd = ["ideviceinfo", "-q", "com.apple.disk_usage"]
        if udid:
            cmd[1:1] = ["-u", udid]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    total = avail = 0
    for line in out.splitlines():
        if line.startswith("TotalDataCapacity:"):
            total = int(line.split()[1])
        elif line.startswith("AmountDataAvailable:"):
            avail = int(line.split()[1])
    return max(0, total - avail) if total > 0 else 0


SIZE_FILE_PREFIX = "gusthx-resources-size-"
SIZING_LOCK_NAME = "gusthx-resources-sizing.lock"
DU_TTL = 15 * 60          # a du answer is reused this long, by any transfer
RSYNC_TTL = 10 * 60       # a dry-run answer is kept this long after its rsync ends
SIZE_JOB_TIMEOUT = 30 * 60
SIZE_JOB_STALE = 60


def _size_paths(key):
    d = runtime_dir()
    if not d:
        return None, None
    base = os.path.join(d, SIZE_FILE_PREFIX + key)
    return base + ".json", base + ".req.json"


def _write_json_atomic(path, data):
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def read_size_job(key):
    path, _req = _size_paths(key)
    if not path:
        return None
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _helper_alive(job):
    """True while the helper process that owns a size job still runs."""
    pid = int(job.get("helper_pid") or 0)
    if pid <= 0:
        return False
    args = read_cmdline(pid)
    return "--size-job" in args


def _job_stale(job, now):
    """A queued/scanning job whose helper died without a word."""
    if job.get("state") not in ("queued", "scanning"):
        return False
    if now - float(job.get("updated") or 0) < SIZE_JOB_STALE:
        return False
    return not _helper_alive(job)


def _helper_command(req_path):
    cmd = [sys.executable, os.path.abspath(__file__), "--size-job", req_path]
    prefix = []
    for tool, args in (("nice", ["-n", "19"]), ("ionice", ["-c3"])):
        if any(os.access(os.path.join(p, tool), os.X_OK) for p in os.environ.get("PATH", "/usr/bin").split(":") if p):
            prefix += [tool] + args
    return prefix + cmd


def request_size_job(key, mode, cwd, argv, paths, target_pid=0, target_start=0, now=None):
    """Write a size request and start the helper for it in the background
    (its own session, nice 19, idle I/O class). Returns the job as written."""
    now = time.time() if now is None else now
    job_path, req_path = _size_paths(key)
    if not job_path:
        return None
    req = {"key": key, "mode": mode, "cwd": cwd, "argv": argv, "paths": paths,
           "target_pid": target_pid, "target_start": target_start, "created": now}
    job = _new_job(req, now)
    try:
        _write_json_atomic(req_path, req)
        _write_json_atomic(job_path, job)
        env = dict(os.environ, **{HELPER_ENV: "1"})
        cmd = _helper_command(req_path)
        # Spawned, not waited for: it outlives this run (its own session).
        devnull = [(os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                   (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                   (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0)]
        os.posix_spawnp(cmd[0], cmd, env, file_actions=devnull, setsid=True)
    except OSError as e:
        job.update(state="failed", error=f"spawn: {e}", finished=now)
        try:
            _write_json_atomic(job_path, job)
        except OSError:
            pass
    return job


def _new_job(req, now):
    return {"key": req["key"], "mode": req["mode"], "state": "queued", "helper_pid": 0,
            "started": now, "updated": now, "finished": 0, "found_bytes": 0, "found_files": 0,
            "transfer_size": 0, "total_file_size": 0, "files_total": 0, "files_to_transfer": 0,
            "moved_at_cross": None, "cross_at": 0, "cross_counter": "",
            "target_pid": req.get("target_pid", 0), "target_start": req.get("target_start", 0),
            "rc": None, "error": "", "paused": False}


def rsync_progress_counter(pid, tree_pids=None, parsed=None):
    """(bytes, counter) an rsync run has written so far: the receiver's wchar
    (logical bytes, as rsync's own totals count them), or the write_bytes of the
    whole tree while the receiver is not known yet."""
    tree_pids = tree_pids if tree_pids is not None else get_process_tree(pid)
    if parsed is None:
        parsed = parse_rsync_args(read_cmdline(pid))
    roles = rsync_roles(pid, tree_pids, parsed["remote_mode"])
    if roles["receiver"]:
        return read_proc_io(roles["receiver"])["wchar"], "receiver_write", roles["receiver"]
    return tree_proc_io(tree_pids)["write_bytes"], "tree_write", None


def _sample_cross(target_pid, appending=False):
    """What the copy had written when the dry run caught up with it, minus the
    file it is writing right now (the dry run counts that one in full). With
    --append the dry run only counts what that file still lacks, so nothing
    is taken off."""
    if not target_pid:
        return 0, ""
    moved, counter, receiver = rsync_progress_counter(target_pid)
    if receiver and not appending:
        moved -= writing_position(receiver)
    return max(0, moved), counter


def append_destination(argv, cwd):
    """For an rsync command line with --append/--append-verify: (destination,
    is a folder), to find the files it appends to; None otherwise."""
    p = parse_rsync_args(argv)
    if not ({"--append", "--append-verify"} & p["flags"]) or not p["destination"]:
        return None
    dest = p["destination"]
    path = os.path.normpath(os.path.join(cwd, dest)) if cwd else dest
    return path, dest.endswith("/") or os.path.isdir(path)


def existing_size(append, name):
    """Size of the destination file an rsync --append run writes `name` (the
    dry run's %n) into; 0 when there is none."""
    dest, is_dir = append
    path = os.path.join(dest, name) if is_dir else dest
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    return st.st_size if stat.S_ISREG(st.st_mode) else 0


def _read_json_file(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def target_paused(pid):
    """True while the process a size job works for is stopped (SIGSTOP: the
    thermal guard, or the plugin's pause button)."""
    return bool(pid) and process_state(pid) in ("T", "t")


def run_size_job(req_path):
    """The background helper (`collect-transfers.py --size-job REQ`): one heavy
    scan at a time machine-wide (a shared lock), progress written every 2 s,
    gives up when the transfer it sizes is gone or after 30 minutes of work.
    While that transfer is stopped (paused), the scan is stopped too, and a
    queued one does not start: a pause is often there to let the machine cool.
    A second helper for the same job (two collectors raced) leaves it alone."""
    req = _read_json_file(req_path)
    if req is None:
        return 1
    key = req.get("key", "")
    job_path, _ = _size_paths(key)
    if not job_path or os.path.dirname(job_path) != os.path.dirname(req_path):
        job_path = os.path.join(os.path.dirname(req_path), SIZE_FILE_PREFIX + key + ".json")
    now = time.time()
    job = _new_job(req, now)
    me = os.getpid()
    job["helper_pid"] = me
    target_pid = int(req.get("target_pid") or 0)
    target_start = int(req.get("target_start") or 0)

    def taken_by_other():
        """The job file belongs to another helper for the same instance, one
        that still works on it or already finished it."""
        cur = _read_json_file(job_path)
        if not cur:
            return False
        hp = int(cur.get("helper_pid") or 0)
        if not hp or hp == me:
            return False
        if cur.get("target_pid") != job["target_pid"] or cur.get("target_start") != job["target_start"]:
            return False
        return cur.get("state") == "done" or (cur.get("state") in ("queued", "scanning") and _helper_alive(cur))

    def save(**changes):
        job.update(changes)
        job["updated"] = time.time()
        try:
            _write_json_atomic(job_path, job)
        except OSError:
            pass

    def target_alive():
        return not target_pid or process_alive(target_pid, target_start)

    def paused():
        return target_paused(target_pid)

    if taken_by_other():
        return 0
    save()
    lock_path = os.path.join(os.path.dirname(job_path), SIZING_LOCK_NAME)
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        save(state="failed", error=f"lock: {e}", finished=time.time())
        return 1
    try:
        last_beat = time.time()
        while True:
            if not paused():
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    pass
            if not target_alive():
                save(state="cancelled", finished=time.time())
                return 0
            if time.time() - last_beat > 20:
                if taken_by_other():
                    return 0
                save(paused=paused())
                last_beat = time.time()
            time.sleep(1.0)
        if not target_alive():
            save(state="cancelled", finished=time.time())
            return 0
        if taken_by_other():
            return 0
        save(state="scanning", started=time.time(), paused=False)
        if req.get("mode") == "du":
            return _run_du(req, job, save, target_alive, paused)
        return _run_rsync_dry_run(req, job, save, target_alive, target_pid, paused)
    finally:
        os.close(lock_fd)


def _run_du(req, job, save, target_alive, paused=None):
    paths = [p for p in req.get("paths") or [] if p]
    env = dict(os.environ, LC_ALL="C", **{HELPER_ENV: "1"})
    try:
        proc = subprocess.Popen(["du", "-sbc", "--"] + paths, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    except OSError as e:
        save(state="failed", error=str(e), finished=time.time())
        return 1
    out, rc, err = _drain(proc, save, target_alive, lambda line: None, target_paused=paused)
    if err:
        save(state=err[0], error=err[1], finished=time.time(), rc=rc)
        return 0
    total = None
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].strip() == "total":
            try:
                total = int(parts[0])
            except ValueError:
                pass
    if total is None:
        save(state="failed", error="no total", finished=time.time(), rc=rc)
        return 0
    save(state="done", found_bytes=total, transfer_size=total, total_file_size=total,
         finished=time.time(), rc=rc)
    return 0


def _drain(proc, save, target_alive, on_line, keep_other=400, target_paused=None, check_every=5.0,
           timeout=None):
    """Read a helper command's stdout until it exits, calling on_line for each
    line; every 2 s write progress, every 5 s check the target. While the
    target is stopped the command is stopped too (SIGSTOP to its process
    group, SIGCONT when the target goes on), and that time does not count
    toward the timeout. Returns (other output, rc, None) or ("", rc, (state,
    error)) when it was stopped for good."""
    timeout = SIZE_JOB_TIMEOUT if timeout is None else timeout
    fd = proc.stdout.fileno()
    buf = b""
    other = []
    started = last_save = last_check = time.time()
    held_since = 0.0   # when the command was stopped (0: it runs)
    held_total = 0.0
    while True:
        r, _w, _x = select.select([fd], [], [], 1.0)
        if r:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            lines = buf.split(b"\n")
            buf = lines.pop()
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                if on_line(line) is None:
                    other.append(line)
                    if len(other) > keep_other:
                        del other[:len(other) - keep_other]
        now = time.time()
        if target_paused is not None:
            stop = bool(target_paused())
            if stop and not held_since:
                _signal_group(proc, signal.SIGSTOP)
                held_since = now
                save(paused=True)
                last_save = now
            elif not stop and held_since:
                _signal_group(proc, signal.SIGCONT)
                held_total += now - held_since
                held_since = 0.0
                save(paused=False)
                last_save = now
        if now - last_save >= 2:
            save()
            last_save = now
        if now - last_check >= check_every:
            last_check = now
            if not target_alive():
                _kill_group(proc)
                return "", proc.wait(), ("cancelled", "")
        worked = now - started - held_total - ((now - held_since) if held_since else 0.0)
        if worked > timeout:
            _kill_group(proc)
            return "", proc.wait(), ("failed", "timeout")
    if buf:
        line = buf.decode("utf-8", "replace")
        if on_line(line) is None:
            other.append(line)
    proc.stdout.close()
    return "\n".join(other), proc.wait(), None


def _signal_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        pass


def _kill_group(proc):
    _signal_group(proc, signal.SIGTERM)
    _signal_group(proc, signal.SIGCONT)  # a stopped command only acts on SIGTERM once it runs
    try:
        proc.stdout.close()
    except OSError:
        pass


def _run_rsync_dry_run(req, job, save, target_alive, target_pid, paused=None):
    argv = req.get("argv") or []
    if not is_sizing_helper(argv):
        save(state="failed", error="not a sizing command", finished=time.time())
        return 1
    env = dict(os.environ, LC_ALL="C", **{HELPER_ENV: "1"})
    try:
        proc = subprocess.Popen(argv, cwd=req.get("cwd") or None, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                start_new_session=True)
    except OSError as e:
        save(state="failed", error=str(e), finished=time.time())
        return 1

    append = append_destination(argv, req.get("cwd") or "")
    appended = {"existing": 0}

    def on_line(line):
        if not line.startswith(SIZER_LINE):
            return None
        item, size, name = (line[len(SIZER_LINE):].split("|", 2) + ["", ""])[:3]
        if len(item) >= 2 and item[1] == "f" and item[0] in "<>":
            try:
                n = int(size.strip() or 0)
            except ValueError:
                n = 0
            if append:
                # --append writes only what the destination file lacks; the
                # dry run counts the whole file.
                have = min(n, existing_size(append, name))
                appended["existing"] += have
                n -= have
            job["found_bytes"] += n
            job["found_files"] += 1
            if job["moved_at_cross"] is None:
                job["moved_at_cross"], job["cross_counter"] = _sample_cross(target_pid, appending=bool(append))
                job["cross_at"] = time.time()
        return True

    out, rc, err = _drain(proc, save, target_alive, on_line, target_paused=paused)
    if err:
        save(state=err[0], error=err[1], finished=time.time(), rc=rc)
        return 0
    stats = parse_rsync_stats(out)
    if rc in (0, 23, 24) and "transfer_size" in stats:
        if job["moved_at_cross"] is None:
            job["moved_at_cross"], job["cross_counter"] = _sample_cross(target_pid, appending=bool(append))
            job["cross_at"] = time.time()
        save(state="done", finished=time.time(), rc=rc,
             transfer_size=max(0, stats.get("transfer_size", 0) - appended["existing"]),
             total_file_size=stats.get("total_file_size", 0),
             files_total=stats.get("files_total", 0),
             files_to_transfer=stats.get("files_to_transfer", 0))
    else:
        save(state="failed", error=f"rsync exit {rc}", finished=time.time(), rc=rc)
    return 0


def source_size_job(key, paths, target_pid=0, target_start=0, now=None):
    """The du job sizing `paths`: an answer from the last 15 minutes is reused
    (any transfer over the same folders), otherwise the helper is started."""
    now = time.time() if now is None else now
    paths = [p for p in paths if p and os.path.exists(p)]
    if not runtime_dir() or not paths:
        return None
    job = read_size_job(key)
    if job:
        st = job.get("state")
        # The transfer that asked keeps its answer for as long as it runs; the
        # 15 minutes only limit the reuse by another transfer.
        mine = bool(target_pid) and job.get("target_pid") == target_pid and job.get("target_start") == target_start
        if st == "done" and (mine or now - float(job.get("finished") or 0) <= DU_TTL):
            return job
        if st in ("queued", "scanning") and not _job_stale(job, now):
            return job
        if st == "failed" and (mine or now - float(job.get("finished") or job.get("updated") or 0) <= DU_TTL):
            return job
    return request_size_job(key, "du", "", [], paths, target_pid, target_start, now)


def source_size(key, paths, target_pid=0, target_start=0):
    """Total size in bytes of the sources of a transfer, when they are folders.
    Walking a big tree takes a while, so `du` runs once in the background (the
    helper: nice 19, idle I/O, one scan at a time) and its answer is read on a
    later run. Returns (bytes, scanning): (0, True) while du is still walking."""
    job = source_size_job(key, paths, target_pid, target_start)
    if not job:
        return 0, False
    st = job.get("state")
    if st == "done":
        return int(job.get("found_bytes") or 0), False
    if st in ("queued", "scanning"):
        return 0, True
    return 0, False


def _size_key_of(name):
    key = name[len(SIZE_FILE_PREFIX):]
    if ".tmp" in key:
        key = key[:key.index(".tmp")]
    for suffix in (".req.json", ".json"):
        if key.endswith(suffix):
            key = key[:-len(suffix)]
    return key


def forget_source_sizes(live_keys, now=None, du_ttl=DU_TTL, rsync_ttl=RSYNC_TTL):
    """Drop the size files nobody needs any more: not used by a live transfer
    and older than their TTL (du answers are kept 15 min for reuse). A job whose
    helper still runs is kept; one whose helper died goes after an hour."""
    now = time.time() if now is None else now
    d = runtime_dir()
    if not d:
        return
    try:
        names = os.listdir(d)
    except OSError:
        return
    jobs = {}
    for name in names:
        if not name.startswith(SIZE_FILE_PREFIX):
            continue
        key = _size_key_of(name)
        if key in live_keys:
            continue
        path = os.path.join(d, name)
        if key not in jobs:
            jobs[key] = read_size_job(key) or {}
        job = jobs[key]
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if ".tmp" in name[len(SIZE_FILE_PREFIX):]:
            ts, ttl = mtime, 120
        else:
            ts = float(job.get("finished") or job.get("updated") or mtime)
            ttl = du_ttl if key.startswith("du-") else rsync_ttl
            if job.get("state") in ("queued", "scanning"):
                if _helper_alive(job):
                    continue
                ttl = max(ttl, 3600) if job.get("helper_pid") else ttl
        if now - ts > ttl:
            try:
                os.unlink(path)
            except OSError:
                pass


def rsync_total_kind(parsed):
    """"exact" when the receiver writes every byte the dry run counts. Else an
    estimate: -c was dropped from the dry run (it decides on size and time
    only), --sparse seeks over runs of zeros, and --inplace with the delta
    algorithm skips the blocks that already match. (--append is subtracted
    file by file by the helper.)"""
    flags = parsed["flags"]
    sparse = bool({"-S", "--sparse"} & flags) and not ({"--no-sparse", "--no-S"} & flags)
    if parsed["checksum"] or sparse:
        return "estimate"
    delta = bool({"--no-whole-file", "--no-W"} & flags) or parsed["remote_mode"] != "local"
    inplace = "--inplace" in flags and "--no-inplace" not in flags
    if delta and inplace and not ({"-W", "--whole-file"} & flags):
        return "estimate"
    return "exact"


def rsync_total(item, now=None):
    """(total, kind, source, scan, plan) of an rsync transfer. The total is what
    the dry run says is left to copy plus what this run had already written when
    the dry run caught up with it: exact (an estimate when -c was dropped from
    the dry run). Remote runs, stdin lists and failures fall back to the
    apparent size of the local sources (an upper bound).

    `item` holds: key (pid-start), pid, start, args, cwd, local_sources."""
    now = time.time() if now is None else now
    args, cwd = item["args"], item.get("cwd", "")
    argv, reason = rsync_sizing_argv(args, cwd)
    scan = None
    if argv:
        # One job per instance: two copies running the same command line each
        # get their own answer instead of taking turns overwriting one file.
        key = rsync_job_key(cwd, args, item["pid"], item["start"])
        item.setdefault("size_keys", []).append(key)
        job = read_size_job(key)
        mine = bool(job) and job.get("target_pid") == item["pid"] and job.get("target_start") == item["start"]
        if job and not mine:
            job = None
        if job is None:
            job = request_size_job(key, "rsync", cwd, argv, [], item["pid"], item["start"], now) or {}
        if _job_stale(job, now):
            job = dict(job, state="failed", error="helper died", finished=now)
            path, _ = _size_paths(key)
            try:
                _write_json_atomic(path, job)
            except (OSError, TypeError):
                pass
        st = job.get("state", "failed")
        elapsed = int(max(0, (float(job.get("finished") or 0) or now) - float(job.get("started") or now)))
        scan = {"state": st if st in ("queued", "scanning", "done") else "failed",
                "method": "rsync_dry_run", "found_bytes": int(job.get("found_bytes") or 0),
                "found_files": int(job.get("found_files") or 0), "elapsed_sec": elapsed,
                "reason": "" if st in ("queued", "scanning", "done") else ("timeout" if job.get("error") == "timeout" else "error")}
        if st == "done":
            to_copy = int(job.get("moved_at_cross") or 0) + int(job.get("transfer_size") or 0)
            selection = int(job.get("total_file_size") or 0)
            kind = rsync_total_kind(parse_rsync_args(args))
            plan = {"selection_bytes": selection, "selection_files": int(job.get("files_total") or 0),
                    "to_copy_bytes": to_copy, "to_copy_files": int(job.get("files_to_transfer") or 0),
                    "already_there_bytes": max(0, selection - to_copy)}
            return to_copy, kind, "rsync_dry_run", scan, plan
        if st in ("queued", "scanning"):
            return 0, "", "", scan, None
    else:
        scan = {"state": "skipped", "method": "rsync_dry_run", "found_bytes": 0, "found_files": 0,
                "elapsed_sec": 0, "reason": reason}
    # Fallback: the apparent size of the local sources, at most what is copied.
    local = [p for p in item.get("local_sources") or [] if p and os.path.exists(p)]
    if not local:
        return 0, "", "", scan, None
    dkey = du_size_key("rsync", local)
    item.setdefault("size_keys", []).append(dkey)
    job = source_size_job(dkey, local, item["pid"], item["start"], now)
    if not job:
        return 0, "", "", scan, None
    scan = dict(scan, method="du")
    if job.get("state") == "done":
        # The fallback answered: the scan is done (the reason the dry run did
        # not, "remote" or "error", stays for the curious).
        scan.update(state="done", found_bytes=int(job.get("found_bytes") or 0))
        return int(job.get("found_bytes") or 0), "upper_bound", "du", scan, None
    if job.get("state") in ("queued", "scanning"):
        scan["state"] = job["state"]
    return 0, "", "", scan, None


def du_scan(job):
    if not job:
        return None
    st = job.get("state", "failed")
    now = time.time()
    return {"state": st if st in ("queued", "scanning", "done") else "failed", "method": "du",
            "found_bytes": int(job.get("found_bytes") or 0), "found_files": 0,
            "elapsed_sec": int(max(0, (float(job.get("finished") or 0) or now) - float(job.get("started") or now))),
            "reason": "" if st in ("queued", "scanning", "done") else "error"}


# Programs that run a script file: the script is the "batch" its transfers belong to.
SCRIPT_INTERPRETERS = {"bash", "sh", "dash", "zsh", "fish", "ksh", "python", "python3", "node",
                       "perl", "ruby", "lua", "bun", "deno"}
INTERPRETER_OPTS_WITH_ARG = {"-o", "-O", "--rcfile", "--init-file", "-W", "-X", "-r", "--require"}
# Between a transfer and its script there may be wrappers; they are skipped.
BATCH_PASS_THROUGH = {"nice", "ionice", "sudo", "doas", "env", "time", "timeout", "nohup", "setsid",
                      "stdbuf", "xargs", "parallel", "find", "chrt", "taskset", "systemd-inhibit"}


def script_of(args, cwd=""):
    """The script file an interpreter command line runs ("" when it runs none,
    e.g. `bash -c ...`, an interactive shell or `python -m module`)."""
    if not args:
        return ""
    comm = os.path.basename(args[0]).lstrip("-")
    base = comm.rstrip("0123456789.")
    if base not in SCRIPT_INTERPRETERS:
        return ""
    i = 1
    if base in ("bun", "deno") and len(args) > 1 and args[1] == "run":
        i = 2
    while i < len(args):
        a = args[i]
        if a in ("-c", "-m", "-e", "--eval", "-p", "--print", "-i"):
            return ""
        if a in INTERPRETER_OPTS_WITH_ARG:
            i += 2
            continue
        if a.startswith("-"):
            if base in ("bash", "sh", "dash", "zsh", "ksh") and not a.startswith("--") and "c" in a[1:]:
                return ""  # bundled, e.g. `bash -lc`
            i += 1
            continue
        return a if os.path.isabs(a) or not cwd else os.path.normpath(os.path.join(cwd, a))
    return ""


def read_proc(pid):
    """(ppid, uid, args, cwd, start time in clock ticks) of a process, or None."""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            data = f.read()
        fields = data[data.rindex(")") + 2:].split()
        ppid, start = int(fields[1]), int(fields[19])
        uid = os.stat(f"/proc/{pid}").st_uid
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a for a in f.read().decode("utf-8", "replace").split("\x00") if a]
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = ""
        return ppid, uid, args, cwd, start
    except (OSError, ValueError, IndexError):
        return None


def find_batch(pid, read=read_proc, uid=None):
    """The script a transfer was started by: the nearest ancestor that is an
    interpreter running a script file (`bash backup.sh`), or a `make`/`just`
    run. Shells running `-c` and wrappers such as nice or sudo are looked
    through; anything else (a terminal, an editor, an agent) ends the search,
    and the transfer then belongs to no batch. A `( ... )` or `$( ... )` in a
    script is a forked copy of the script with the same command line: the
    search climbs over those to the real script, so all its steps share one
    batch. Returns a dict or None."""
    uid = CURRENT_UID if uid is None else uid
    info = read(pid)
    seen = set()
    while info:
        ppid = info[0]
        if ppid <= 1 or ppid in seen:
            return None
        seen.add(ppid)
        info = read(ppid)
        if not info or info[1] != uid or not info[2]:
            return None
        args, cwd = info[2], info[3]
        comm = os.path.basename(args[0]).lstrip("-")
        script = script_of(args, cwd)
        if script or comm in ("make", "gmake", "just", "ninja"):
            top_pid, top = ppid, info
            while top[0] > 1 and top[0] not in seen:
                seen.add(top[0])
                parent = read(top[0])
                if not parent or parent[1] != uid or parent[2] != top[2]:
                    break
                top_pid, top = top[0], parent
            args, cwd, start = top[2], top[3], top[4]
            if script:
                name, path = os.path.basename(script), script_of(args, cwd) or script
            else:
                name = " ".join([comm] + [a for a in args[1:] if not a.startswith("-")][:2])
                path = cwd
            return {"pid": top_pid, "key": f"{top_pid}-{start}", "name": name, "path": path,
                    "command": " ".join(args), "cwd": cwd, "start_ticks": start}
        if comm.rstrip("0123456789.") in SCRIPT_INTERPRETERS or comm in BATCH_PASS_THROUGH:
            continue
        return None
    return None


def process_state(pid):
    """The state letter of a process (R, S, D, T, Z), "" when it is gone."""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            data = f.read()
        return data[data.rindex(")") + 2:].split()[0]
    except (OSError, ValueError, IndexError):
        return ""


def process_elapsed(start_ticks, now=None):
    """Seconds a process has been running, from its start time in clock ticks."""
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return max(1, int(uptime - start_ticks / os.sysconf("SC_CLK_TCK")))
    except (OSError, ValueError):
        return 0


def get_filesystem_info(path):
    """Query free and total space for the filesystem containing path."""
    if not path or not os.path.exists(path):
        return None
    try:
        st = os.statvfs(path)
        free = st.f_bavail * st.f_frsize
        total = st.f_blocks * st.f_frsize
        used = total - free
        p = os.path.abspath(path)
        mount_point = p
        while mount_point != "/" and not os.path.ismount(mount_point):
            mount_point = os.path.dirname(mount_point)
        mount_name = os.path.basename(mount_point) or "System"

        return {
            "mount": mount_point,
            "name": mount_name,
            "free_bytes": free,
            "total_bytes": total,
            "used_bytes": used,
            "free_pct": round((free / total * 100), 1) if total > 0 else 0
        }
    except Exception:
        return None

STATE_VERSION = 2
STATE_STALE_AFTER = 15


def _empty_state():
    return {"version": STATE_VERSION, "timestamp": 0, "processes": {}, "recent_completed": [],
            "batches": {}, "stale": False}


def load_previous_state(now=None):
    """The previous run, kept in RAM: speeds are deltas between runs and the
    batches remember the steps that already finished. A state older than 15 s
    still gives its batches and recent list; its processes come back flagged
    stale (no speed deltas, no notifications from them)."""
    now = time.time() if now is None else now
    out = _empty_state()
    try:
        path = runtime_file(STATE_FILE_NAME)
        if not path or not os.path.exists(path):
            return out
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    recent = data.get("recent_completed")
    out["recent_completed"] = recent if isinstance(recent, list) else []
    if data.get("version") != STATE_VERSION:
        return out  # an older collector's processes have none of the fields used here
    out["timestamp"] = float(data.get("timestamp") or 0)
    out["processes"] = data.get("processes") if isinstance(data.get("processes"), dict) else {}
    out["batches"] = data.get("batches") if isinstance(data.get("batches"), dict) else {}
    out["stale"] = now - out["timestamp"] >= STATE_STALE_AFTER
    return out


def save_current_state(processes_dict, recent_completed, batches=None):
    """Save current state to RAM cache."""
    try:
        data = {
            "version": STATE_VERSION,
            "timestamp": time.time(),
            "processes": processes_dict,
            "recent_completed": recent_completed[:6],
            "batches": batches or {},
        }
        path = runtime_file(STATE_FILE_NAME)
        if not path:
            return
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass


def format_eta(seconds):
    """Format seconds into clean human ETA."""
    if seconds <= 0 or seconds > 86400 * 3:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def format_duration(seconds):
    """A running time, never empty and without a cap: "45s", "3m 05s", "2h 07m", "4d 03h"."""
    seconds = max(0, int(seconds or 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d}d {h:02d}h"
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


SAMPLE_KEEP = 64
SAMPLE_MAX_AGE = 300
RATE_WINDOW = 120


def update_samples(samples, now, value):
    """Append (now, value) to a progress history: at most 64 entries and 5
    minutes. Points closer than 2 s replace the newest one, so a fast poll
    still covers the window; a counter that went back starts over."""
    out = [list(s) for s in (samples or []) if isinstance(s, (list, tuple)) and len(s) == 2]
    if out and value < out[-1][1]:
        out = []
    if len(out) >= 2 and out[-1][0] - out[-2][0] < 2.0:
        out[-1] = [now, value]
    else:
        out.append([now, value])
    out = [s for s in out if now - s[0] <= SAMPLE_MAX_AGE]
    return out[-SAMPLE_KEEP:]


def window_rate(samples):
    """(bytes per second, seconds covered) over the last 2 minutes of samples."""
    if not samples or len(samples) < 2:
        return 0.0, 0.0
    t_now, v_now = samples[-1]
    old = None
    for t, v in samples:
        if t_now - t <= RATE_WINDOW:
            old = (t, v)
            break
    if not old or t_now - old[0] <= 0:
        return 0.0, 0.0
    span = t_now - old[0]
    return max(0.0, (v_now - old[1]) / span), span


def compute_overall(moved, total, kind, samples, state, alive=True, eta_cap=3 * 86400, scanning=False):
    """Percent, ETA and why there is no ETA, from what was moved, the total and
    a windowed rate (paused stretches stay in the window, so a duty cycle shows
    honestly). The percent never reaches 100 while the process lives."""
    rate, span = window_rate(samples)
    out = {"pct": 0.0, "beyond": False, "remaining": 0, "rate": int(rate), "eta": "",
           "eta_sec": 0, "eta_reason": ""}
    if not total or total <= 0:
        out["eta_reason"] = "scanning" if scanning else "unknown_total"
        return out
    cap = 99.9 if alive else 100.0
    out["remaining"] = max(0, int(total - moved))
    # Some slack before "more than the total": the file in flight, page and
    # block rounding, metadata. A size worked out from the sources (du, the
    # device) is rougher than one rsync worked out, so it gets more.
    if kind == "exact":
        beyond = moved > total * 1.01 + 16 * MIB
    else:
        beyond = moved > total * 1.02 + 16 * MIB
    if beyond:
        out.update(pct=cap, beyond=True, remaining=0, eta_reason="beyond")
        return out
    out["pct"] = min(cap, max(0.0, round(moved / total * 100, 1)))
    if kind == "upper_bound":
        out["eta_reason"] = "upper_bound"
    elif state == "paused":
        out["eta_reason"] = "paused"
    elif out["remaining"] <= 0:
        pass
    elif span < 20:
        out["eta_reason"] = "measuring"
    elif rate < 1000:
        out["eta_reason"] = "stalled" if span >= 60 else "measuring"
    else:
        eta = int(out["remaining"] / rate)
        out["eta_sec"] = eta
        if eta > eta_cap:
            out["eta_reason"] = "too_long"
        else:
            out["eta"] = format_eta(eta)
    return out


def _fd_targets(pid):
    """(fd, target, pos, flags) of the open files of a process."""
    out = []
    fd_dir = f"/proc/{pid}/fd"
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return out
    for fd in entries:
        try:
            target = os.readlink(f"{fd_dir}/{fd}")
        except OSError:
            continue
        pos = flags = 0
        try:
            with open(f"/proc/{pid}/fdinfo/{fd}") as fh:
                for line in fh:
                    if line.startswith("pos:"):
                        pos = int(line.split()[1])
                    elif line.startswith("flags:"):
                        flags = int(line.split()[1], 8)
        except (OSError, ValueError, IndexError):
            pass
        out.append((fd, target, pos, flags))
    return out


# A checksum tool reads its list through a stdio buffer (4 KiB or more): a list
# this short is at its end after the first reads, and its position is no
# measure of progress. Its entries are sized instead (manifest_total()).
SMALL_LIST_BYTES = 64 * 1024
MANIFEST_MAX_ENTRIES = 5000
MANIFEST_GNU = re.compile(r"^\\?[0-9a-fA-F]{8,} [ *](.+)$")
MANIFEST_BSD = re.compile(r"^\\?[A-Za-z0-9-]+ ?\((.+)\) ?= ?[0-9a-fA-F]{8,}$")


def _manifest_name(line):
    m = MANIFEST_GNU.match(line) or MANIFEST_BSD.match(line)
    if not m:
        return ""
    name = m.group(1)
    if line.startswith("\\"):  # an escaped name: backslash-n is a newline
        name = re.sub(r"\\(.)", lambda e: {"n": "\n", "r": "\r", "\\": "\\"}.get(e.group(1), e.group(1)), name)
    return name


def manifest_total(lists, cwd, max_entries=MANIFEST_MAX_ENTRIES):
    """Bytes of the files a checksum list names (relative names resolved
    against the tool's working directory); missing files count 0. 0 when there
    is nothing to size or the list is too long to size every poll."""
    total = entries = 0
    for path in lists:
        if not path or path == "-":
            return 0
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    name = _manifest_name(line.rstrip("\n"))
                    if not name:
                        continue
                    entries += 1
                    if entries > max_entries:
                        return 0
                    try:
                        st = os.stat(os.path.join(cwd, name) if cwd else name)
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        total += st.st_size
        except OSError:
            return 0
    return total


def verification_progress(pids, info, small_list=SMALL_LIST_BYTES):
    """How far a `sha256sum -c LIST` is: the read position in its list(s)
    against their size. -1 when the list is a pipe (no size), or when it is so
    short that it was read in one go (list_buffered: see manifest_total())."""
    lists = [p for p in info.get("lists") or []]
    sizes = []
    for p in lists:
        try:
            st = os.stat(p) if p != "-" else None
            sizes.append(st.st_size if st and stat.S_ISREG(st.st_mode) else -1)
        except OSError:
            sizes.append(-1)
    pos = -1
    path = ""
    stdin_list = None
    for p in pids:
        for fd, target, fpos, _flags in _fd_targets(p):
            if target in lists:
                idx = lists.index(target)
                if sizes[idx] >= 0:
                    pos = sum(s for s in sizes[:idx] if s > 0) + fpos
                    path = target
                    break
            if fd == "0" and "-" in lists and target.startswith("/") and stdin_list is None:
                try:
                    st = os.stat(target)
                    if stat.S_ISREG(st.st_mode):
                        stdin_list = (target, fpos, st.st_size)
                except OSError:
                    pass
        if pos >= 0:
            break
    size = sum(s for s in sizes) if sizes and all(s >= 0 for s in sizes) else -1
    if pos < 0 and stdin_list:
        path, pos, size = stdin_list
    if not path and lists:
        path = lists[0]
    fraction = round(min(1.0, pos / size), 4) if pos >= 0 and size and size > 0 else -1
    buffered = 0 < size <= small_list
    if buffered:
        fraction = -1
    named = [stdin_list[0] if (p == "-" and stdin_list) else p for p in lists]
    return {"mode": "check" if info.get("check") else "hash", "list_path": path,
            "list_pos": max(0, pos), "list_size": max(0, size), "list_fraction": fraction,
            "list_buffered": buffered, "lists": named}


def active_directory(pids, under=()):
    """The deepest folder a process tree has open (a dry run walks folders
    without opening files), preferring folders under the source or destination."""
    best = ("", 0)
    under = [u.rstrip("/") for u in under if u]
    for p in pids:
        for _fd, target, _pos, _flags in _fd_targets(p):
            if not target.startswith("/") or target.startswith(("/proc", "/dev", "/sys")):
                continue
            if not os.path.isdir(target):
                continue
            pref = 1 if any(target == u or target.startswith(u + "/") for u in under) else 0
            rank = (pref, target.count("/"), len(target))
            if not best[0] or rank > best[1]:
                best = (target, rank)
    return best[0]


def _new_batch_state(batch, now):
    return {"pid": batch["pid"], "start_ticks": batch.get("start_ticks", 0), "name": batch.get("name", ""),
            "path": batch.get("path", ""), "command": batch.get("command", ""), "cwd": batch.get("cwd", ""),
            "done_bytes": 0, "verified_bytes": 0, "verified_hash_bytes": 0, "verified_check_bytes": 0,
            "selection_bytes_done": 0, "last_hash_bytes": 0,
            "steps": [], "steps_done": 0, "first_seen": now, "last_seen": now, "last_state": "running",
            "elapsed_sec": 0, "elapsed_str": ""}


def verified_parts(b):
    """(bytes hashed, bytes checked) by the finished verification steps of a
    batch. They are kept apart because a script often reads the same data
    twice (hash the copy, then `sha256sum -c` the source): the batch's
    verified_bytes is the larger of the two, not their sum."""
    h, c = b.get("verified_hash_bytes"), b.get("verified_check_bytes")
    if h is None and c is None:
        return int(b.get("verified_bytes") or 0), 0  # state from before the split
    return int(h or 0), int(c or 0)


def batch_is_alive(b):
    return process_alive(int(b.get("pid") or 0), int(b.get("start_ticks") or -1))


def finish_vanished(prev_processes, live_keys, batches_state, batch_alive, now, stale):
    """What became of the processes of the previous run that are gone now.
    Returns (recent entries, events, batches): a dry run or a listing is only
    a step of its batch; a transfer or verification is "finished", or
    "stopped" when it was paused or ended clearly short of its exact total (the
    exit code is not known, so nothing is called "completed"). Steps of a batch
    credit it and do not notify (the batch notifies once, when it ends); a
    stale previous state gives no events. Batches whose script is gone become
    one entry and one event."""
    recent, events = [], []
    batches = {k: dict(v, steps=list(v.get("steps") or [])) for k, v in (batches_state or {}).items()}
    for key, p in (prev_processes or {}).items():
        if key in live_keys or not isinstance(p, dict):
            continue
        kind = p.get("kind") or "transfer"
        bkey = p.get("batch_key") or ""
        b = batches.get(bkey) if bkey else None
        moved = int(p.get("total_bytes") or 0)
        total = int(p.get("overall_total_bytes") or 0)
        tkind = p.get("overall_total_kind") or ""
        step = {"tool": p.get("tool", ""), "kind": kind, "verify_mode": p.get("verify_mode", ""),
                "total_bytes": moved, "overall_total_bytes": total, "outcome": "finished",
                "elapsed_str": p.get("elapsed_str", ""), "finished_at": now}
        if kind in ("simulation", "listing"):
            if b is not None:
                b["steps"] = (b["steps"] + [step])[-12:]
                b["steps_done"] = int(b.get("steps_done") or 0) + 1
            continue
        # What is left can only be called "stopped early" when it is more
        # than the run could have done between two polls (the bytes after the
        # last poll are not seen).
        tail = 15 * max(float(p.get("progress_speed") or 0), float(p.get("overall_rate") or 0))
        well_short = total > 0 and moved < 0.9 * total and total - moved > tail
        short = tkind == "exact" and well_short
        frac = p.get("list_fraction", -1)
        if frac is None:
            frac = -1
        short_list = (kind == "verification" and p.get("verify_mode") == "check" and 0 <= frac < 0.98
                      and int(p.get("list_remaining") or 0) > 64 * 1024)
        # Paused at the last poll is no proof on its own: the thermal guard
        # stops and resumes whole scripts, and a step can finish within one
        # poll of being resumed. An estimate that says much was left does.
        paused_short = p.get("state") == "paused" and tkind == "estimate" and well_short
        outcome = "stopped" if (short or short_list or paused_short) else "finished"
        step["outcome"] = outcome
        # A step that finished with an exact total moved all of it; the last
        # poll only saw up to one interval less.
        if outcome == "finished" and tkind == "exact" and kind == "transfer" and total > moved:
            moved = total
        if b is not None:
            if kind == "verification":
                hashed, checked = verified_parts(b)
                if p.get("verify_mode") == "hash":
                    hashed += moved
                    b["last_hash_bytes"] = moved
                else:
                    checked += moved
                b.update(verified_hash_bytes=hashed, verified_check_bytes=checked,
                         verified_bytes=max(hashed, checked))
            else:
                b["done_bytes"] = int(b.get("done_bytes") or 0) + moved
            b["selection_bytes_done"] = int(b.get("selection_bytes_done") or 0) + int(p.get("selection_bytes") or 0)
            b["steps"] = (b["steps"] + [step])[-12:]
            b["steps_done"] = int(b.get("steps_done") or 0) + 1
        if not (moved > 5 * MIB or int(p.get("elapsed_sec") or 0) > 15):
            continue
        recent.append({"tool": p.get("tool", "Transfer"), "kind": kind, "outcome": outcome,
                       "source": p.get("source", ""), "destination": p.get("destination", ""),
                       "total_bytes": moved, "overall_total_bytes": total,
                       "elapsed_sec": int(p.get("elapsed_sec") or 0), "elapsed_str": p.get("elapsed_str", ""),
                       "finished_at": now, "batch": b.get("name", "") if b else "", "name": ""})
        if stale:
            continue
        if kind == "verification":
            etype = "verification_finished"
        else:
            etype = "transfer_stopped" if outcome == "stopped" else "transfer_finished"
        events.append({"id": f"{key}@{now}", "type": etype, "kind": kind, "outcome": outcome,
                       "tool": p.get("tool", ""), "name": b.get("name", "") if b else "",
                       "total_bytes": moved, "overall_total_bytes": total,
                       "verified_bytes": moved if kind == "verification" else 0,
                       "elapsed_str": p.get("elapsed_str", ""),
                       # The batch it belongs to notifies once, when it ends.
                       "notify": b is None})
    for bkey in list(batches):
        b = batches[bkey]
        if batch_alive(b):
            continue
        del batches[bkey]
        if now - float(b.get("last_seen") or 0) > 3600:
            continue  # gone long ago (the collector was not running): forget it quietly
        verified = max(verified_parts(b))
        if not int(b.get("steps_done") or 0) and not int(b.get("done_bytes") or 0) and not verified:
            continue  # no step of it was ever seen to finish: nothing to report
        recent.append({"tool": "batch", "kind": "batch", "outcome": "finished", "name": b.get("name", ""),
                       "source": b.get("path", ""), "destination": "",
                       "total_bytes": int(b.get("done_bytes") or 0), "overall_total_bytes": 0,
                       "verified_bytes": verified,
                       "elapsed_sec": int(b.get("elapsed_sec") or 0), "elapsed_str": b.get("elapsed_str", ""),
                       "finished_at": now, "batch": ""})
        if not stale:
            events.append({"id": f"{bkey}@{now}", "type": "batch_finished", "kind": "batch", "outcome": "finished",
                           "tool": "batch", "name": b.get("name", ""),
                           "total_bytes": int(b.get("done_bytes") or 0), "overall_total_bytes": 0,
                           "verified_bytes": verified,
                           "elapsed_str": b.get("elapsed_str", ""), "notify": True})
    return recent, events, batches


def discovery_skip(comm, args, pid=None):
    """True for processes that are never listed: package-manager queries,
    rclone commands that move nothing, rsync's server side, and this plugin's
    own sizing dry run."""
    if not args:
        return True
    if is_sizing_helper(args):
        return True
    if comm == "rsync":
        if "--server" in args or "--daemon" in args:
            return True
        if pid is not None and ("--dry-run" in args or "-n" in args):
            try:
                with open(f"/proc/{pid}/environ", "rb") as fh:
                    if (HELPER_ENV + "=1").encode() in fh.read().split(b"\x00"):
                        return True
            except OSError:
                pass
    joined = "\x00" + "\x00".join(args) + "\x00"
    if "checkup-db" in joined or "checkupdates" in joined or "\x00-Q\x00" in joined:
        return True
    if comm == "rclone" and rclone_subcommand(args) not in RCLONE_TRANSFER_COMMANDS:
        return True  # mount, config, serve, ls...: not a transfer
    return False


def discover_roots():
    """The root process of every transfer, dry run and verification: one item
    per root (see group_roots())."""
    candidates = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            if os.stat(f"/proc/{pid}").st_uid != CURRENT_UID:
                continue
            comm = read_comm(pid)
            if comm not in DISCOVERY_COMMANDS:
                continue
            args = read_cmdline(pid)
            if discovery_skip(comm, args, pid):
                continue
            ppid = read_ppid(pid)
            candidates[pid] = {"comm": comm, "ppid": ppid, "args": args}
        except Exception:
            pass
    return group_roots(candidates)


def group_roots(candidates, comm_of=None):
    """One root per item from the discovered processes ({pid: {comm, ppid,
    args}}): a candidate under another candidate belongs to it. A checksum tool
    run by xargs/parallel is followed through it (the item is the xargs
    process, whose I/O counters keep what its finished children read). Only
    checksum tools: each rsync/cp/tar run by xargs has its own total, which
    would not match bytes summed over all the children, so they stay one item
    per child."""
    comm_of = comm_of or read_comm
    roots = {}
    for pid, c in sorted(candidates.items()):
        if c["ppid"] in candidates:
            continue
        root = pid
        promoted = False
        if c["comm"] in CHECKSUM_COMMANDS and c["ppid"] > 1 and comm_of(c["ppid"]) in XARGS_COMMANDS:
            root = c["ppid"]
            promoted = True
        if root in roots:
            continue  # several children of one xargs: one item
        roots[root] = {"pid": root, "tool_pid": pid, "comm": c["comm"], "args": c["args"], "promoted": promoted}
    return list(roots.values())


def xargs_between_children(prev_processes, found_pids, alive=None, comm_of=None):
    """xargs items of the previous run whose xargs still runs but has no child
    at this instant (between two invocations): they stay listed, with the
    command line of their last child, instead of finishing and coming back."""
    alive = alive or process_alive
    comm_of = comm_of or read_comm
    out = []
    for p in (prev_processes or {}).values():
        if not isinstance(p, dict) or not p.get("promoted") or not p.get("tool_args"):
            continue
        pid = int(p.get("pid") or 0)
        if pid in found_pids or not alive(pid, int(p.get("start_ticks") or -1)):
            continue
        if comm_of(pid) not in XARGS_COMMANDS:
            continue
        args = list(p["tool_args"])
        if os.path.basename(args[0]) not in CHECKSUM_COMMANDS:
            continue  # only checksum tools are followed through xargs
        out.append({"pid": pid, "tool_pid": pid, "comm": os.path.basename(args[0]), "args": args,
                    "promoted": True})
        found_pids = set(found_pids) | {pid}
    return out


def _category(tool_name, kind):
    if kind in ("simulation", "listing"):
        return "check"
    if kind == "verification":
        return "verify"
    if tool_name in ("curl", "wget", "aria2c", "yt-dlp"):
        return "download"
    if tool_name in ("cp", "mv"):
        return "copy"
    if tool_name == "dd":
        return "disk_image"
    if "git" in tool_name:
        return "git"
    if tool_name in ("pacman", "yay", "paru"):
        return "system"
    if "rclone" in tool_name or tool_name in ("scp", "sftp"):
        return "cloud"
    return "sync"


ESTIMATE_KIND_COMPAT = {"device": "device", "du": "source", "batch": "source", "mount": "source",
                        "rsync_dry_run": "source", "file": ""}


def collect_all_transfers():
    """Main discovery and metric collection loop."""
    now = time.time()
    state_data = load_previous_state(now)
    stale = state_data["stale"]
    prev_processes = state_data["processes"]
    recent_completed = state_data["recent_completed"]
    batches_state = state_data["batches"]
    new_processes = {}

    roots = discover_roots()
    roots += xargs_between_children(prev_processes, {r["pid"] for r in roots})
    for r in roots:
        r["start"] = read_start_ticks(r["pid"])
        r["key"] = f"{r['pid']}-{r['start']}"
    live_keys = {r["key"] for r in roots}

    recent_new, events, batches_state = finish_vanished(
        prev_processes, live_keys, batches_state, batch_is_alive, now, stale)
    recent_completed = recent_new[::-1] + recent_completed

    transfers = []
    live_size_keys = set()
    total_agg_read_speed = 0.0
    total_agg_write_speed = 0.0

    for r in roots:
        try:
            item, record = _collect_item(r, prev_processes.get(r["key"]), stale, batches_state, now, live_size_keys)
        except Exception:
            continue
        if not item:
            continue
        transfers.append(item)
        new_processes[r["key"]] = record
        total_agg_read_speed += item["read_speed"]
        total_agg_write_speed += item["write_speed"]

    # Batches: every live script with a step now or one in the state.
    step_pids = set()
    for t in transfers:
        step_pids.update(t["tree_pids"])
    live_batches = {}
    for bkey, b in batches_state.items():
        if not batch_is_alive(b):
            continue
        st = "paused" if process_state(b["pid"]) == "T" else "running"
        el = process_elapsed(b.get("start_ticks", 0))
        b.update(last_seen=now, last_state=st, elapsed_sec=el, elapsed_str=format_duration(el))
        script_comm = read_comm(b["pid"])
        cmds = []
        for p in get_process_tree(b["pid"])[1:]:
            if p in step_pids:
                continue
            c = read_comm(p)
            if c and c not in ("sleep", script_comm) and c not in cmds:
                cmds.append(c)
        live_batches[bkey] = dict(b, key=bkey, state=st, current_commands=cmds[:4])

    save_current_state(new_processes, recent_completed, batches_state)
    forget_source_sizes(live_size_keys, now)

    # Transfers of one script stay together, in the order they started.
    transfers.sort(key=lambda t: (t["batch"]["pid"] if t["batch"] else t["pid"], t.get("started_at", 0), t["pid"]))
    summary = summarize(transfers, live_batches)

    return {
        "timestamp": now,
        "home": os.path.expanduser("~"),
        "agg_total_bytes": summary["total_bytes"],
        "agg_overall_total_bytes": summary["overall_total_bytes"],
        "agg_overall_pct": summary["overall_pct"],
        "agg_overall_is_estimate": summary["overall_is_estimate"],
        "agg_overall_total_kind": summary["overall_total_kind"],
        "batches": summary["batches"],
        "count": len(transfers),
        "transfer_count": summary["transfer_count"],
        "check_count": summary["check_count"],
        "agg_read_speed": round(total_agg_read_speed),
        "agg_write_speed": round(total_agg_write_speed),
        "agg_speed": summary["speed"],
        "transfers": transfers,
        "recent_completed": recent_completed[:5],
        "events": events,
    }


def _collect_item(r, prev_any, stale, batches_state, now, live_size_keys):
    """One listed item and the state record kept for it, or (None, None)."""
    root_pid, tool_pid, args = r["pid"], r["tool_pid"], r["args"]
    start = r["start"]
    if start < 0:
        return None, None
    cwd = read_cwd(tool_pid) or read_cwd(root_pid)
    comm = os.path.basename(args[0])
    kind, verify_mode = classify_kind(comm, args)
    parsed = parse_command(args, cwd)
    if kind == "verification":
        # A checksum tool reads files where it runs; its operands are lists or
        # the files being hashed, never a source and a destination.
        parsed = {"tool": comm, "source": cwd, "destination": "", "sources": [cwd] if cwd else [],
                  "all_sources": [cwd] if cwd else []}
    tool_name, src_path, dst_path = parsed["tool"], parsed["source"], parsed["destination"]
    if tool_name == "tar" and not src_path and not dst_path:
        return None, None  # tar in a pipe with nothing to show (e.g. `tar -t`)
    tree_pids = get_process_tree(root_pid)
    rb, wb, rss, state = get_tree_io_and_stat(tree_pids)
    tio = tree_proc_io(tree_pids)
    elapsed_sec = process_elapsed(start)
    if (kind == "verification" and verify_mode == "hash" and not r["promoted"]
            and elapsed_sec < 5 and tio["rchar"] < 256 * MIB):
        return None, None  # a quick hash, not worth a row

    prev = None if stale else prev_any
    dt = max(0.2, now - prev["timestamp"]) if prev and "timestamp" in prev else 0.0

    # What this run has moved, measured where it is true for the tool.
    direction = ""
    net_bytes = 0
    verify = None
    rsync_parsed = parse_rsync_args(args) if comm == "rsync" else None
    if kind in ("simulation", "listing"):
        moved, counter = 0, "none"
    elif kind == "verification":
        moved, counter = tio["rchar"], "hashed"
        ca = checksum_args(comm, args, cwd)
        verify = verification_progress(tree_pids, ca) if ca["check"] else {
            "mode": "hash", "list_path": "", "list_pos": 0, "list_size": 0, "list_fraction": -1}
        if ca["check"]:
            moved = max(0, moved - verify["list_pos"])  # reading the list is not checking files
        verify["hashed_bytes"] = moved
    elif rsync_parsed is not None:
        mode = rsync_parsed["remote_mode"]
        if mode == "push":
            # What the sender read; its ssh child carries the same bytes again.
            direction = "upload"
            moved, net_bytes = network_counters("upload", tree_pids)
            counter = "sender_read"
        else:
            moved, counter, _rcv = rsync_progress_counter(root_pid, tree_pids, rsync_parsed)
            if mode == "pull":
                direction = "download"
                net_moved, net_bytes = network_counters("download", tree_pids)
                if counter != "receiver_write":
                    moved, counter = net_moved, "network"
    else:
        direction = transfer_direction(tool_name, src_path, dst_path)
        moved, counter = max(rb, wb), "disk"
        if direction:
            moved, net_bytes = network_counters(direction, tree_pids)
            counter = "network"
        else:
            lmoved, lcounter = local_progress(tool_name.split()[0] if tool_name else "", args, cwd,
                                              tree_pids, root_pid)
            if lcounter:
                if lmoved is None:  # between two reads of the archive: keep the last value
                    same = prev_any and prev_any.get("progress_counter") == lcounter
                    lmoved = int(prev_any.get("total_bytes") or 0) if same else 0
                moved, counter = lmoved, lcounter

    read_speed = write_speed = through_speed = progress_speed = 0.0
    if prev and dt:
        read_speed = 0.7 * max(0.0, (rb - prev.get("rb", rb)) / dt) + 0.3 * prev.get("read_speed", 0.0)
        write_speed = 0.7 * max(0.0, (wb - prev.get("wb", wb)) / dt) + 0.3 * prev.get("write_speed", 0.0)
        if prev.get("progress_counter") == counter:
            delta = max(0.0, (moved - prev.get("total_bytes", moved)) / dt)
            progress_speed = 0.7 * delta + 0.3 * prev.get("progress_speed", 0.0)
        if direction and "net_bytes" in prev:
            through_speed = max(0.0, (net_bytes - prev["net_bytes"]) / dt)
            through_speed = 0.7 * through_speed + 0.3 * prev.get("through_speed", 0.0)
    if state == "paused":
        read_speed = write_speed = active_speed = 0.0
    elif kind in ("simulation", "listing"):
        active_speed = 0.0
    else:
        # To or from another machine, what goes through the socket is the speed.
        active_speed = through_speed if through_speed > 1024 else progress_speed

    if tool_name in ("curl", "wget"):
        has_dest_file = bool(dst_path and dst_path != cwd)
        # A brief API query to stdout with no file is not listed. Decided before
        # the batch is looked up, so it does not leave an empty batch behind.
        if not has_dest_file and moved < 256 * 1024 and elapsed_sec < 4 and active_speed < 1024:
            return None, None

    # The files the item is busy with.
    all_sources = parsed.get("all_sources") or ([src_path] if src_path else [])
    if kind == "verification":
        excl = set(p for p in ca["lists"] if p != "-")
        active_file = get_active_file_info(tree_pids, cwd, "", exclude_paths=excl, skip_writing=True)
    elif kind in ("simulation", "listing"):
        active_file = None
    else:
        active_file = get_active_file_info(tree_pids, all_sources, dst_path)
    active_dir = ""
    if not active_file:
        active_dir = active_directory(tree_pids, tuple(all_sources) + ((dst_path,) if dst_path else ()))

    file_eta_str = ""
    if active_file and active_speed > 1024 and not active_file["writing"]:
        rem_bytes = max(0, active_file["size"] - active_file["pos"])
        if rem_bytes > 0:
            file_eta_str = format_eta(rem_bytes / active_speed)

    dest_fs = None
    if dst_path and kind != "verification":
        probe_dir = dst_path if os.path.isdir(dst_path) else os.path.dirname(dst_path)
        dest_fs = get_filesystem_info(probe_dir)
    source_fs = None
    if src_path and is_local_path(src_path, tool_name):
        probe_src = src_path if os.path.isdir(src_path) else os.path.dirname(src_path)
        source_fs = get_filesystem_info(probe_src)

    # The batch (script) it belongs to.
    batch = find_batch(root_pid)
    bstate = None
    if batch:
        # Only kept in the state once the item is (see the end).
        bstate = batches_state.get(batch["key"]) or _new_batch_state(batch, now)

    # Overall total.
    total, tkind, tsource, scan, plan = 0, "", "", None, None
    base_tool = tool_name.split()[0]
    local_sources = parsed["sources"]
    progress_basis = "bytes"
    if kind in ("simulation", "listing"):
        progress_basis = "none"
    elif kind == "verification":
        if verify_mode == "hash" and bstate and _batch_selection(bstate) > 0:
            total, tkind, tsource = _batch_selection(bstate), "estimate", "batch"
        elif verify_mode == "check" and bstate and int(bstate.get("last_hash_bytes") or 0) > 0:
            total, tkind, tsource = int(bstate["last_hash_bytes"]), "estimate", "batch"
        elif verify_mode == "check" and verify and verify.get("list_buffered"):
            # A short list is read in one go: its position says nothing. The
            # sizes of the files it names are the total instead.
            total = manifest_total(verify["lists"], cwd)
            if total:
                tkind, tsource = "exact", "file"
        elif verify_mode == "check" and verify and verify["list_fraction"] >= 0:
            progress_basis = "list"
    elif sticky_total(prev_any, kind, base_tool):
        # A size worked out from the sources is kept for the whole run: mv
        # removes its sources as it goes, and du is not run again.
        st = prev_any["sticky_total"]
        total, tkind, tsource = int(st["total"]), st["kind"], st["source"]
        scan = st.get("scan")
    elif base_tool == "rsync":
        item = {"pid": root_pid, "start": start, "args": args, "cwd": cwd, "local_sources": local_sources}
        total, tkind, tsource, scan, plan = rsync_total(item, now)
        live_size_keys.update(item.get("size_keys", []))
    elif tool_name == "idevicebackup2 backup":
        # The backup size is not known up front: use the space used on the
        # device, worked out once per backup and kept in the state file.
        if prev_any and prev_any.get("estimated_total_bytes"):
            total = prev_any["estimated_total_bytes"]
        else:
            _sub, udid, _dst = parse_idevicebackup2_args(args, cwd)
            total = ios_used_bytes(udid)
        if total:
            tkind, tsource = "estimate", "device"
    elif len(local_sources) == 1 and os.path.isfile(local_sources[0]):
        try:
            total, tkind, tsource = os.path.getsize(local_sources[0]), "exact", "file"
        except OSError:
            pass
    elif (len(local_sources) == 1 and source_fs and os.path.isdir(local_sources[0])
          and os.path.abspath(local_sources[0]).rstrip("/") == source_fs.get("mount", "").rstrip("/")
          and base_tool not in ("rclone",)):
        total, tkind, tsource = source_fs.get("used_bytes", 0), "estimate", "mount"
    if (not total and kind == "transfer" and base_tool in SIZED_TOOLS and base_tool != "rsync"
            and any(os.path.isdir(p) for p in local_sources)):
        # Folders: their size is worked out once, in the background.
        dkey = du_size_key(base_tool, [p for p in local_sources if os.path.exists(p)])
        live_size_keys.add(dkey)
        job = source_size_job(dkey, local_sources, root_pid, start, now)
        scan = du_scan(job)
        if job and job.get("state") == "done":
            total = int(job.get("found_bytes") or 0)
            tkind = "upper_bound" if base_tool == "rclone" else "estimate"
            tsource = "du"
    if not total:
        tkind, tsource = "", ""

    # Progress, ETA.
    if progress_basis == "list":
        value, ototal, okind = verify["list_pos"], verify["list_size"], "estimate"
    else:
        value, ototal, okind = moved, total, tkind
    samples = []
    if prev_any and prev_any.get("progress_basis") == progress_basis and prev_any.get("progress_counter") == counter:
        samples = prev_any.get("samples") or []
    samples = update_samples(samples, now, value) if progress_basis != "none" else []
    scanning = bool(scan and scan.get("state") in ("queued", "scanning"))
    ov = compute_overall(value, ototal, okind, samples, state, alive=True, scanning=scanning)
    if progress_basis == "list":
        ov["pct"] = min(99.9, round(max(0.0, verify["list_fraction"]) * 100, 1))
    if progress_basis == "none":
        ov.update(pct=0.0, eta="", eta_sec=0, eta_reason="")

    batch_out = None
    if batch:
        batch_out = {"pid": batch["pid"], "key": batch["key"], "name": batch["name"], "path": batch["path"],
                     "command": batch["command"], "cwd": batch["cwd"],
                     "state": "paused" if process_state(batch["pid"]) == "T" else "running",
                     "elapsed_sec": process_elapsed(batch["start_ticks"]), "total_bytes": 0}
        batch_out["elapsed_str"] = format_duration(batch_out["elapsed_sec"])

    sources_out = all_sources[:32]
    item = {
        "pid": root_pid,
        "key": r["key"],
        "tree_pids": tree_pids,
        "tool": tool_name,
        "kind": kind,
        "verify_mode": verify_mode,
        "category": _category(tool_name, kind),
        "state": state,
        "command": " ".join(read_cmdline(root_pid) or args),
        "source": src_path,
        "sources": sources_out,
        "source_count": len(all_sources),
        "destination": dst_path,
        "cwd": cwd,
        "read_bytes": rb,
        "write_bytes": wb,
        "total_bytes": moved,
        "progress_counter": counter,
        "progress_basis": progress_basis,
        "direction": direction,
        "read_speed": round(read_speed),
        "write_speed": round(write_speed),
        "active_speed": round(active_speed),
        "rss_bytes": rss,
        "started_at": round(now - elapsed_sec, 1),
        "elapsed_sec": elapsed_sec,
        "elapsed_str": format_duration(elapsed_sec),
        "active_file": active_file,
        "active_dir": active_dir,
        "file_eta": file_eta_str,
        "dest_fs": dest_fs,
        "source_fs": source_fs,
        "overall_total_bytes": total,
        "overall_total_kind": tkind,
        "overall_total_source": tsource,
        "overall_is_estimate": bool(total) and tkind != "exact",
        "overall_estimate_kind": ESTIMATE_KIND_COMPAT.get(tsource, "source") if total and tkind != "exact" else "",
        "overall_pct": ov["pct"],
        "overall_beyond": ov["beyond"],
        "overall_remaining_bytes": ov["remaining"],
        "overall_rate": ov["rate"],
        "overall_eta": ov["eta"],
        "overall_eta_sec": ov["eta_sec"],
        "overall_eta_reason": ov["eta_reason"],
        "overall_scanning": scanning,
        "scan": scan,
        "rsync_plan": plan,
        "verify": verify,
        "batch": batch_out,
    }
    record = {
        "pid": root_pid, "start_ticks": start, "timestamp": now, "rb": rb, "wb": wb,
        "promoted": bool(r["promoted"]), "tool_args": args[:16] if r["promoted"] else [],
        "read_speed": read_speed, "write_speed": write_speed, "through_speed": through_speed,
        "progress_speed": progress_speed, "net_bytes": net_bytes, "total_bytes": moved,
        "progress_counter": counter, "progress_basis": progress_basis, "samples": samples,
        "tool": tool_name, "kind": kind, "verify_mode": verify_mode, "source": src_path,
        "destination": dst_path, "elapsed_sec": elapsed_sec, "elapsed_str": format_duration(elapsed_sec),
        "overall_total_bytes": total, "overall_total_kind": tkind,
        "list_fraction": verify["list_fraction"] if verify else -1,
        "list_remaining": max(0, verify["list_size"] - verify["list_pos"]) if verify and verify["list_fraction"] >= 0 else 0,
        "state": state,
        "batch_key": batch["key"] if batch else "",
        "selection_bytes": int(plan["selection_bytes"]) if plan else 0,
        "estimated_total_bytes": total if tsource == "device" else 0,
        "sticky_total": ({"total": total, "kind": tkind, "source": tsource, "scan": scan}
                         if total and tsource == "du" and kind == "transfer" else None),
        "overall_rate": ov["rate"],
    }
    if batch and batch["key"] not in batches_state:
        batches_state[batch["key"]] = bstate
    return item, record


def sticky_total(prev, kind, base_tool):
    """True when the previous record of this instance holds a total worked
    out with du, to be used again instead of sizing the sources again."""
    st = (prev or {}).get("sticky_total")
    return (kind == "transfer" and base_tool in SIZED_TOOLS and isinstance(st, dict)
            and int(st.get("total") or 0) > 0 and st.get("source") == "du")


def _batch_selection(b):
    return int(b.get("selection_bytes_done") or 0)


def summarize(transfers, batch_state=None):
    """Totals over the live items and one entry per batch (script). Only
    transfers count in the byte totals (a dry run moves nothing, a verification
    only reads). The combined progress only exists when every transfer knows an
    exact or estimated total; an upper bound or an unknown total gives none.
    `batch_state` maps batch keys to their state (finished steps); a live batch
    between steps is listed too."""
    batch_state = batch_state or {}
    kinds = [(t, t.get("kind") or "transfer") for t in transfers]
    moving = [t for t, k in kinds if k == "transfer"]
    total = sum(t.get("total_bytes", 0) for t in moving)

    def total_kind(t):
        if t.get("overall_total_bytes", 0) <= 0:
            return ""
        k = t.get("overall_total_kind")
        if k is None:
            return "estimate" if t.get("overall_is_estimate") else "exact"
        return k

    overall = pct = 0
    agg_kind = ""
    if moving and all(total_kind(t) in ("exact", "estimate") for t in moving):
        overall = sum(t["overall_total_bytes"] for t in moving)
        done = sum(min(t.get("total_bytes", 0), t["overall_total_bytes"]) for t in moving)
        pct = min(99.9, round(done / overall * 100, 1)) if overall > 0 else 0
        agg_kind = "estimate" if any(total_kind(t) == "estimate" for t in moving) else "exact"

    batches = {}
    order = []

    def entry_for(bkey, meta):
        if bkey not in batches:
            st = batch_state.get(bkey) or {}
            e = {k: meta.get(k, st.get(k)) for k in ("pid", "name", "path", "command", "cwd")}
            e.update(key=bkey, state=meta.get("state") or st.get("state") or st.get("last_state") or "running",
                     elapsed_sec=meta.get("elapsed_sec", st.get("elapsed_sec", 0)),
                     elapsed_str=meta.get("elapsed_str") or st.get("elapsed_str", ""),
                     transfers=0, active_speed=0,
                     done_bytes=int(st.get("done_bytes") or 0),
                     verified_hash_bytes=verified_parts(st)[0],
                     verified_check_bytes=verified_parts(st)[1],
                     selection_bytes=int(st.get("selection_bytes_done") or 0),
                     steps_done=int(st.get("steps_done") or 0),
                     steps=list(st.get("steps") or [])[-12:],
                     current_commands=list(st.get("current_commands") or [])[:4],
                     _kinds=set())
            e["total_bytes"] = e["done_bytes"]
            batches[bkey] = e
            order.append(bkey)
        return batches[bkey]

    for t, k in kinds:
        b = t.get("batch")
        if not b:
            continue
        bkey = b.get("key") or str(b.get("pid"))
        e = entry_for(bkey, b)
        e["transfers"] += 1
        e["active_speed"] += t.get("active_speed", 0)
        e["_kinds"].add(k)
        if k == "transfer":
            e["total_bytes"] += t.get("total_bytes", 0)
        elif k == "verification":
            part = "verified_hash_bytes" if t.get("verify_mode") == "hash" else "verified_check_bytes"
            e[part] += t.get("total_bytes", 0)
        plan = t.get("rsync_plan") or {}
        e["selection_bytes"] += int(plan.get("selection_bytes") or 0)
    for bkey, st in batch_state.items():
        entry_for(bkey, st)
    out = []
    for bkey in order:
        e = batches[bkey]
        ks = e.pop("_kinds")
        e["verified_bytes"] = max(e["verified_hash_bytes"], e["verified_check_bytes"])
        if "transfer" in ks:
            e["phase"] = "transfer"
        elif "verification" in ks:
            e["phase"] = "verification"
        elif ks & {"simulation", "listing"}:
            e["phase"] = "simulation"
        else:
            e["phase"] = "other"
        out.append(e)
    by_key = {e["key"]: e for e in out}
    for t in transfers:
        b = t.get("batch")
        if b:
            e = by_key.get(b.get("key") or str(b.get("pid")))
            if e:
                b["total_bytes"] = e["total_bytes"]
    return {
        "total_bytes": total,
        "overall_total_bytes": overall,
        "overall_pct": pct,
        "overall_is_estimate": agg_kind == "estimate",
        "overall_total_kind": agg_kind,
        "transfer_count": len(moving),
        "check_count": sum(1 for _t, k in kinds if k in ("simulation", "listing", "verification")),
        "speed": round(sum(t.get("active_speed", 0) for t, k in kinds if k in ("transfer", "verification"))),
        "batches": out,
    }


COLLECT_LOCK_NAME = "gusthx-resources-collect.lock"


def collector_lock(timeout=10.0):
    """Run one collector at a time (each bar/screen polls on its own): the
    second one waits for the first, then reads the state it saved, so a size
    job is requested once and a finished transfer gives one event. After
    `timeout` seconds it goes on without the lock. Returns the fd to close."""
    d = runtime_dir()
    if not d:
        return None
    try:
        fd = os.open(os.path.join(d, COLLECT_LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.time() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.05)
        except OSError:
            os.close(fd)
            return None


def handle_action(action, target):
    """Execute interactive actions: pause, resume, cancel, open, copy."""
    try:
        if action == "pause":
            pid = int(target)
            tree = get_process_tree(pid)
            for p in tree:
                try:
                    os.kill(p, signal.SIGSTOP)
                except Exception:
                    pass
            print(json.dumps({"success": True, "action": "pause", "pid": pid}))
            return

        elif action == "resume":
            pid = int(target)
            tree = get_process_tree(pid)
            for p in tree:
                try:
                    os.kill(p, signal.SIGCONT)
                except Exception:
                    pass
            print(json.dumps({"success": True, "action": "resume", "pid": pid}))
            return

        elif action == "cancel":
            pid = int(target)
            tree = get_process_tree(pid)
            for p in tree:
                try:
                    os.kill(p, signal.SIGTERM)
                except Exception:
                    pass
            print(json.dumps({"success": True, "action": "cancel", "pid": pid}))
            return

        elif action == "open":
            path = target
            if not os.path.exists(path):
                path = os.path.dirname(path)
            if os.path.isfile(path):
                path = os.path.dirname(path)
            if os.path.isdir(path):
                subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(json.dumps({"success": True, "action": "open", "path": path}))
            else:
                print(json.dumps({"success": False, "error": "Path does not exist"}))
            return

        elif action == "copy":
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            p.communicate(input=target.encode("utf-8"))
            print(json.dumps({"success": True, "action": "copy"}))
            return

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))

def main():
    if len(sys.argv) >= 3:
        flag = sys.argv[1]
        arg = sys.argv[2]
        if flag == "--size-job":
            sys.exit(run_size_job(arg) or 0)
        elif flag == "--pause":
            handle_action("pause", arg)
            return
        elif flag == "--resume":
            handle_action("resume", arg)
            return
        elif flag == "--cancel":
            handle_action("cancel", arg)
            return
        elif flag == "--open":
            handle_action("open", arg)
            return
        elif flag == "--copy":
            handle_action("copy", arg)
            return

    lock = collector_lock()
    try:
        res = collect_all_transfers()
    finally:
        if lock is not None:
            os.close(lock)
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
