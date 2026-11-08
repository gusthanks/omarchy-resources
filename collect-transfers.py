#!/usr/bin/env python3
"""
Ultra-fast, robust background transfer collector for Omarchy.
Discovers active file transfers, local syncs, downloads, and disk operations.
Reports live metrics: current file, bytes/progress, instantaneous speed, and system impact.
"""

import os
import sys
import time
import json
import stat
import signal
import subprocess

STATE_CACHE_FILE = "/dev/shm/omarchy-transfers-state.json"
TRANSFER_COMMANDS = {
    "rsync", "rclone", "cp", "mv", "dd", "pv", "curl", "wget", "aria2c", "yt-dlp",
    "git", "restic", "borg", "btrfs", "pacman", "yay", "paru", "tar", "zstd", "ffmpeg"
}

CURRENT_UID = os.getuid()

def format_bytes_py(b):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.1f} {u}"
        b /= 1024.0
    return f"{b:.1f} PB"

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

def get_tree_io_and_stat(pids):
    """Aggregate read_bytes, write_bytes, and CPU/RSS across a process tree."""
    total_rb = 0
    total_wb = 0
    total_rss_pages = 0
    utime_total = 0
    stime_total = 0
    state_set = set()

    for p in pids:
        # Read IO
        try:
            with open(f"/proc/{p}/io", "r") as f:
                for line in f:
                    if line.startswith("read_bytes:"):
                        total_rb += int(line.split()[1])
                    elif line.startswith("write_bytes:"):
                        total_wb += int(line.split()[1])
        except Exception:
            pass

        # Read Stat
        try:
            with open(f"/proc/{p}/stat", "r") as f:
                parts = f.read().split()
                # parts[2] is state: R, S, D, T, Z
                state_set.add(parts[2])
                utime_total += int(parts[13])
                stime_total += int(parts[14])
                total_rss_pages += int(parts[23])
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

def get_active_file_info(pids, source_hint="", dest_hint=""):
    """
    Inspect open file descriptors across all processes in the tree.
    Finds the regular file currently being read or written.
    """
    candidates = []

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

                try:
                    st = os.stat(target)
                except Exception:
                    continue

                if not stat.S_ISREG(st.st_mode):
                    continue

                # Read current file offset from fdinfo
                pos = 0
                fdi_path = f"/proc/{p}/fdinfo/{fd}"
                if os.path.exists(fdi_path):
                    with open(fdi_path, "r") as fdi:
                        for l in fdi:
                            if l.startswith("pos:"):
                                pos = int(l.split()[1])
                                break

                # Calculate hints
                is_src_or_dst = False
                if source_hint and target.startswith(source_hint):
                    is_src_or_dst = True
                if dest_hint and target.startswith(dest_hint):
                    is_src_or_dst = True

                raw_name = os.path.basename(target)
                pct = round((pos / st.st_size * 100), 1) if st.st_size > 0 else 0.0
                pct = max(0.0, min(100.0, pct))

                candidates.append({
                    "pid": p,
                    "fd": fd,
                    "path": target,
                    "name": raw_name,
                    "dir": os.path.dirname(target),
                    "size": st.st_size,
                    "pos": pos,
                    "is_hinted": is_src_or_dst,
                    "pct": pct
                })
            except Exception:
                pass

    if not candidates:
        return None

    # Filter out 0-byte or tiny log files if larger files exist
    significant = [c for c in candidates if c["size"] > 1024 * 10] or candidates

    def file_rank(c):
        # 1. Matches source or dest hints
        is_hinted = 1 if c["is_hinted"] else 0
        # 2. Currently actively moving (0 < pos < size)
        is_in_flight = 1 if (0 < c["pos"] < c["size"]) else 0
        # 3. Clean real filename rather than rsync temp file (.filename.suffix)
        is_clean = 1 if not c["name"].startswith(".") else 0
        # 4. pos > 0
        has_pos = 1 if c["pos"] > 0 else 0
        # 5. size
        return (is_hinted, is_in_flight, is_clean, has_pos, c["size"])

    significant.sort(key=file_rank, reverse=True)
    best = significant[0]

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

