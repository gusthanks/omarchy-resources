"""Unit tests for collect-transfers.py: python3 -m unittest discover dev/tests
(or dev/run.sh test). Standard library only; nothing here reads /proc."""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("collect_transfers", os.path.join(ROOT, "collect-transfers.py"))
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


class IdeviceBackup2Args(unittest.TestCase):
    def parse(self, line, cwd="/home/user"):
        return ct.parse_idevicebackup2_args(line.split(), cwd)

    def test_plain_backup(self):
        self.assertEqual(self.parse("idevicebackup2 backup /mnt/backups"), ("backup", "", "/mnt/backups"))

    def test_option_values_are_not_positional(self):
        self.assertEqual(
            self.parse("idevicebackup2 -u 00008030-001A backup --full /mnt/backups"),
            ("backup", "00008030-001A", "/mnt/backups"))
        self.assertEqual(
            self.parse("idevicebackup2 --udid 00008030-001A -s 00008030-002B restore --system /mnt/b"),
            ("restore", "00008030-001A", "/mnt/b"))
        self.assertEqual(
            self.parse("idevicebackup2 --udid=00008030-001A backup /mnt/b"),
            ("backup", "00008030-001A", "/mnt/b"))
        self.assertEqual(
            self.parse("idevicebackup2 restore --password secret /mnt/b"),
            ("restore", "", "/mnt/b"))

    def test_directory_defaults_to_cwd(self):
        self.assertEqual(self.parse("idevicebackup2 -u ABC backup", cwd="/srv"), ("backup", "ABC", "/srv"))
        self.assertEqual(self.parse("idevicebackup2", cwd="/srv"), ("backup", "", "/srv"))

    def test_parse_command_details(self):
        tool, src, dst = ct.parse_command_details("idevicebackup2 -u ABC backup /mnt/b".split(), "/x")
        self.assertEqual((tool, src, dst), ("idevicebackup2 backup", "", "/mnt/b"))


