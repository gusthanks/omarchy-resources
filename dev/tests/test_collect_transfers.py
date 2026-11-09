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
        script = open(os.path.join(ROOT, "collect-details.sh")).read()
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
    def test_du_runs_in_the_background_and_is_read_later(self):
        import time
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            with open(os.path.join(src, "f"), "wb") as fh:
                fh.write(b"x" * 5000)
            self.assertEqual(ct.source_size("1-2", [src]), (0, True))
            size = 0
            for _ in range(100):
                size, scanning = ct.source_size("1-2", [src])
                if not scanning:
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(size, 5000)
            ct.forget_source_sizes({"9-9"})
            self.assertEqual([n for n in os.listdir(d) if n.startswith(ct.SIZE_FILE_PREFIX)], [])

    def test_nothing_to_size(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            self.assertEqual(ct.source_size("1-2", ["/nonexistent/x"]), (0, False))


if __name__ == "__main__":
    unittest.main()
