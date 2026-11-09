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
        self.assertEqual(ct.parse_command_details(["cp", "-t", "/dst", "f1"], "/"), ("cp", "f1", "/dst"))


if __name__ == "__main__":
    unittest.main()