def parse_command_details(args, cwd):
    """
    Parse command arguments to extract user-friendly tool name, source, and destination.
    """
    if not args:
        return "transfer", "", ""
    comm = os.path.basename(args[0])

    if comm == "rsync":
        opts_with_arg = {
            "-e", "--rsh", "--exclude", "--exclude-from", "--include",
            "--include-from", "--files-from", "--log-file", "--timeout",
            "--contimeout", "--bwlimit", "--max-size", "--min-size",
            "--chown", "--chmod", "--filter", "-f", "--info"
        }
        pos_args = []
        i = 1
        while i < len(args):
            arg = args[i]
            if any(arg.startswith(opt + "=") for opt in opts_with_arg):
                i += 1
                continue
            if arg in opts_with_arg and i + 1 < len(args):
                i += 2
                continue
            if arg.startswith("-"):
                i += 1
                continue
            pos_args.append(arg)
            i += 1
        if len(pos_args) >= 2:
            return "rsync", pos_args[-2], pos_args[-1]
        elif len(pos_args) == 1:
            return "rsync", pos_args[0], ""
        return "rsync", "", ""

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
                dst = a[4:]
        return "dd", src, dst

    elif comm == "rclone":
        sub = "copy"
        pos_args = []
        for a in args[1:]:
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

    elif comm in ("pacman", "yay", "paru"):
        pkg_targets = [a for a in args[1:] if not a.startswith("-")]
        pkg_desc = ", ".join(pkg_targets[:3]) if pkg_targets else "Packages"
        if len(pkg_targets) > 3:
            pkg_desc += f" (+{len(pkg_targets)-3})"
        return comm, f"Arch Repos ({pkg_desc})", "/var/cache/pacman/pkg"

    return comm, (args[1] if len(args) > 1 else ""), (args[-1] if len(args) > 2 else "")

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

def load_previous_state():
    """Load previously cached metrics from RAM for speed calculations and completion tracking."""
    try:
        if os.path.exists(STATE_CACHE_FILE):
            with open(STATE_CACHE_FILE, "r") as f:
                data = json.load(f)
                if time.time() - data.get("timestamp", 0) < 15:
                    return data
    except Exception:
        pass
    return {"timestamp": 0, "processes": {}, "recent_completed": []}

def save_current_state(processes_dict, recent_completed):
    """Save current state to RAM cache."""
    try:
        data = {
            "timestamp": time.time(),
            "processes": processes_dict,
            "recent_completed": recent_completed[:6]
        }
        with open(STATE_CACHE_FILE, "w") as f:
            json.dump(data, f)
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