class RuntimeDir(unittest.TestCase):
    def test_prefers_xdg_runtime_dir(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            self.assertEqual(ct.runtime_dir(), d)
            self.assertEqual(ct.runtime_file("x"), os.path.join(d, "x"))

    def test_falls_back_without_xdg_runtime_dir(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            d = ct.runtime_dir()
            self.assertIn(d, (f"/run/user/{os.getuid()}", f"/tmp/gusthx-resources-{os.getuid()}"))

    def test_same_rule_as_collect_details(self):
        # collect-details.sh writes the iOS cache where this script reads it.
        import subprocess
        with open(os.path.join(ROOT, "collect-details.sh")) as fh:
            script = fh.read()
        start = script.index("runtime_dir() {")
        end = script.index("\n}\n", start) + 3
        for env in ({"XDG_RUNTIME_DIR": ""}, {}):
            with tempfile.TemporaryDirectory() as d:
                e = dict(os.environ, XDG_RUNTIME_DIR=d)
                e.update(env)
                out = subprocess.run(["bash", "-c", script[start:end] + "runtime_dir"], env=e,
                                     capture_output=True, text=True).stdout.strip()
                with mock.patch.dict(os.environ, e, clear=True):
                    self.assertEqual(out, ct.runtime_dir())


class IosUsedBytes(unittest.TestCase):
    def test_reads_the_cache_and_matches_the_udid(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            with open(os.path.join(d, ct.IOS_CACHE_NAME), "w") as fh:
                fh.write("DISK\tiPhone · A\tafc\tiPhone14,2\t1000\t600\t400\t60\tUDID-A\n")
                fh.write("DISK\tiPad · B\tafc\tiPad13,1\t2000\t500\t1500\t25\tUDID-B\n")
            self.assertEqual(ct.ios_used_bytes(), 600)
            self.assertEqual(ct.ios_used_bytes("UDID-B"), 500)

    def test_asks_ideviceinfo_when_there_is_no_cache(self):
        out = "TotalDataCapacity: 1000\nAmountDataAvailable: 250\n"
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}), \
                mock.patch.object(ct.subprocess, "run", return_value=mock.Mock(stdout=out)) as run:
            self.assertEqual(ct.ios_used_bytes("UDID-C"), 750)
            self.assertEqual(run.call_args[0][0], ["ideviceinfo", "-u", "UDID-C", "-q", "com.apple.disk_usage"])

    def test_no_tools_no_estimate(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}), \
                mock.patch.object(ct.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(ct.ios_used_bytes(), 0)


class StateFile(unittest.TestCase):
    def test_state_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            ct.save_current_state({"1": {"timestamp": 1}}, [])
            path = os.path.join(d, ct.STATE_FILE_NAME)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(ct.load_previous_state()["processes"], {"1": {"timestamp": 1}})


class OtherParsers(unittest.TestCase):
    def test_rsync_skips_option_values(self):
        self.assertEqual(ct.parse_command_details(["rsync", "-a", "--exclude", "x", "/a/", "/b/"], "/"),
                         ("rsync", "/a/", "/b/"))

    def test_cp_target_directory(self):
        self.assertEqual(ct.parse_command_details(["cp", "-t", "/dst", "f1"], "/"), ("cp", "/f1", "/dst"))


class Paths(unittest.TestCase):
    def test_relative_paths_are_resolved_against_the_cwd(self):
        self.assertEqual(ct.parse_command_details(["cp", "-r", "photos", "../backup/"], "/data/me"),
                         ("cp", "/data/me/photos", "/data/backup/"))
        self.assertEqual(ct.parse_command(["cp", "a", "b", "/dst"], "/w")["sources"], ["/w/a", "/w/b"])

    def test_remotes_and_urls_stay_as_typed(self):
        self.assertEqual(ct.parse_command_details(["rsync", "-a", "docs/", "host:backup/"], "/home/u"),
                         ("rsync", "/home/u/docs/", "host:backup/"))
        self.assertEqual(ct.parse_command_details(["rclone", "copy", "x.tar", "gdrive:b/"], "/d"),
                         ("rclone copy", "/d/x.tar", "gdrive:b/"))
        tool, src, dst = ct.parse_command_details(["curl", "-o", "f.iso", "https://e.org/f.iso"], "/dl")
        self.assertEqual((src, dst), ("https://e.org/f.iso", "/dl/f.iso"))

    def test_dd(self):
        self.assertEqual(ct.parse_command_details(["dd", "if=a.img", "of=/dev/sdx"], "/w"),
                         ("dd", "/w/a.img", "/dev/sdx"))

    def test_unknown_tools_never_show_an_option_as_a_path(self):
        self.assertEqual(ct.parse_command_details(["zstd", "-19", "big.bin"], "/w"), ("zstd", "/w/big.bin", ""))


class Remote(unittest.TestCase):
    def test_rclone_commands(self):
        self.assertEqual(ct.rclone_subcommand(["rclone", "-q", "copy", "a", "gdrive:b"]), "copy")
        # "rclone" contains "rc": only the command word decides.
        self.assertIn(ct.rclone_subcommand(["rclone", "copy", "a", "gdrive:b"]), ct.RCLONE_TRANSFER_COMMANDS)
        for sub in ("mount", "config", "rc", "rcd", "serve", "lsf", "version"):
            self.assertNotIn(ct.rclone_subcommand(["/usr/bin/rclone", sub, "gdrive:"]), ct.RCLONE_TRANSFER_COMMANDS)

    def test_rclone_option_values_are_not_paths(self):
        line = "rclone copy --transfers 4 --drive-chunk-size 64M --stats 60s -q dji gdrive:raw/dji"
        self.assertEqual(ct.parse_command_details(line.split(), "/data"),
                         ("rclone copy", "/data/dji", "gdrive:raw/dji"))

    def test_scp(self):
        self.assertEqual(ct.parse_command_details("scp -P 2222 -r site host:/var/www".split(), "/w"),
                         ("scp", "/w/site", "host:/var/www"))
        self.assertEqual(ct.parse_command_details("scp host:big.iso .".split(), "/w"),
                         ("scp", "host:big.iso", "/w"))

    def test_direction(self):
        self.assertEqual(ct.transfer_direction("rclone copy", "/d/x", "gdrive:b/"), "upload")
        self.assertEqual(ct.transfer_direction("rsync", "host:a/", "/b/"), "download")
        self.assertEqual(ct.transfer_direction("curl", "https://e.org/f", "/dl/f"), "download")
        self.assertEqual(ct.transfer_direction("git push", "origin", "main"), "")
        self.assertEqual(ct.transfer_direction("cp", "/a", "/b"), "")
        self.assertEqual(ct.transfer_direction("idevicebackup2 backup", "", "/b"), "")

    def test_progress_from_the_local_side_and_speed_from_the_socket(self):
        # upload: 300 read from the source, 120 sent so far
        self.assertEqual(ct.network_progress("upload", 200, 0, 300, 120), (300, 120))
        # download: 500 received, 480 already written to the file
        self.assertEqual(ct.network_progress("download", 0, 400, 500, 480), (480, 500))

    def test_what_went_through_counts_sockets(self):
        rc, wc = ct.get_tree_char_io([os.getpid()])
        self.assertGreater(rc, 0)
        self.assertEqual(ct.get_tree_char_io([2 ** 31 - 1]), (0, 0))


class Tar(unittest.TestCase):
    def parse(self, line, cwd="/data"):
        return ct.parse_command_details(line.split(), cwd)

    def test_create(self):
        self.assertEqual(self.parse("tar -cf phone.tar phone"), ("tar", "/data/phone", "/data/phone.tar"))
        self.assertEqual(self.parse("tar czf /b/p.tgz a b"), ("tar", "/data/a", "/b/p.tgz"))
        self.assertEqual(self.parse("tar --create --file=p.tar -C /src a"), ("tar", "/src/a", "/data/p.tar"))
        self.assertEqual(ct.parse_command("tar -cf p.tar a b".split(), "/data")["sources"], ["/data/a", "/data/b"])

    def test_extract(self):
        self.assertEqual(self.parse("tar -xzf p.tgz"), ("tar", "/data/p.tgz", "/data"))
        self.assertEqual(self.parse("tar -xf p.tar -C out"), ("tar", "/data/p.tar", "/data/out"))
        self.assertEqual(self.parse("tar xfC p.tar /out"), ("tar", "/data/p.tar", "/out"))

    def test_options_with_values(self):
        self.assertEqual(self.parse("tar --exclude x -I zstd -cf p.tar a"), ("tar", "/data/a", "/data/p.tar"))
        self.assertEqual(self.parse("tar -cfp.tar a"), ("tar", "/data/a", "/data/p.tar"))

    def test_pipes_have_no_archive(self):
        self.assertEqual(self.parse("tar -cf - a"), ("tar", "/data/a", ""))
        self.assertEqual(self.parse("tar -tf p.tar"), ("tar", "/data/p.tar", ""))


class Batch(unittest.TestCase):
    def test_script_of(self):
        self.assertEqual(ct.script_of(["bash", "/s/up.sh"]), "/s/up.sh")
        self.assertEqual(ct.script_of(["/usr/bin/bash", "-eu", "up.sh", "arg"], "/s"), "/s/up.sh")
        self.assertEqual(ct.script_of(["python3.12", "-u", "job.py"], "/s"), "/s/job.py")
        self.assertEqual(ct.script_of(["bash", "-c", "tar cf a b"]), "")
        self.assertEqual(ct.script_of(["bash", "-lc", "tar cf a b"]), "")
        self.assertEqual(ct.script_of(["-bash"]), "")
        self.assertEqual(ct.script_of(["python3", "-m", "http.server"]), "")
        self.assertEqual(ct.script_of(["kitty", "file.sh"]), "")

    def tree(self, procs):
        # pid -> (ppid, args); every process belongs to uid 1000
        return lambda pid: (procs[pid][0], 1000, procs[pid][1], "/w", 100) if pid in procs else None

    def test_finds_the_script_through_wrappers(self):
        read = self.tree({
            50: (40, ["tar", "-cf", "a.tar", "a"]),
            40: (30, ["nice", "-n", "19", "tar", "-cf", "a.tar", "a"]),
            30: (20, ["bash", "/s/up.sh"]),
            20: (10, ["bash", "-c", "bash /s/up.sh"]),
            10: (1, ["kitty"]),
        })
        b = ct.find_batch(50, read=read, uid=1000)
        self.assertEqual((b["pid"], b["name"], b["path"]), (30, "up.sh", "/s/up.sh"))

    def test_a_command_typed_in_a_terminal_has_no_batch(self):
        read = self.tree({50: (20, ["cp", "a", "b"]), 20: (10, ["-bash"]), 10: (1, ["kitty"])})
        self.assertIsNone(ct.find_batch(50, read=read, uid=1000))

    def test_stops_at_other_programs(self):
        read = self.tree({50: (30, ["cp", "a", "b"]), 30: (20, ["nautilus"]), 20: (1, ["bash", "/s/x.sh"])})
        self.assertIsNone(ct.find_batch(50, read=read, uid=1000))

    def test_make(self):
        read = self.tree({50: (30, ["sh", "-c", "rsync a b"]), 30: (1, ["make", "-j4", "deploy"])})
        self.assertEqual(ct.find_batch(50, read=read, uid=1000)["name"], "make deploy")


class ProcessState(unittest.TestCase):
    def test_reads_the_state_of_a_live_process_and_of_none(self):
        self.assertIn(ct.process_state(os.getpid()), ("R", "S"))
        self.assertEqual(ct.process_state(2 ** 31 - 1), "")

    def test_a_batch_is_stopped_from_the_script_down(self):
        # Pause/cancel walk the tree root first, so the script cannot start its
        # next step while its current one is being signalled.
        with mock.patch.object(ct, "get_process_tree", return_value=[30, 40, 50]), \
                mock.patch.object(ct.os, "kill") as kill, mock.patch("builtins.print"):
            ct.handle_action("cancel", "30")
        self.assertEqual([c[0][0] for c in kill.call_args_list], [30, 40, 50])


class Summary(unittest.TestCase):
    def test_totals_and_batches(self):
        b = {"pid": 7, "name": "up.sh"}
        s = ct.summarize([
            {"total_bytes": 50, "overall_total_bytes": 100, "active_speed": 5, "batch": dict(b)},
            {"total_bytes": 300, "overall_total_bytes": 300, "active_speed": 7, "batch": dict(b)},
        ])
        self.assertEqual((s["total_bytes"], s["overall_total_bytes"], s["overall_pct"]), (350, 400, 87.5))
        self.assertEqual([(x["pid"], x["transfers"], x["active_speed"]) for x in s["batches"]], [(7, 2, 12)])

    def test_no_combined_progress_when_a_total_is_unknown(self):
        s = ct.summarize([{"total_bytes": 50, "overall_total_bytes": 100, "batch": None},
                          {"total_bytes": 9, "overall_total_bytes": 0, "batch": None}])
        self.assertEqual((s["total_bytes"], s["overall_total_bytes"], s["batches"]), (59, 0, []))


class SourceSize(unittest.TestCase):
    def wait_done(self, key, paths, timeout=60):
        import time
        size, scanning = 0, True
        deadline = time.time() + timeout
        while time.time() < deadline:
            size, scanning = ct.source_size(key, paths)
            if not scanning:
                break
            time.sleep(0.1)
        return size, scanning

    def test_du_runs_in_the_background_and_is_read_later(self):
        import time
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            with open(os.path.join(src, "f"), "wb") as fh:
                fh.write(b"x" * 5000)
            key = ct.du_size_key("cp", [src])
            self.assertEqual(ct.source_size(key, [src]), (0, True))
            size, scanning = self.wait_done(key, [src])
            self.assertFalse(scanning)
            self.assertGreaterEqual(size, 5000)
            # Fresh answers are kept for other transfers over the same folders...
            ct.forget_source_sizes({"9-9"})
            self.assertTrue([n for n in os.listdir(d) if n.startswith(ct.SIZE_FILE_PREFIX)])
            # ...and forgotten after their TTL.
            ct.forget_source_sizes({"9-9"}, now=time.time() + ct.DU_TTL + 5)
            self.assertEqual([n for n in os.listdir(d) if n.startswith(ct.SIZE_FILE_PREFIX)], [])

    def test_the_answer_is_reused_by_a_new_transfer_over_the_same_paths(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            with open(os.path.join(src, "f"), "wb") as fh:
                fh.write(b"x" * 3000)
            key = ct.du_size_key("cp", [src])
            self.assertEqual(key, ct.du_size_key("cp", [src]))
            self.assertNotEqual(key, ct.du_size_key("tar", [src]))
            self.assertEqual(ct.du_size_key("cp", ["/b", "/a"]), ct.du_size_key("cp", ["/a", "/b"]))
            ct.source_size_job(key, [src], target_pid=111, target_start=1)
            self.assertGreaterEqual(self.wait_done(key, [src])[0], 3000)
            with mock.patch.object(ct.os, "posix_spawnp") as spawn:
                job = ct.source_size_job(key, [src], target_pid=222, target_start=2)
            spawn.assert_not_called()
            self.assertEqual(job["state"], "done")

    def test_forget_keeps_live_and_running_jobs(self):
        import time
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            old = time.time() - 4000
            for key, state in (("du-live", "done"), ("rs-old", "done"), ("du-old", "failed")):
                path, _req = ct._size_paths(key)
                ct._write_json_atomic(path, {"key": key, "state": state, "finished": old, "updated": old})
            ct.forget_source_sizes({"du-live"})
            left = sorted(n for n in os.listdir(d) if n.startswith(ct.SIZE_FILE_PREFIX))
            self.assertEqual(left, [ct.SIZE_FILE_PREFIX + "du-live.json"])

    def test_nothing_to_size(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            self.assertEqual(ct.source_size("1-2", ["/nonexistent/x"]), (0, False))


# ---------------------------------------------------------------------------
# Following a copy as it really happens. The fixture is a sanitized sample of a
# real disk rescue: a script (copy.sh, under a thermal guard) running a local
# rsync with 9 sources, an --exclude-from list and --relative.

SRC = "/run/media/user/WINDISK"
DST = "/mnt/backup/rescue"
RSYNC_A = ["rsync", "-rlt", "--relative", "--partial", "--info=stats1",
           "--log-file=/home/user/.local/state/rescue/copy.log.rsync",
           "--exclude-from=/mnt/backup/rescue/_cloud-only.txt.rsync",
           "./bench", "./dev", "./vault", "./k", "./master", "./Projects", "./Sites", "./c",
           "./Users/me/AppData/Local/app", "/mnt/backup/rescue/"]
COPY_SH = ["/bin/bash", "/home/user/.local/state/rescue/copy.sh"]
GUARD = ["/bin/bash", "/home/user/.local/bin/thermal-guard"] + COPY_SH
# pid -> (ppid, comm, cwd, argv, io)
FIXTURE = {
    29670: (752, "bash", "/home/user", GUARD, (3116265, 2180, 167936, 0)),
    29684: (29670, "bash", SRC, COPY_SH, (212244144, 209979844, 8764424192, 3637248)),
    30073: (29684, "rsync", SRC, RSYNC_A, (47110485564, 47408741126, 48946954240, 221569024)),
    30074: (30073, "rsync", DST, RSYNC_A, (240298276, 24069913, 142909440, 115875840)),
    30075: (30074, "rsync", DST, RSYNC_A, (47241071354, 47370617276, 5422530560, 50426683392)),
}

STATS_BLOCK = """gusthx-resources-size:cd+++++++++|60
gusthx-resources-size:>f+++++++++|5000

Number of files: 7 (reg: 3, dir: 4)
Number of created files: 4 (reg: 2, dir: 2)
Number of deleted files: 0
Number of regular files transferred: 2
Total file size: 9100 bytes
Total transferred file size: 6100 bytes
Literal data: 0 bytes
Matched data: 0 bytes
File list size: 0
File list generation time: 0.001 seconds
File list transfer time: 0.000 seconds
Total bytes sent: 211
Total bytes received: 31

sent 211 bytes  received 31 bytes  484.00 bytes/sec
total size is 9100  speedup is 48.92 (DRY RUN)
"""


def fixture_patches():
    """Patch the /proc readers with the fixture."""
    def io(pid):
        rc, wc, rb, wb = FIXTURE[pid][4] if pid in FIXTURE else (0, 0, 0, 0)
        return {"rchar": rc, "wchar": wc, "read_bytes": rb, "write_bytes": wb}
    return [
        mock.patch.object(ct, "read_comm", lambda p: FIXTURE[p][1] if p in FIXTURE else ""),
        mock.patch.object(ct, "read_ppid", lambda p: FIXTURE[p][0] if p in FIXTURE else 0),
        mock.patch.object(ct, "read_cwd", lambda p: FIXTURE[p][2] if p in FIXTURE else ""),
        mock.patch.object(ct, "read_proc_io", io),
        mock.patch.object(ct, "read_cmdline", lambda p: FIXTURE[p][3] if p in FIXTURE else []),
    ]


class RsyncArgs(unittest.TestCase):
    def kind(self, line):
        args = line if isinstance(line, list) else line.split()
        return ct.classify_kind("rsync", args)[0]

    def test_dry_run_forms(self):
        for line in ("rsync -rltn a/ b/", "rsync -n a/ b/", "rsync --dry-run a/ b/",
                     "rsync -avn --delete a/ b/", "rsync a/ b/ -n"):
            self.assertEqual(self.kind(line), "simulation", line)
        self.assertEqual(self.kind("rsync -rlt a/ b/"), "transfer")

    def test_option_values_are_not_a_dry_run(self):
        self.assertEqual(self.kind(["rsync", "-e", "ssh -n", "a/", "/b/"]), "transfer")
        self.assertEqual(self.kind(["rsync", "--exclude=-n", "a/", "/b/"]), "transfer")
        self.assertEqual(self.kind(["rsync", "-f- n", "a/", "/b/"]), "transfer")
        self.assertEqual(self.kind(["rsync", "-f", "- n", "a/", "/b/"]), "transfer")
        self.assertEqual(self.kind(["rsync", "--exclude", "-n", "a/", "/b/"]), "transfer")
        self.assertEqual(self.kind(["rsync", "-rlte", "ssh -n", "a/", "/b/"]), "transfer")

    def test_later_options_win(self):
        self.assertEqual(self.kind("rsync -n --no-dry-run a/ b/"), "transfer")
        self.assertEqual(self.kind("rsync --no-dry-run -n a/ b/"), "simulation")

    def test_listing(self):
        self.assertEqual(self.kind("rsync --list-only a/ b/"), "listing")
        self.assertEqual(self.kind("rsync -r host:/srv/"), "listing")
        p = ct.parse_rsync_args("rsync -r /srv/".split())
        self.assertEqual((p["sources"], p["destination"]), (["/srv/"], ""))

    def test_option_values_are_not_operands(self):
        line = ("rsync -T /tmp --partial-dir .p --link-dest ../prev -B 1024 -@ 2 --log-file x "
                "--out-format x -M--fake-super --temp-dir=/t a/ b/")
        p = ct.parse_rsync_args(line.split())
        self.assertEqual(p["operands"], ["a/", "b/"])
        self.assertEqual(p["values"]["-T"], ["/tmp"])
        self.assertEqual(p["values"]["--link-dest"], ["../prev"])
        self.assertEqual(p["values"]["-@"], ["2"])
        self.assertEqual(p["values"]["-M"], ["--fake-super"])
        p = ct.parse_rsync_args(["rsync", "-avT/tmp", "x", "y"])
        self.assertEqual((p["operands"], p["values"]["-T"]), (["x", "y"], ["/tmp"]))
        self.assertIn("-v", p["flags"])

    def test_double_dash_and_stdin(self):
        p = ct.parse_rsync_args(["rsync", "-r", "--", "-n", "b/"])
        self.assertEqual((p["operands"], p["dry_run"]), (["-n", "b/"], False))

    def test_fixture_has_nine_sources(self):
        p = ct.parse_rsync_args(RSYNC_A)
        self.assertEqual(len(p["sources"]), 9)
        self.assertEqual(p["destination"], DST + "/")
        self.assertEqual(p["remote_mode"], "local")
        self.assertFalse(p["dry_run"] or p["list_only"] or p["stdin_lists"] or p["batch_mode"])
        self.assertEqual(p["values"]["--exclude-from"], ["/mnt/backup/rescue/_cloud-only.txt.rsync"])
        tool, src, dst = ct.parse_command_details(RSYNC_A, SRC)
        self.assertEqual((tool, src, dst), ("rsync", SRC + "/bench", DST + "/"))

    def test_relative_sources_are_resolved_against_the_cwd(self):
        info = ct.parse_command(RSYNC_A, SRC)
        self.assertEqual(len(info["sources"]), 9)
        self.assertEqual(info["sources"][0], SRC + "/bench")
        self.assertEqual(info["sources"][-1], SRC + "/Users/me/AppData/Local/app")
        self.assertEqual(info["all_sources"], info["sources"])
        info = ct.parse_command(["rsync", "-R", "/data/./photos/2024", "host:/b/"], "/x")
        self.assertEqual(info["source"], "/data/photos/2024")

    def test_remote_modes(self):
        self.assertEqual(ct.parse_rsync_args("rsync -a d/ host:/x".split())["remote_mode"], "push")
        self.assertEqual(ct.parse_rsync_args("rsync -a u@h::mod/ /d/".split())["remote_mode"], "pull")
        self.assertEqual(ct.parse_rsync_args("rsync -a rsync://h/m/ /d/".split())["remote_mode"], "pull")
        self.assertEqual(ct.parse_rsync_args("rsync -a ./a:b /d/".split())["remote_mode"], "local")
        info = ct.parse_command("rsync -a a/ host:/x".split(), "/w")
        self.assertEqual((info["sources"], info["destination"]), (["/w/a/"], "host:/x"))

    def test_server_side_and_batch(self):
        self.assertTrue(ct.parse_rsync_args("rsync --server -logDtpre.iLsfxCIvu . /d".split())["server"])
        self.assertTrue(ct.parse_rsync_args("rsync --write-batch=b a/ /d/".split())["batch_mode"])
        self.assertTrue(ct.parse_rsync_args("rsync --files-from=- / /d/".split())["stdin_lists"])
        self.assertTrue(ct.parse_rsync_args(["rsync", "--filter=merge -", "a/", "/d/"])["stdin_lists"])
        self.assertTrue(ct.parse_rsync_args(["rsync", "-f", ". -", "a/", "/d/"])["stdin_lists"])
        self.assertFalse(ct.parse_rsync_args(["rsync", "--filter=- *.tmp", "a/", "/d/"])["stdin_lists"])

    def test_checksum_flag(self):
        self.assertTrue(ct.parse_rsync_args("rsync -avc a/ /d/".split())["checksum"])
        self.assertFalse(ct.parse_rsync_args("rsync -a --exclude=-c a/ /d/".split())["checksum"])


class RsyncHelpTable(unittest.TestCase):
    def test_every_option_with_a_value_is_known(self):
        import re
        import shutil
        import subprocess
        if not shutil.which("rsync"):
            self.skipTest("rsync is not installed")
        text = subprocess.run(["rsync", "--help"], capture_output=True, text=True).stdout
        longs = set(re.findall(r"(--[a-z0-9-]+)=[A-Za-z|]", text))
        self.assertTrue(longs)
        self.assertEqual(sorted(longs - ct.RSYNC_LONG_WITH_ARG), [])
        shorts = set(re.findall(r"--[a-z0-9-]+=[A-Z]+, -(\S)\s", text))
        self.assertTrue(shorts)
        self.assertEqual(sorted(shorts - ct.RSYNC_SHORT_WITH_ARG), [])
        self.assertNotIn("F", ct.RSYNC_SHORT_WITH_ARG)


class Roles(unittest.TestCase):
    def test_local_copy_receiver_is_the_grandchild(self):
        patches = fixture_patches()
        for p in patches:
            p.start()
        try:
            roles = ct.rsync_roles(30073, [30073, 30074, 30075], "local")
            self.assertEqual(roles, {"sender": 30073, "generator": 30074, "receiver": 30075})
            moved, counter, receiver = ct.rsync_progress_counter(30073, [30073, 30074, 30075])
            self.assertEqual((moved, counter, receiver), (47370617276, "receiver_write", 30075))
        finally:
            for p in patches:
                p.stop()

    def roles(self, procs, root, mode):
        return ct.rsync_roles(root, list(procs), mode,
                              comm_of=lambda p: procs[p][1], ppid_of=lambda p: procs[p][0],
                              cwd_of=lambda p: procs[p][2])

    def test_pull_with_ssh(self):
        procs = {10: (1, "rsync", "/d"), 11: (10, "ssh", "/d"), 12: (10, "rsync", "/d")}
        self.assertEqual(self.roles(procs, 10, "pull")["receiver"], 12)
        self.assertEqual(self.roles(procs, 10, "pull")["generator"], 10)

    def test_push_has_no_local_receiver(self):
        procs = {10: (1, "rsync", "/s"), 11: (10, "ssh", "/s")}
        self.assertIsNone(self.roles(procs, 10, "push")["receiver"])

    def test_just_forked_falls_back_to_the_tree(self):
        procs = {10: (1, "rsync", "/s"), 11: (10, "rsync", "/d")}
        self.assertIsNone(self.roles(procs, 10, "local")["receiver"])
        with mock.patch.object(ct, "rsync_roles", return_value={"sender": 10, "generator": 11, "receiver": None}), \
                mock.patch.object(ct, "read_proc_io", lambda p: {"rchar": 0, "wchar": 0, "read_bytes": 0,
                                                                 "write_bytes": 100 * p}):
            moved, counter, receiver = ct.rsync_progress_counter(10, [10, 11], ct.parse_rsync_args(["rsync", "a", "/b"]))
        self.assertEqual((moved, counter, receiver), (2100, "tree_write", None))

    def test_different_cwds_are_not_a_receiver(self):
        procs = {10: (1, "rsync", "/s"), 11: (10, "rsync", "/d"), 12: (11, "rsync", "/elsewhere")}
        self.assertIsNone(self.roles(procs, 10, "local")["receiver"])

    def test_writing_position_skips_logs(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = open(os.path.join(d, ".f.XXXX"), "w+b")
            log = open(os.path.join(d, "log"), "ab")
            try:
                tmp.write(b"x" * 1234)
                tmp.flush()
                log.write(b"y" * 999)
                log.flush()
                self.assertGreaterEqual(ct.writing_position(os.getpid()), 1234)
                self.assertLess(ct.writing_position(os.getpid()), 1234 + 999)
            finally:
                tmp.close()
                log.close()


class SizingArgv(unittest.TestCase):
    def test_strips_output_options_and_inserts_the_marker(self):
        args = ["rsync", "-avPc", "--info=progress2", "--log-file=/l", "--log-file-format", "%f",
                "--stats", "-h", "-i", "--exclude-from=/x.txt", "--files-from=/list", "--exclude", "-v",
                "-e", "ssh -v", "./a", "./b", "/dst/"]
        argv, reason = ct.rsync_sizing_argv(args, "/src")
        self.assertEqual(reason, "")
        self.assertEqual(argv[:5], ["rsync", "--dry-run", "--stats", "--no-human-readable", ct.SIZER_OUT_FORMAT])
        rest = argv[5:]
        self.assertEqual(rest, ["-a", "--exclude-from=/x.txt", "--files-from=/list", "--exclude", "-v",
                                "-e", "ssh -v", "./a", "./b", "/dst/"])
        self.assertTrue(ct.is_sizing_helper(argv))
        self.assertFalse(ct.is_sizing_helper(args))

    def test_groups_keep_letters_with_values(self):
        argv, _ = ct.rsync_sizing_argv(["rsync", "-vT/tmp", "-qe", "ssh", "a", "/b/"], "/w")
        self.assertEqual(argv[5:], ["-T/tmp", "-e", "ssh", "a", "/b/"])
        argv, _ = ct.rsync_sizing_argv(["rsync", "-v", "a", "/b/", "--no-dry-run"], "/w")
        self.assertEqual(argv[5:], ["a", "/b/"])

    def test_relative_program_is_resolved(self):
        argv, _ = ct.rsync_sizing_argv(["./bin/rsync", "a", "/b/"], "/w")
        self.assertEqual(argv[0], "/w/bin/rsync")

    def test_key_ignores_logs_and_verbosity(self):
        k1 = ct.rsync_size_key(SRC, RSYNC_A)
        other = [a if not a.startswith("--log-file=") else "--log-file=/elsewhere" for a in RSYNC_A]
        other = [a if a != "--info=stats1" else "--info=progress2" for a in other] + ["-v"]
        self.assertEqual(ct.rsync_size_key(SRC, other), k1)
        self.assertTrue(k1.startswith("rs-"))
        self.assertNotEqual(ct.rsync_size_key("/elsewhere", RSYNC_A), k1)
        changed = RSYNC_A[:6] + ["--exclude-from=/other.txt"] + RSYNC_A[7:]
        self.assertNotEqual(ct.rsync_size_key(SRC, changed), k1)

    def test_skip_reasons(self):
        self.assertEqual(ct.rsync_sizing_argv("rsync -a a/ host:/b/".split())[1], "remote")
        self.assertEqual(ct.rsync_sizing_argv("rsync -a host:/a/ /b/".split())[1], "remote")
        self.assertEqual(ct.rsync_sizing_argv("rsync -a --files-from=- / /b/".split())[1], "stdin_list")
        self.assertEqual(ct.rsync_sizing_argv("rsync -a --write-batch=x a/ /b/".split())[1], "batch_mode")
        self.assertEqual(ct.rsync_sizing_argv("rsync -a --copy-as=me a/ /b/".split())[1], "copy_as")
        self.assertEqual(ct.rsync_sizing_argv("rsync -a /a/".split())[1], "listing")
        self.assertIsNone(ct.rsync_sizing_argv("rsync -a host:/a/ /b/".split())[0])
        with tempfile.TemporaryDirectory() as d:
            os.mkfifo(os.path.join(d, "list"))
            self.assertEqual(ct.rsync_sizing_argv(["rsync", "-a", "--exclude-from=list", "a/", "/b/"], d)[1],
                             "stdin_list")
            self.assertEqual(ct.rsync_sizing_argv(["rsync", "-a", "--files-from", "/dev/stdin", "/", "/b/"], d)[1],
                             "stdin_list")
            with open(os.path.join(d, "real"), "w") as fh:
                fh.write("x\n")
            self.assertEqual(ct.rsync_sizing_argv(["rsync", "-a", "--exclude-from=real", "a/", "/b/"], d)[1], "")


class RsyncStats(unittest.TestCase):
    def test_parses_the_dry_run_block(self):
        s = ct.parse_rsync_stats(STATS_BLOCK)
        self.assertEqual(s, {"files_total": 7, "files_reg": 3, "files_to_transfer": 2,
                             "total_file_size": 9100, "transfer_size": 6100})

    def test_thousand_separators(self):
        text = ("Number of files: 2,148,301 (reg: 1,990,002, dir: 158,299)\n"
                "Number of regular files transferred: 1.234\n"
                "Total file size: 113,004,511,232 bytes\n"
                "Total transferred file size: 65.904.123.000 bytes\n")
        s = ct.parse_rsync_stats(text)
        self.assertEqual((s["files_total"], s["files_reg"], s["files_to_transfer"]), (2148301, 1990002, 1234))
        self.assertEqual((s["total_file_size"], s["transfer_size"]), (113004511232, 65904123000))

    def test_nothing(self):
        self.assertEqual(ct.parse_rsync_stats("rsync error: some files could not be transferred"), {})


class RsyncTempTree(unittest.TestCase):
    """Temporary folders for a real rsync dry run: 3 sources, one file
    excluded, one already at the destination."""

    def setUp(self):
        import shutil
        if not shutil.which("rsync"):
            self.skipTest("rsync is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.src, self.dst, self.run = (os.path.join(t, n) for n in ("src", "dst", "run"))
        for d in (self.run, os.path.join(self.dst, "c")):
            os.makedirs(d)
        files = {"a/f1": 5000, "a/skip.bin": 7777, "b/f2": 1100, "c/f3": 3000}
        for rel, size in files.items():
            os.makedirs(os.path.join(self.src, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(self.src, rel), "wb") as fh:
                fh.write(b"z" * size)
        with open(os.path.join(self.dst, "c", "f3"), "wb") as fh:
            fh.write(b"z" * 3000)
        st = os.stat(os.path.join(self.src, "c", "f3"))
        os.utime(os.path.join(self.dst, "c", "f3"), (st.st_atime, st.st_mtime))
        self.excl = os.path.join(t, "excl.txt")
        with open(self.excl, "w") as fh:
            fh.write("/a/skip.bin\n")
        self.log = os.path.join(t, "copy.log")
        self.args = ["rsync", "-rlt", "--relative", "--partial", "--info=stats1", "--log-file=" + self.log,
                     "--exclude-from=" + self.excl, "./a", "./b", "./c", self.dst + "/"]
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.run})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def snapshot(self):
        out = []
        for base, dirs, names in os.walk(self.dst):
            for n in dirs + names:
                p = os.path.join(base, n)
                out.append((p, os.path.getsize(p) if os.path.isfile(p) else -1))
        return sorted(out)



class SizeJobIntegration(RsyncTempTree):
    def test_dry_run_sizes_what_is_left(self):
        before = self.snapshot()
        argv, reason = ct.rsync_sizing_argv(self.args, self.src)
        self.assertEqual(reason, "")
        key = ct.rsync_size_key(self.src, self.args)
        _job, req = ct._size_paths(key)
        ct._write_json_atomic(req, {"key": key, "mode": "rsync", "cwd": self.src, "argv": argv,
                                    "paths": [], "target_pid": 0, "target_start": 0})
        self.assertEqual(ct.run_size_job(req), 0)
        job = ct.read_size_job(key)
        self.assertEqual(job["state"], "done", job)
        self.assertEqual((job["transfer_size"], job["total_file_size"]), (6100, 9100))
        self.assertEqual((job["found_bytes"], job["found_files"], job["files_to_transfer"]), (6100, 2, 2))
        self.assertEqual(job["moved_at_cross"], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(self.log))

    def test_rsync_total_for_a_live_instance(self):
        # The test process stands in for the transfer (it is alive).
        item = {"pid": os.getpid(), "start": ct.read_start_ticks(os.getpid()), "args": self.args,
                "cwd": self.src, "local_sources": [os.path.join(self.src, x) for x in "abc"]}
        total, kind, source, scan, plan = ct.rsync_total(dict(item))
        self.assertEqual((total, kind), (0, ""))
        self.assertIn(scan["state"], ("queued", "scanning", "done"))
        import time
        deadline = time.time() + 60
        while time.time() < deadline:
            total, kind, source, scan, plan = ct.rsync_total(dict(item))
            if scan["state"] not in ("queued", "scanning"):
                break
            time.sleep(0.2)
        self.assertEqual((kind, source, scan["state"]), ("exact", "rsync_dry_run", "done"))
        # The helper samples this process as "the copy": no receiver, so its
        # tree write_bytes is what had been written when the dry run caught up.
        job = ct.read_size_job(ct.rsync_job_key(self.src, self.args, item["pid"], item["start"]))
        self.assertEqual(total, job["moved_at_cross"] + 6100)
        self.assertEqual((plan["selection_bytes"], plan["to_copy_files"]), (9100, 2))
        self.assertEqual(plan["already_there_bytes"], max(0, 9100 - total))

    def test_one_scan_at_a_time(self):
        import fcntl
        import time
        argv, _ = ct.rsync_sizing_argv(self.args, self.src)
        key = ct.rsync_size_key(self.src, self.args)
        lock = os.open(os.path.join(self.run, ct.SIZING_LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            ct.request_size_job(key, "rsync", self.src, argv, [], 0, 0)
            deadline = time.time() + 30
            while time.time() < deadline and not ct.read_size_job(key)["helper_pid"]:
                time.sleep(0.2)
            time.sleep(1.5)
            job = ct.read_size_job(key)
            self.assertEqual(job["state"], "queued")
            self.assertGreater(job["helper_pid"], 0)
        finally:
            os.close(lock)
        deadline = time.time() + 60
        while time.time() < deadline and ct.read_size_job(key)["state"] in ("queued", "scanning"):
            time.sleep(0.2)
        self.assertEqual(ct.read_size_job(key)["state"], "done")


class HelperNotListed(unittest.TestCase):
    def test_discovery_filter(self):
        argv, _ = ct.rsync_sizing_argv(RSYNC_A, SRC)
        self.assertTrue(ct.discovery_skip("rsync", argv))
        self.assertFalse(ct.discovery_skip("rsync", RSYNC_A))
        self.assertTrue(ct.discovery_skip("rsync", ["rsync", "--server", "--sender", "-vlogDtpre.iLsfxC", "."]))
        self.assertTrue(ct.discovery_skip("pacman", ["pacman", "-Q", "linux"]))
        self.assertFalse(ct.discovery_skip("pacman", ["pacman", "-S", "linux"]))
        self.assertTrue(ct.discovery_skip("rclone", ["rclone", "mount", "g:", "/m"]))
        self.assertFalse(ct.discovery_skip("sha256sum", ["sha256sum", "-c", "MAN"]))


class Overall(unittest.TestCase):
    def samples(self, points):
        out = []
        for t, v in points:
            out = ct.update_samples(out, t, v)
        return out

    def test_exact_gives_an_eta(self):
        s = self.samples([(0, 20e6), (30, 35e6), (60, 50e6)])
        o = ct.compute_overall(50e6, 100e6, "exact", s, "running")
        self.assertEqual((o["pct"], o["beyond"], o["eta_reason"]), (50.0, False, ""))
        self.assertEqual(o["rate"], 500000)
        self.assertEqual(o["eta_sec"], 100)
        self.assertEqual(o["eta"], "1m 40s")

    def test_never_100_while_alive(self):
        s = self.samples([(0, 0), (60, 100e6)])
        self.assertEqual(ct.compute_overall(100e6, 100e6, "exact", s, "running")["pct"], 99.9)
        self.assertEqual(ct.compute_overall(99.99e6, 100e6, "exact", s, "running")["pct"], 99.9)
        self.assertEqual(ct.compute_overall(100e6, 100e6, "exact", s, "running", alive=False)["pct"], 100.0)

    def test_beyond(self):
        s = self.samples([(0, 0), (60, 200e6)])
        o = ct.compute_overall(200e6, 100e6, "exact", s, "running")
        self.assertEqual((o["beyond"], o["eta"], o["eta_reason"], o["pct"]), (True, "", "beyond", 99.9))
        # An exact total allows a little slack (page rounding, the file in flight).
        o = ct.compute_overall(100e6 + 1e6, 100e6, "exact", s, "running")
        self.assertFalse(o["beyond"])
        # An estimate (du, the device) gets more slack: 2% + 16 MiB.
        self.assertFalse(ct.compute_overall(101e6, 100e6, "estimate", s, "running")["beyond"])
        self.assertFalse(ct.compute_overall(0.95e9, 0.94e9, "estimate", s, "running")["beyond"])
        self.assertTrue(ct.compute_overall(120e6, 100e6, "estimate", s, "running")["beyond"])
        self.assertTrue(ct.compute_overall(120e6, 100e6, "upper_bound", s, "running")["beyond"])

    def test_upper_bound_has_no_eta(self):
        s = self.samples([(0, 0), (60, 10e6)])
        o = ct.compute_overall(10e6, 100e6, "upper_bound", s, "running")
        self.assertEqual((o["pct"], o["eta"], o["eta_reason"]), (10.0, "", "upper_bound"))

    def test_reasons(self):
        self.assertEqual(ct.compute_overall(5e6, 100e6, "exact", self.samples([(0, 0), (10, 5e6)]),
                                            "running")["eta_reason"], "measuring")
        self.assertEqual(ct.compute_overall(5e6, 100e6, "exact", self.samples([(0, 0), (60, 5e6)]),
                                            "paused")["eta_reason"], "paused")
        flat = self.samples([(0, 5e6), (30, 5e6), (60, 5e6), (70, 5e6)])
        self.assertEqual(ct.compute_overall(5e6, 100e6, "exact", flat, "running")["eta_reason"], "stalled")
        slow = self.samples([(0, 0), (60, 60e3)])
        o = ct.compute_overall(60e3, 1e12, "exact", slow, "running")
        self.assertEqual(o["eta_reason"], "too_long")
        self.assertGreater(o["eta_sec"], 3 * 86400)
        self.assertEqual(ct.compute_overall(5, 0, "", [], "running")["eta_reason"], "unknown_total")
        self.assertEqual(ct.compute_overall(5, 0, "", [], "running", scanning=True)["eta_reason"], "scanning")

    def test_window_uses_the_last_two_minutes(self):
        # A burst of fast metadata reads at the start does not inflate the rate.
        s = self.samples([(0, 0), (5, 900e6), (200, 1000e6), (260, 1060e6), (320, 1120e6)])
        rate, span = ct.window_rate(s)
        self.assertEqual((round(rate), span), (1000000, 120))

    def test_samples_restart_when_the_counter_goes_back(self):
        s = self.samples([(0, 100), (10, 200), (20, 50)])
        self.assertEqual(s, [[20, 50]])
        s = self.samples([(0, 0), (10, 1), (11, 2), (11.5, 3)])
        self.assertEqual(s, [[0, 0], [10, 1], [11.5, 3]])

    def test_format_duration(self):
        self.assertEqual(ct.format_duration(0), "0s")
        self.assertEqual(ct.format_duration(185), "3m 05s")
        self.assertEqual(ct.format_duration(4 * 86400 + 3 * 3600), "4d 03h")


class Checksum(unittest.TestCase):
    def test_check_and_hash_modes(self):
        ca = ct.checksum_args("sha256sum", ["sha256sum", "-c", "--quiet", "MAN"], "/src")
        self.assertEqual((ca["check"], ca["lists"]), (True, ["/src/MAN"]))
        self.assertEqual(ct.classify_kind("sha256sum", ["sha256sum", "-c", "--quiet", "/m/MAN"]),
                         ("verification", "check"))
        self.assertEqual(ct.classify_kind("sha256sum", ["sha256sum", "-cw", "MAN"]), ("verification", "check"))
        self.assertEqual(ct.classify_kind("b2sum", ["b2sum", "-l", "256", "-c", "x"]), ("verification", "check"))
        self.assertEqual(ct.checksum_args("b2sum", ["b2sum", "-l", "256", "-c", "x"], "/w")["lists"], ["/w/x"])
        self.assertEqual(ct.classify_kind("b2sum", ["b2sum", "-l", "256", "x"]), ("verification", "hash"))
        self.assertEqual(ct.classify_kind("sha256sum", ["sha256sum", "./a", "./b"]), ("verification", "hash"))
        self.assertEqual(ct.checksum_args("md5sum", ["md5sum", "--check"], "")["lists"], ["-"])
        self.assertEqual(ct.classify_kind("cp", ["cp", "-c", "a", "b"]), ("transfer", ""))

    def test_progress_from_the_list_position(self):
        with tempfile.TemporaryDirectory() as d:
            man = os.path.join(d, "MAN")
            with open(man, "w") as fh:
                fh.write("0" * 64 + "  ./file\n" * 1 + "x" * 927)
            big = os.path.join(d, "being-hashed.bin")
            with open(big, "wb") as fh:
                fh.write(b"q" * 50000)
            mf = open(man, "rb")
            bf = open(big, "rb")
            try:
                mf.seek(250)
                bf.seek(20000)
                ca = ct.checksum_args("sha256sum", ["sha256sum", "-c", "MAN"], d)
                # (small_list=0: this 1 kB list stands for a long one.)
                v = ct.verification_progress([os.getpid()], ca, small_list=0)
                self.assertEqual((v["list_pos"], v["list_size"]), (250, 1000))
                self.assertEqual(v["list_fraction"], 0.25)
                f = ct.get_active_file_info([os.getpid()], d, "", exclude_paths={man}, skip_writing=True)
                self.assertEqual((f["path"], f["pos"]), (big, 20000))
            finally:
                mf.close()
                bf.close()

    def test_a_pipe_has_no_fraction(self):
        v = ct.verification_progress([2 ** 31 - 1], {"check": True, "lists": ["-"]})
        self.assertEqual(v["list_fraction"], -1)


class FindBatchSubshell(unittest.TestCase):
    def test_climbs_over_forked_subshells(self):
        procs = {
            900: (800, ["sha256sum", "./x"], DST),
            800: (700, ["xargs", "-0", "sha256sum"], DST),
            700: (29684, COPY_SH, DST),          # ( cd DST && find ... | xargs sha256sum )
            29684: (29670, COPY_SH, SRC),
            29670: (752, GUARD, "/home/user"),
            752: (1, ["/usr/lib/systemd/systemd", "--user"], "/"),
        }
        read = lambda pid: (procs[pid][0], 1000, procs[pid][1], procs[pid][2], pid * 10) if pid in procs else None
        b = ct.find_batch(900, read=read, uid=1000)
        self.assertEqual((b["pid"], b["key"], b["name"]), (29684, "29684-296840", "copy.sh"))
        self.assertEqual(b["cwd"], SRC)
        # Promoted to xargs, the same batch.
        self.assertEqual(ct.find_batch(800, read=read, uid=1000)["pid"], 29684)
        # The rsync of the fixture.
        procs[30073] = (29684, RSYNC_A, SRC)
        self.assertEqual(ct.find_batch(30073, read=read, uid=1000)["pid"], 29684)


class XargsBetweenChildren(unittest.TestCase):
    def test_an_xargs_item_stays_listed_between_invocations(self):
        prev = {"800-8": {"pid": 800, "start_ticks": 8, "promoted": True,
                          "tool_args": ["sha256sum", "./a"]},
                "900-9": {"pid": 900, "start_ticks": 9, "promoted": False, "tool_args": []}}
        alive = lambda pid, start: True
        roots = ct.xargs_between_children(prev, set(), alive, lambda p: "xargs")
        self.assertEqual([(r["pid"], r["tool_pid"], r["comm"], r["promoted"]) for r in roots],
                         [(800, 800, "sha256sum", True)])
        # Found with a live child, or gone: nothing to add.
        self.assertEqual(ct.xargs_between_children(prev, {800}, alive, lambda p: "xargs"), [])
        self.assertEqual(ct.xargs_between_children(prev, set(), lambda p, s: False, lambda p: "xargs"), [])
        self.assertEqual(ct.xargs_between_children(prev, set(), alive, lambda p: "bash"), [])


def prev_record(**kw):
    rec = {"pid": 1, "start_ticks": 1, "timestamp": 0, "total_bytes": 0, "tool": "rsync", "kind": "transfer",
           "verify_mode": "", "source": "/s", "destination": "/d", "elapsed_sec": 100, "elapsed_str": "1m 40s",
           "overall_total_bytes": 0, "overall_total_kind": "", "list_fraction": -1, "state": "running",
           "batch_key": "", "selection_bytes": 0}
    rec.update(kw)
    return rec


def batch_state(**kw):
    b = ct._new_batch_state({"pid": 7, "start_ticks": 70, "name": "copy.sh", "path": "/s/copy.sh"}, 0)
    b.update(kw)
    return b


class FinishVanished(unittest.TestCase):
    def run_it(self, prev, batches=None, alive=lambda b: True, stale=False, live=()):
        return ct.finish_vanished(prev, set(live), batches or {}, alive, 1000.0, stale)

    def test_a_simulation_is_only_a_step(self):
        prev = {"5-5": prev_record(kind="simulation", total_bytes=0, batch_key="7-70")}
        recent, events, batches = self.run_it(prev, {"7-70": batch_state()})
        self.assertEqual((recent, events), ([], []))
        self.assertEqual(batches["7-70"]["steps_done"], 1)
        self.assertEqual(batches["7-70"]["steps"][0]["kind"], "simulation")
        self.assertEqual(batches["7-70"]["done_bytes"], 0)

    def test_a_step_of_a_live_batch_credits_it_and_does_not_notify(self):
        prev = {"5-5": prev_record(total_bytes=40 * ct.MIB, overall_total_bytes=41 * ct.MIB,
                                   overall_total_kind="exact", batch_key="7-70", selection_bytes=99)}
        recent, events, batches = self.run_it(prev, {"7-70": batch_state(done_bytes=10)})
        self.assertEqual(recent[0]["outcome"], "finished")
        self.assertEqual(recent[0]["batch"], "copy.sh")
        self.assertEqual(events[0]["type"], "transfer_finished")
        self.assertFalse(events[0]["notify"])
        # Finished with an exact total: the whole total was copied, not just
        # what the last poll saw.
        self.assertEqual(batches["7-70"]["done_bytes"], 10 + 41 * ct.MIB)
        self.assertEqual(recent[0]["total_bytes"], 41 * ct.MIB)
        self.assertEqual(batches["7-70"]["selection_bytes_done"], 99)

    def test_only_an_exact_finished_transfer_is_rounded_up_to_its_total(self):
        est = prev_record(total_bytes=40 * ct.MIB, overall_total_bytes=41 * ct.MIB, overall_total_kind="estimate")
        recent, _e, _b = self.run_it({"5-5": est})
        self.assertEqual(recent[0]["total_bytes"], 40 * ct.MIB)

    def test_a_standalone_transfer_notifies(self):
        recent, events, _b = self.run_it({"5-5": prev_record(total_bytes=10 * ct.MIB)})
        self.assertEqual((events[0]["notify"], events[0]["id"]), (True, "5-5@1000.0"))
        self.assertEqual(recent[0]["kind"], "transfer")

    def test_still_alive_is_not_finished(self):
        self.assertEqual(self.run_it({"5-5": prev_record(total_bytes=10 * ct.MIB)}, live={"5-5"})[:2], ([], []))

    def test_tiny_quick_transfers_are_not_listed(self):
        self.assertEqual(self.run_it({"5-5": prev_record(total_bytes=1000, elapsed_sec=2)})[:2], ([], []))

    def test_stopped(self):
        short = prev_record(total_bytes=50 * ct.MIB, overall_total_bytes=100 * ct.MIB, overall_total_kind="exact")
        recent, events, _b = self.run_it({"5-5": short})
        self.assertEqual((recent[0]["outcome"], events[0]["type"]), ("stopped", "transfer_stopped"))
        # Paused at the last poll is not enough: the thermal guard resumes it
        # and it may finish within a poll. It is stopped when an estimate also
        # says much was left.
        paused = prev_record(total_bytes=50 * ct.MIB, state="paused")
        self.assertEqual(self.run_it({"5-5": paused})[0][0]["outcome"], "finished")
        paused_est = prev_record(total_bytes=50 * ct.MIB, overall_total_bytes=100 * ct.MIB,
                                 overall_total_kind="estimate", state="paused")
        self.assertEqual(self.run_it({"5-5": paused_est})[0][0]["outcome"], "stopped")
        self.assertEqual(self.run_it({"5-5": dict(paused_est, state="running")})[0][0]["outcome"], "finished")
        nearly = dict(paused_est, total_bytes=99 * ct.MIB)
        self.assertEqual(self.run_it({"5-5": nearly})[0][0]["outcome"], "finished")
        check = prev_record(kind="verification", verify_mode="check", tool="sha256sum", total_bytes=30 * ct.MIB,
                            list_fraction=0.999, list_remaining=0, state="paused")
        self.assertEqual(self.run_it({"5-5": check})[0][0]["outcome"], "finished")
        # Paused at the very end of an exact total: the copy got there.
        done = prev_record(total_bytes=99 * ct.MIB, overall_total_bytes=100 * ct.MIB,
                           overall_total_kind="exact", state="paused")
        self.assertEqual(self.run_it({"5-5": done})[0][0]["outcome"], "finished")
        # An upper bound says nothing about stopping early.
        ub = prev_record(total_bytes=10 * ct.MIB, overall_total_bytes=100 * ct.MIB, overall_total_kind="upper_bound")
        self.assertEqual(self.run_it({"5-5": ub})[0][0]["outcome"], "finished")

    def test_a_tail_faster_than_a_poll_is_not_stopped(self):
        fast = prev_record(total_bytes=80 * ct.MIB, overall_total_bytes=100 * ct.MIB, overall_total_kind="exact",
                           progress_speed=30 * ct.MIB)
        self.assertEqual(self.run_it({"5-5": fast})[0][0]["outcome"], "finished")
        small_list = prev_record(kind="verification", verify_mode="check", tool="sha256sum", total_bytes=30 * ct.MIB,
                                 list_fraction=0.1, list_remaining=40000)
        self.assertEqual(self.run_it({"5-5": small_list})[0][0]["outcome"], "finished")

    def test_verification(self):
        prev = {"5-5": prev_record(kind="verification", verify_mode="check", tool="sha256sum",
                                   total_bytes=30 * ct.MIB, list_fraction=0.5, list_remaining=50 * ct.MIB,
                                   batch_key="7-70"),
                "6-6": prev_record(kind="verification", verify_mode="hash", tool="sha256sum",
                                   total_bytes=80 * ct.MIB, batch_key="7-70")}
        recent, events, batches = self.run_it(prev, {"7-70": batch_state()})
        by_tool = {e["id"].split("@")[0]: e for e in events}
        self.assertEqual(by_tool["5-5"]["type"], "verification_finished")
        self.assertEqual(by_tool["5-5"]["outcome"], "stopped")
        self.assertEqual(by_tool["6-6"]["outcome"], "finished")
        # The hash pass and the check pass usually read the same data: the
        # larger one, not the sum.
        self.assertEqual(batches["7-70"]["verified_bytes"], 80 * ct.MIB)
        self.assertEqual((batches["7-70"]["verified_hash_bytes"], batches["7-70"]["verified_check_bytes"]),
                         (80 * ct.MIB, 30 * ct.MIB))
        self.assertEqual(batches["7-70"]["last_hash_bytes"], 80 * ct.MIB)
        self.assertEqual(batches["7-70"]["done_bytes"], 0)

    def test_stale_state_gives_no_events(self):
        recent, events, _b = self.run_it({"5-5": prev_record(total_bytes=10 * ct.MIB)}, stale=True)
        self.assertEqual(events, [])
        self.assertEqual(len(recent), 1)

    def test_a_batch_that_ended(self):
        prev = {"5-5": prev_record(total_bytes=8 * ct.MIB, batch_key="7-70")}
        b = batch_state(done_bytes=100, last_seen=990.0, elapsed_str="2h 00m")
        recent, events, batches = self.run_it(prev, {"7-70": b}, alive=lambda b: False)
        self.assertEqual(batches, {})
        self.assertEqual([e["type"] for e in events], ["transfer_finished", "batch_finished"])
        self.assertEqual([e["notify"] for e in events], [False, True])
        self.assertEqual(recent[-1]["kind"], "batch")
        self.assertEqual(recent[-1]["total_bytes"], 100 + 8 * ct.MIB)
        # A batch not seen for over an hour goes quietly.
        recent, events, batches = self.run_it({}, {"7-70": batch_state(last_seen=-3000.0)}, alive=lambda b: False)
        self.assertEqual((recent, events, batches), ([], [], {}))


class Summary2(unittest.TestCase):
    def item(self, kind="transfer", moved=0, total=0, tkind="", speed=0, batch=None, plan=None):
        return {"kind": kind, "total_bytes": moved, "overall_total_bytes": total, "overall_total_kind": tkind,
                "active_speed": speed, "batch": dict(batch) if batch else None, "rsync_plan": plan}

    def test_kinds(self):
        s = ct.summarize([self.item(moved=50, total=100, tkind="exact", speed=5),
                          self.item("simulation", speed=0),
                          self.item("verification", moved=900, speed=7)])
        self.assertEqual((s["total_bytes"], s["overall_total_bytes"], s["overall_pct"]), (50, 100, 50.0))
        self.assertEqual((s["transfer_count"], s["check_count"], s["speed"]), (1, 2, 12))
        self.assertEqual(s["overall_total_kind"], "exact")

    def test_an_upper_bound_gives_no_combined_progress(self):
        s = ct.summarize([self.item(moved=50, total=100, tkind="exact"),
                          self.item(moved=10, total=1000, tkind="upper_bound")])
        self.assertEqual((s["overall_total_bytes"], s["overall_pct"]), (0, 0))
        s = ct.summarize([self.item(moved=100, total=100, tkind="exact"),
                          self.item(moved=10, total=10, tkind="estimate")])
        self.assertEqual((s["overall_pct"], s["overall_is_estimate"]), (99.9, True))

    def test_batches(self):
        meta = {"pid": 7, "key": "7-70", "name": "copy.sh", "state": "running"}
        state = {"7-70": dict(batch_state(done_bytes=1000, steps_done=2, selection_bytes_done=5000),
                              key="7-70", state="paused", current_commands=["find"]),
                 "8-80": dict(batch_state(done_bytes=3), pid=8, start_ticks=80, name="other.sh", key="8-80",
                              state="running", current_commands=["find", "sort"])}
        items = [self.item(moved=50, total=100, tkind="exact", speed=4, batch=meta,
                           plan={"selection_bytes": 700}),
                 self.item("simulation", batch=meta)]
        s = ct.summarize(items, state)
        b = {x["key"]: x for x in s["batches"]}
        self.assertEqual(b["7-70"]["total_bytes"], 1050)
        self.assertEqual(b["7-70"]["done_bytes"], 1000)
        self.assertEqual(b["7-70"]["selection_bytes"], 5700)
        self.assertEqual((b["7-70"]["transfers"], b["7-70"]["phase"], b["7-70"]["steps_done"]), (2, "transfer", 2))
        self.assertEqual(items[0]["batch"]["total_bytes"], 1050)
        # A live batch between steps.
        self.assertEqual((b["8-80"]["phase"], b["8-80"]["transfers"], b["8-80"]["total_bytes"]), ("other", 0, 3))
        self.assertEqual(b["8-80"]["current_commands"], ["find", "sort"])

    def test_batch_aggregation_across_steps_that_finish(self):
        # Step 1 ends (credited to the batch); step 2 runs: the batch total is both.
        prev = {"5-5": prev_record(total_bytes=40 * ct.MIB, batch_key="7-70", selection_bytes=60 * ct.MIB)}
        _r, _e, batches = ct.finish_vanished(prev, {"6-6"}, {"7-70": batch_state()}, lambda b: True, 10.0, False)
        live = dict(batches["7-70"], key="7-70", state="running", current_commands=[])
        meta = {"pid": 7, "key": "7-70", "name": "copy.sh"}
        s = ct.summarize([self.item(moved=5 * ct.MIB, batch=meta)], {"7-70": live})
        self.assertEqual(s["batches"][0]["total_bytes"], 45 * ct.MIB)
        self.assertEqual(s["batches"][0]["selection_bytes"], 60 * ct.MIB)
        self.assertEqual(s["batches"][0]["steps_done"], 1)


class StateV2(unittest.TestCase):
    def test_old_and_stale_states(self):
        import json
        import time
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            path = os.path.join(d, ct.STATE_FILE_NAME)
            with open(path, "w") as fh:
                json.dump({"timestamp": time.time(), "processes": {"30073": {}}, "recent_completed": [{"tool": "cp"}]}, fh)
            st = ct.load_previous_state()
            self.assertEqual((st["processes"], st["recent_completed"]), ({}, [{"tool": "cp"}]))
            ct.save_current_state({"1-1": {"x": 1}}, [], {"7-70": {"pid": 7}})
            st = ct.load_previous_state(now=time.time() + 60)
            self.assertTrue(st["stale"])
            self.assertEqual((st["processes"], st["batches"]), ({"1-1": {"x": 1}}, {"7-70": {"pid": 7}}))
            self.assertFalse(ct.load_previous_state()["stale"])


class Elapsed(unittest.TestCase):
    def test_from_start_ticks(self):
        import subprocess
        start = ct.read_start_ticks(os.getpid())
        el = ct.process_elapsed(start)
        ps = int(subprocess.run(["ps", "-o", "etimes=", "-p", str(os.getpid())],
                                capture_output=True, text=True).stdout.strip() or 0)
        self.assertGreaterEqual(el, 1)
        self.assertLessEqual(abs(el - max(1, ps)), 2)
        self.assertTrue(ct.process_alive(os.getpid(), start))
        self.assertFalse(ct.process_alive(os.getpid(), start + 1))



# ---------------------------------------------------------------------------
# Review fixes.

def fake_io(table):
    def io(pid):
        rc, wc, rb, wb = table.get(pid, (0, 0, 0, 0))
        return {"rchar": rc, "wchar": wc, "read_bytes": rb, "write_bytes": wb}
    return io


class NetworkCounters(unittest.TestCase):
    """An ssh child carries every byte the tool pipes to it: summing the tree
    counted a push (or an scp) twice."""

    def test_rsync_push_counts_the_sender_only(self):
        # Measured with a relay standing in for ssh: 3,002,000 B sent.
        io = fake_io({10: (3030724, 3010000, 0, 0), 11: (3002901, 3100000, 0, 0)})
        comm = {10: "rsync", 11: "ssh"}.get
        moved, wire = ct.network_counters("upload", [10, 11], comm, io)
        self.assertEqual((moved, wire), (3030724, 3100000))

    def test_scp_download_and_pull(self):
        # scp writes the file; ssh writes what it received into scp's pipe.
        io = fake_io({20: (900, 5000000, 0, 4096000), 21: (5100000, 5000500, 0, 0)})
        comm = {20: "scp", 21: "ssh"}.get
        self.assertEqual(ct.network_counters("download", [20, 21], comm, io), (5000000, 5100000))

    def test_without_ssh_the_tool_talks_to_the_network(self):
        io = fake_io({30: (700, 400, 1000, 0)})
        self.assertEqual(ct.network_counters("upload", [30], lambda p: "rclone", io), (1000, 400))
        self.assertEqual(ct.network_counters("download", [30], lambda p: "curl", io), (400, 700))


class GroupRoots(unittest.TestCase):
    def comm(self, table):
        return lambda p: table.get(p, "")

    def test_only_checksum_tools_follow_xargs(self):
        cands = {101: {"comm": "rsync", "ppid": 100, "args": ["rsync", "-r", "a", "."]},
                 201: {"comm": "sha256sum", "ppid": 200, "args": ["sha256sum", "./a"]},
                 202: {"comm": "sha256sum", "ppid": 200, "args": ["sha256sum", "./b"]},
                 301: {"comm": "cp", "ppid": 300, "args": ["cp", "x.iso", "/mnt/usb/"]}}
        roots = ct.group_roots(cands, self.comm({100: "xargs", 200: "xargs", 300: "parallel"}))
        got = sorted((r["pid"], r["tool_pid"], r["promoted"]) for r in roots)
        self.assertEqual(got, [(101, 101, False), (200, 201, True), (301, 301, False)])

    def test_child_of_a_candidate_belongs_to_it(self):
        cands = {10: {"comm": "rsync", "ppid": 1, "args": ["rsync", "a", "/b/"]},
                 11: {"comm": "rsync", "ppid": 10, "args": ["rsync", "a", "/b/"]}}
        self.assertEqual([r["pid"] for r in ct.group_roots(cands, lambda p: "")], [10])

    def test_between_children_only_for_checksum_tools(self):
        # A record left by an older collector that had promoted an rsync.
        prev = {"800-8": {"pid": 800, "start_ticks": 8, "promoted": True,
                          "tool_args": ["rsync", "-a", "--relative", "d1", "d2"]}}
        self.assertEqual(ct.xargs_between_children(prev, set(), lambda p, s: True, lambda p: "xargs"), [])


class LocalProgress(unittest.TestCase):
    def test_tar_extract_follows_the_archive(self):
        with tempfile.TemporaryDirectory() as d:
            arc = os.path.join(d, "p.tgz")
            with open(arc, "wb") as fh:
                fh.write(b"a" * 10000)
            with open(arc, "rb") as fh:
                fh.seek(4000)
                moved, counter = ct.local_progress("tar", ["tar", "-xzf", "p.tgz", "-C", "out"], d,
                                                   [os.getpid()], os.getpid())
            self.assertEqual((moved, counter), (4000, "archive_read"))
            # Not open at this instant: unknown (the last value is kept).
            self.assertEqual(ct.local_progress("tar", ["tar", "-xf", "p.tgz"], d, [2 ** 31 - 1], 2 ** 31 - 1),
                             (None, "archive_read"))

    def test_tar_create_and_cp(self):
        io = fake_io({1: (5000, 900, 0, 8192), 2: (7000, 2000, 0, 0)})
        with mock.patch.object(ct, "read_proc_io", io):
            # tar itself read 5000; its compressor (2) read the pipe again.
            self.assertEqual(ct.local_progress("tar", ["tar", "-czf", "a.tgz", "d"], "/w", [1, 2], 1),
                             (5000, "source_read"))
            self.assertEqual(ct.local_progress("cp", ["cp", "-r", "a", "b"], "/w", [1], 1), (900, "logical_write"))
            self.assertEqual(ct.local_progress("dd", ["dd", "if=a", "of=b"], "/w", [1], 1), (None, ""))


class ManifestSizes(unittest.TestCase):
    def test_short_list_is_sized_by_its_entries(self):
        with tempfile.TemporaryDirectory() as d:
            for name, size in (("f1", 1500), ("sub/f 2", 2500), ("a\nb", 7)):
                os.makedirs(os.path.dirname(os.path.join(d, name)) or d, exist_ok=True)
                with open(os.path.join(d, name), "wb") as fh:
                    fh.write(b"x" * size)
            man = os.path.join(d, "SHA256SUMS")
            with open(man, "w") as fh:
                fh.write("ab" * 32 + "  f1\n")
                fh.write("cd" * 32 + " *sub/f 2\n")
                fh.write("\\" + "ef" * 32 + "  a\\nb\n")
                fh.write("SHA256 (missing) = " + "00" * 32 + "\n")
                fh.write("# comment\n")
            self.assertEqual(ct.manifest_total([man], d), 1500 + 2500 + 7)
            self.assertEqual(ct.manifest_total([man], d, max_entries=2), 0)
            self.assertEqual(ct.manifest_total(["-"], d), 0)
            # Read in one go: the position is at the end from the start, so no
            # list fraction (it showed 99.9% while the first file was hashed).
            with open(man, "rb") as fh:
                fh.read()
                v = ct.verification_progress([os.getpid()], {"check": True, "lists": [man]})
            self.assertEqual((v["list_fraction"], v["list_buffered"], v["lists"]), (-1, True, [man]))


class RsyncTotalKind(unittest.TestCase):
    def kind(self, line):
        return ct.rsync_total_kind(ct.parse_rsync_args(line.split()))

    def test_kinds(self):
        self.assertEqual(self.kind("rsync -a a/ /b/"), "exact")
        self.assertEqual(self.kind("rsync -a --inplace a/ /b/"), "exact")  # local: whole files
        self.assertEqual(self.kind("rsync -ac a/ /b/"), "estimate")
        self.assertEqual(self.kind("rsync -aS a/ /b/"), "estimate")
        self.assertEqual(self.kind("rsync -aS --no-sparse a/ /b/"), "exact")
        self.assertEqual(self.kind("rsync -a --inplace --no-W a/ /b/"), "estimate")
        self.assertEqual(self.kind("rsync -a --append a/ /b/"), "exact")


class AppendDryRun(RsyncTempTree):
    """--append writes only what each destination file lacks; the dry run
    counts whole files, so the helper takes off what is already there."""

    def test_append_subtracts_what_is_there(self):
        # b/f2 (1100 B) has 400 B at the destination already.
        os.makedirs(os.path.join(self.dst, "b"))
        with open(os.path.join(self.dst, "b", "f2"), "wb") as fh:
            fh.write(b"z" * 400)
        args = self.args[:1] + ["--append"] + self.args[1:]
        argv, _ = ct.rsync_sizing_argv(args, self.src)
        self.assertEqual(ct.append_destination(argv, self.src), (self.dst, True))
        key = ct.rsync_size_key(self.src, args)
        _job, req = ct._size_paths(key)
        ct._write_json_atomic(req, {"key": key, "mode": "rsync", "cwd": self.src, "argv": argv,
                                    "paths": [], "target_pid": 0, "target_start": 0})
        self.assertEqual(ct.run_size_job(req), 0)
        job = ct.read_size_job(key)
        self.assertEqual(job["state"], "done", job)
        self.assertEqual((job["transfer_size"], job["found_bytes"]), (6100 - 400, 6100 - 400))
        self.assertIsNone(ct.append_destination(ct.rsync_sizing_argv(self.args, self.src)[0], self.src))


class DuFallbackScan(unittest.TestCase):
    def test_a_finished_fallback_is_a_finished_scan(self):
        import time
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            with open(os.path.join(src, "f"), "wb") as fh:
                fh.write(b"x" * 4000)
            item = {"pid": os.getpid(), "start": ct.read_start_ticks(os.getpid()),
                    "args": ["rsync", "-a", src + "/", "host:/backup/"], "cwd": src, "local_sources": [src + "/"]}
            deadline = time.time() + 60
            while time.time() < deadline:
                total, kind, source, scan, _plan = ct.rsync_total(dict(item))
                if total:
                    break
                self.assertIn(scan["state"], ("queued", "scanning"))
                time.sleep(0.1)
            self.assertEqual((kind, source), ("upper_bound", "du"))
            self.assertGreaterEqual(total, 4000)
            self.assertEqual((scan["state"], scan["method"], scan["reason"]), ("done", "du", "remote"))


class SizeJobsPerInstance(unittest.TestCase):
    def test_two_live_copies_do_not_take_turns(self):
        """Same cwd and command line, two live instances: one dry run each,
        not a new one every poll."""
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            spawned = []

            def fake_request(key, mode, cwd, argv, paths, target_pid=0, target_start=0, now=None):
                spawned.append(target_pid)
                job = dict(ct._new_job({"key": key, "mode": mode, "target_pid": target_pid,
                                        "target_start": target_start}, 0), state="done", finished=1,
                           transfer_size=10, moved_at_cross=0)
                ct._write_json_atomic(ct._size_paths(key)[0], job)
                return job

            items = [{"pid": 111, "start": 1, "args": ["rsync", "-a", "/a/", "/b/"], "cwd": "/w",
                      "local_sources": []},
                     {"pid": 222, "start": 2, "args": ["rsync", "-a", "/a/", "/b/"], "cwd": "/w",
                      "local_sources": []}]
            with mock.patch.object(ct, "request_size_job", fake_request):
                for _poll in range(4):
                    for it in items:
                        total, kind, *_ = ct.rsync_total(dict(it), now=100)
            self.assertEqual(spawned, [111, 222])
            self.assertEqual((total, kind), (10, "exact"))
            k1 = ct.rsync_job_key("/w", items[0]["args"], 111, 1)
            self.assertTrue(k1.startswith(ct.rsync_size_key("/w", items[0]["args"]) + "-"))
            self.assertNotEqual(k1, ct.rsync_job_key("/w", items[0]["args"], 222, 2))

    def test_du_is_not_run_again_for_the_same_transfer(self):
        import time
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            key = ct.du_size_key("cp", [src])
            done = dict(ct._new_job({"key": key, "mode": "du", "target_pid": 5, "target_start": 50}, 0),
                        state="done", finished=time.time(), found_bytes=9)
            ct._write_json_atomic(ct._size_paths(key)[0], done)
            later = time.time() + ct.DU_TTL + 60
            with mock.patch.object(ct.os, "posix_spawnp") as spawn:
                self.assertEqual(ct.source_size_job(key, [src], 5, 50, now=later)["state"], "done")
                spawn.assert_not_called()
                # Another transfer past the TTL asks again.
                ct.source_size_job(key, [src], 6, 60, now=later)
                spawn.assert_called_once()

    def test_sticky_totals(self):
        prev = {"sticky_total": {"total": 500, "kind": "estimate", "source": "du", "scan": None}}
        self.assertTrue(ct.sticky_total(prev, "transfer", "mv"))
        self.assertFalse(ct.sticky_total(prev, "verification", "mv"))
        self.assertFalse(ct.sticky_total(prev, "transfer", "curl"))
        self.assertFalse(ct.sticky_total({"sticky_total": None}, "transfer", "mv"))
        self.assertFalse(ct.sticky_total(None, "transfer", "mv"))


class HelperFollowsPauses(unittest.TestCase):
    def test_the_scan_stops_while_the_copy_is_paused(self):
        import subprocess
        import time
        proc = subprocess.Popen(["sh", "-c", "i=0; while [ $i -lt 40 ]; do echo l$i; i=$((i+1)); sleep 0.1; done"],
                                stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
        t0 = time.time()
        seen = {"held_state": "", "lines": 0, "lines_at_hold": None, "lines_at_release": None, "calls": 0}
        saves = []

        def paused():
            t = time.time() - t0
            if 1.5 <= t < 3.5:
                seen["calls"] += 1
                if seen["calls"] >= 2 and not seen["held_state"]:  # the first call stopped it
                    seen["held_state"] = ct.process_state(proc.pid)
                    seen["lines_at_hold"] = seen["lines"]
                return True
            if t >= 3.5 and seen["lines_at_release"] is None:
                seen["lines_at_release"] = seen["lines"]
            return False

        def on_line(line):
            seen["lines"] += 1
            return True

        out, rc, err = ct._drain(proc, lambda **c: saves.append(c), lambda: True, on_line, target_paused=paused)
        self.assertEqual(seen["held_state"], "T")
        self.assertEqual(seen["lines_at_release"], seen["lines_at_hold"])
        self.assertIn({"paused": True}, saves)
        self.assertIn({"paused": False}, saves)
        self.assertIsNone(err)
        self.assertEqual((rc, seen["lines"]), (0, 40))

    def test_held_time_does_not_count_toward_the_timeout(self):
        import subprocess
        import time
        proc = subprocess.Popen(["sleep", "3"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        t0 = time.time()
        out, rc, err = ct._drain(proc, lambda **c: None, lambda: True, lambda line: True,
                                 target_paused=lambda: time.time() - t0 < 2.5, timeout=1.5)
        self.assertIsNone(err)
        self.assertEqual(rc, 0)
        proc = subprocess.Popen(["sleep", "3"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        out, rc, err = ct._drain(proc, lambda **c: None, lambda: True, lambda line: True,
                                 target_paused=lambda: False, timeout=1.5)
        self.assertEqual(err, ("failed", "timeout"))

    def test_a_queued_job_waits_while_the_copy_is_paused(self):
        import signal
        import subprocess
        import time
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            with open(os.path.join(src, "f"), "wb") as fh:
                fh.write(b"x" * 2000)
            target = subprocess.Popen(["sleep", "60"])
            try:
                os.kill(target.pid, signal.SIGSTOP)
                key = ct.du_size_key("cp", [src])
                ct.source_size_job(key, [src], target.pid, ct.read_start_ticks(target.pid))
                # The helper runs at nice 19: on a busy machine it can take a
                # while to start, so wait for it instead of a fixed sleep.
                deadline = time.time() + 30
                while time.time() < deadline and not ct.read_size_job(key)["helper_pid"]:
                    time.sleep(0.2)
                time.sleep(1.5)
                job = ct.read_size_job(key)
                self.assertEqual(job["state"], "queued")
                self.assertGreater(job["helper_pid"], 0)
                os.kill(target.pid, signal.SIGCONT)
                deadline = time.time() + 30
                while time.time() < deadline and ct.read_size_job(key)["state"] in ("queued", "scanning"):
                    time.sleep(0.2)
                self.assertEqual(ct.read_size_job(key)["state"], "done")
            finally:
                target.kill()
                target.wait()


class DuplicateHelpers(unittest.TestCase):
    def test_a_second_helper_leaves_a_finished_job_alone(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            key = "du-dup"
            job_path, req_path = ct._size_paths(key)
            ct._write_json_atomic(req_path, {"key": key, "mode": "du", "cwd": "", "argv": [], "paths": [d],
                                             "target_pid": 5, "target_start": 50})
            done = dict(ct._new_job({"key": key, "mode": "du", "target_pid": 5, "target_start": 50}, 0),
                        state="done", helper_pid=1, found_bytes=77)
            ct._write_json_atomic(job_path, done)
            self.assertEqual(ct.run_size_job(req_path), 0)
            self.assertEqual(ct.read_size_job(key), done)

    def test_collectors_take_turns(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            first = ct.collector_lock()
            self.assertIsNotNone(first)
            try:
                import subprocess
                import sys
                code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('c',sys.argv[1]);"
                        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                        "print(m.collector_lock(timeout=0.3) is None)")
                out = subprocess.run([sys.executable, "-c", code, os.path.join(ROOT, "collect-transfers.py")],
                                     capture_output=True, text=True, env=dict(os.environ)).stdout.strip()
                self.assertEqual(out, "True")
            finally:
                os.close(first)


class NoEmptyBatches(unittest.TestCase):
    def test_a_filtered_curl_leaves_no_batch(self):
        r = {"pid": os.getpid(), "tool_pid": os.getpid(), "args": ["curl", "-s", "https://example.org/api"],
             "promoted": False, "start": ct.read_start_ticks(os.getpid()), "key": "x"}
        batches = {}
        batch = {"pid": 7, "start_ticks": 70, "key": "7-70", "name": "status.sh", "path": "/s/status.sh",
                 "command": "bash status.sh", "cwd": "/s"}
        with mock.patch.object(ct, "network_counters", lambda *a, **k: (0, 0)), \
                mock.patch.object(ct, "process_elapsed", lambda *a, **k: 1), \
                mock.patch.object(ct, "find_batch", mock.Mock(return_value=batch)) as fb:
            item, record = ct._collect_item(r, None, False, batches, 1000.0, set())
        self.assertEqual((item, record, batches), (None, None, {}))
        fb.assert_not_called()

    def test_a_batch_with_no_finished_step_ends_quietly(self):
        b = batch_state(last_seen=990.0)
        recent, events, batches = ct.finish_vanished({}, set(), {"7-70": b}, lambda b: False, 1000.0, False)
        self.assertEqual((recent, events, batches), ([], [], {}))

    def test_live_verification_passes_are_not_added(self):
        meta = {"pid": 7, "key": "7-70", "name": "copy.sh"}
        st = {"7-70": dict(batch_state(verified_hash_bytes=100, verified_check_bytes=0, verified_bytes=100),
                           key="7-70", state="running", current_commands=[])}
        items = [{"kind": "verification", "verify_mode": "check", "total_bytes": 60, "overall_total_bytes": 0,
                  "active_speed": 0, "batch": dict(meta), "rsync_plan": None}]
        b = ct.summarize(items, st)["batches"][0]
        self.assertEqual((b["verified_bytes"], b["verified_hash_bytes"], b["verified_check_bytes"]), (100, 100, 60))


if __name__ == "__main__":
    unittest.main()