def collect_all_transfers():
    """Main discovery and metric collection loop."""
    now = time.time()
    state_data = load_previous_state()
    prev_processes = state_data.get("processes", {})
    recent_completed = state_data.get("recent_completed", [])
    new_processes = {}

    candidates = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            proc_stat = os.stat(f"/proc/{pid}")
            if proc_stat.st_uid != CURRENT_UID:
                continue

            with open(f"/proc/{pid}/comm", "r") as f:
                comm = f.read().strip()

            if comm in TRANSFER_COMMANDS:
                # Filter out update checkups and daemon mounts
                with open(f"/proc/{pid}/cmdline", "rb") as cf:
                    cmd_raw = cf.read().decode("utf-8", "replace")
                    if "checkup-db" in cmd_raw or "checkupdates" in cmd_raw or " -Q" in cmd_raw:
                        continue
                    if comm == "rclone" and any(sub in cmd_raw for sub in ("mount", "config", "version", "rc", "serve")):
                        continue

                with open(f"/proc/{pid}/stat", "r") as f:
                    parts = f.read().split()
                    ppid = int(parts[3])
                candidates[pid] = {
                    "comm": comm,
                    "ppid": ppid
                }
        except Exception:
            pass

    # Keep only root transfer processes
    root_pids = [pid for pid, info in candidates.items() if info["ppid"] not in candidates]

    transfers = []
    total_agg_read_speed = 0.0
    total_agg_write_speed = 0.0
    active_pid_set = set(str(p) for p in root_pids)

    # Detect finished transfers
    for old_pid_str, old_p in prev_processes.items():
        if old_pid_str not in active_pid_set:
            # Transfer completed!
            tb = old_p.get("total_bytes", 0)
            el = old_p.get("elapsed_sec", 0)
            if tb > 5 * 1024 * 1024 or el > 15:
                completed_item = {
                    "tool": old_p.get("tool", "Transfer"),
                    "source": old_p.get("source", ""),
                    "destination": old_p.get("destination", ""),
                    "total_bytes": tb,
                    "elapsed_str": old_p.get("elapsed_str", ""),
                    "finished_at": now
                }
                recent_completed.insert(0, completed_item)
                # Desktop notification
                try:
                    notif_text = f"{old_p.get('tool', 'Transfer')}: {format_bytes_py(tb)} transferred ({old_p.get('elapsed_str', '')})"
                    notif_text = notif_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    subprocess.Popen([
                        "notify-send",
                        "--app-name=Transfer Monitor",
                        "-i", "drive-harddisk",
                        "Transfer Completed",
                        notif_text
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

    for root_pid in root_pids:
        try:
            with open(f"/proc/{root_pid}/cmdline", "rb") as f:
                raw = f.read().decode("utf-8", "replace").split("\x00")
                args = [a for a in raw if a]
            if not args:
                continue

            cwd = ""
            try:
                cwd = os.readlink(f"/proc/{root_pid}/cwd")
            except Exception:
                pass

            tool_name, src_path, dst_path = parse_command_details(args, cwd)
            tree_pids = get_process_tree(root_pid)
            rb, wb, rss, state = get_tree_io_and_stat(tree_pids)

            # Speed calculation
            prev = prev_processes.get(str(root_pid))
            read_speed = 0.0
            write_speed = 0.0
            dt = 1.0

            if prev and "timestamp" in prev:
                dt = max(0.2, now - prev["timestamp"])
                read_speed = max(0.0, (rb - prev.get("rb", rb)) / dt)
                write_speed = max(0.0, (wb - prev.get("wb", wb)) / dt)
                prev_rs = prev.get("read_speed", 0.0)
                prev_ws = prev.get("write_speed", 0.0)
                read_speed = 0.7 * read_speed + 0.3 * prev_rs
                write_speed = 0.7 * write_speed + 0.3 * prev_ws

            if state == "paused":
                read_speed = 0.0
                write_speed = 0.0
                active_speed = 0.0
            else:
                active_speed = max(read_speed, write_speed)

            total_agg_read_speed += read_speed
            total_agg_write_speed += write_speed

            active_file = get_active_file_info(tree_pids, src_path, dst_path)

            file_eta_str = ""
            if active_file and active_speed > 1024:
                rem_bytes = max(0, active_file["size"] - active_file["pos"])
                if rem_bytes > 0:
                    file_eta_str = format_eta(rem_bytes / active_speed)

            dest_fs = None
            if dst_path:
                probe_dir = dst_path if os.path.isdir(dst_path) else os.path.dirname(dst_path)
                dest_fs = get_filesystem_info(probe_dir)

            source_fs = None
            if src_path:
                probe_src = src_path if os.path.isdir(src_path) else os.path.dirname(src_path)
                source_fs = get_filesystem_info(probe_src)

            # Overall transfer estimation
            overall_total_bytes = 0
            overall_pct = 0.0
            overall_eta = ""

            if src_path and os.path.isfile(src_path):
                try:
                    overall_total_bytes = os.path.getsize(src_path)
                except Exception:
                    pass
            elif source_fs and os.path.isdir(src_path):
                # If source is an entire mount or partition
                norm_src = os.path.abspath(src_path).rstrip("/")
                norm_mnt = source_fs.get("mount", "").rstrip("/")
                if norm_src == norm_mnt:
                    overall_total_bytes = source_fs.get("used_bytes", 0)

            elapsed_sec = 0
            try:
                p_st = os.stat(f"/proc/{root_pid}")
                elapsed_sec = max(1, int(now - p_st.st_mtime))
            except Exception:
                pass

            if overall_total_bytes > 0:
                transferred_bytes = max(rb, wb)
                overall_pct = max(0.0, min(100.0, round((transferred_bytes / overall_total_bytes * 100), 1)))
                rem_overall = max(0, overall_total_bytes - transferred_bytes)
                if rem_overall > 0:
                    # Stabilize overall ETA: blend historical average speed (70%) with smoothed speed (30%)
                    # to prevent wild fluctuations between large files and small files.
                    historical_speed = (transferred_bytes / elapsed_sec) if elapsed_sec > 10 else active_speed
                    effective_speed = (0.7 * historical_speed + 0.3 * active_speed) if active_speed > 1024 else historical_speed
                    if effective_speed > 1024:
                        overall_eta = format_eta(rem_overall / effective_speed)

            category = "sync"
            if tool_name in ("curl", "wget", "aria2c", "yt-dlp"):
                category = "download"
            elif tool_name in ("cp", "mv"):
                category = "copy"
            elif tool_name == "dd":
                category = "disk_image"
            elif "git" in tool_name:
                category = "git"
            elif tool_name in ("pacman", "yay", "paru"):
                category = "system"
            elif "rclone" in tool_name:
                category = "cloud"

            if tool_name in ("curl", "wget"):
                has_dest_file = bool(dst_path and dst_path != cwd)
                # If it's a brief API query to stdout with no file, ignore it
                if not has_dest_file and max(rb, wb) < 256 * 1024 and elapsed_sec < 4 and active_speed < 1024:
                    continue

            item_data = {
                "pid": root_pid,
                "tree_pids": tree_pids,
                "tool": tool_name,
                "category": category,
                "state": state,
                "command": " ".join(args),
                "source": src_path,
                "destination": dst_path,
                "cwd": cwd,
                "read_bytes": rb,
                "write_bytes": wb,
                "total_bytes": max(rb, wb),
                "read_speed": round(read_speed),
                "write_speed": round(write_speed),
                "active_speed": round(active_speed),
                "rss_bytes": rss,
                "elapsed_sec": elapsed_sec,
                "elapsed_str": format_eta(elapsed_sec),
                "active_file": active_file,
                "file_eta": file_eta_str,
                "dest_fs": dest_fs,
                "source_fs": source_fs,
                "overall_total_bytes": overall_total_bytes,
                "overall_pct": overall_pct,
                "overall_eta": overall_eta
            }
            transfers.append(item_data)

            new_processes[str(root_pid)] = {
                "timestamp": now,
                "rb": rb,
                "wb": wb,
                "read_speed": read_speed,
                "write_speed": write_speed,
                "total_bytes": max(rb, wb),
                "elapsed_sec": elapsed_sec,
                "elapsed_str": format_eta(elapsed_sec),
                "tool": tool_name,
                "source": src_path,
                "destination": dst_path
            }
        except Exception:
            pass

    save_current_state(new_processes, recent_completed)

    result = {
        "timestamp": now,
        "count": len(transfers),
        "agg_read_speed": round(total_agg_read_speed),
        "agg_write_speed": round(total_agg_write_speed),
        "agg_speed": round(max(total_agg_read_speed, total_agg_write_speed)),
        "transfers": transfers,
        "recent_completed": recent_completed[:5]
    }
    return result

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
        if flag == "--pause":
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

    res = collect_all_transfers()
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
