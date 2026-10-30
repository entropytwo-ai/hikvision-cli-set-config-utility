"""Offline tests against the mock ISAPI server.  Run:  python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hik_config  # noqa: E402
import mock_camera  # noqa: E402
from modules import discovery  # noqa: E402
from modules.camera_loader import (CameraFileError, expand_ip_range, load_cameras,  # noqa: E402
                                   write_camera_file)
from modules.motion import count_active_cells, grid_map  # noqa: E402
from modules.time_date import build_timezone, parse_timezone  # noqa: E402

PW = "Passw0rd!"


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = hik_config.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()


class MockCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = mock_camera.serve(18181, "digest")
        cls.basic = mock_camera.serve(18182, "basic")
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.digest.shutdown()
        cls.basic.shutdown()

    def base(self, port=18181, password=PW):
        return ["--ip", "127.0.0.1", "--port", str(port), "-u", "admin", "-p", password,
                "--no-log-file", "--no-color", "-y"]

    def store(self, path):
        return self.digest.RequestHandlerClass.store[path]

    def test_get_info_digest_and_basic_fallback(self):
        for port in (18181, 18182):
            code, out = run("get-info", *self.base(port))
            self.assertEqual(code, 0, out)
            self.assertIn("DS-2CD2143G2-I", out)
            self.assertIn("✓ Device info retrieved successfully", out)

    def test_set_ntp_switches_mode_and_server(self):
        code, out = run("set-ntp", "--server", "10.0.0.5", "--interval", "30", *self.base())
        self.assertEqual(code, 0, out)
        self.assertIn("<timeMode>NTP</timeMode>", self.store("/ISAPI/System/time"))
        self.assertIn("<ipAddress>10.0.0.5</ipAddress>", self.store("/ISAPI/System/time/ntpServers/1"))
        self.assertIn("NTP server set to: 10.0.0.5:123", out)

    def test_bad_password_stops_after_one_attempt(self):
        code, out = run("get-all", *self.base(password="nope"))
        self.assertEqual(code, 1)
        self.assertEqual(out.count("401 Unauthorized"), 1)
        self.assertIn("Remaining checks skipped", out)

    def test_dry_run_does_not_write(self):
        before = self.store("/ISAPI/System/deviceInfo")
        code, out = run("set-device-name", "--name", "X", "--dry-run", *self.base())
        self.assertEqual(code, 0, out)
        self.assertIn("[DRY-RUN]", out)
        self.assertEqual(before, self.store("/ISAPI/System/deviceInfo"))

    def test_set_video_and_motion(self):
        code, out = run("set-video", "--codec", "h265", "--fps", "15", *self.base())
        self.assertEqual(code, 0, out)
        stored = self.store("/ISAPI/Streaming/channels/101")
        self.assertIn("H.265", stored)
        self.assertIn("<maxFrameRate>1500</maxFrameRate>", stored)
        code, out = run("set-motion", "--enable", "on", "--sensitivity", "70", *self.base())
        self.assertEqual(code, 0, out)
        self.assertIn("<sensitivityLevel>70</sensitivityLevel>",
                      self.store("/ISAPI/System/Video/inputs/channels/1/motionDetection"))

    def test_unreachable_camera_and_json_out(self):
        path = os.path.join(self.tmp, "cams.json")
        with open(path, "w") as fh:
            json.dump([{"ip": "127.0.0.1", "port": 18181, "username": "admin", "password": PW},
                       {"ip": "127.0.0.1", "port": 18199, "username": "admin", "password": PW}], fh)
        result = os.path.join(self.tmp, "out.json")
        code, out = run("get-time", "-c", path, "--no-log-file", "--no-color", "--json-out", result,
                        "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("Connection refused", out)
        data = json.load(open(result))
        self.assertEqual((data["total"], data["successes"], data["failures"]), (2, 1, 1))

    def test_set_without_options_is_rejected(self):
        code, _ = run("set-video", *self.base())
        self.assertEqual(code, 2)

    def test_restore_defaults_requires_confirm(self):
        code, _ = run("restore-defaults", *self.base())
        self.assertNotEqual(code, 0)

    # ── scan / discovery ──────────────────────────────────────────────────────
    def test_scan_needs_auth_without_credentials(self):
        code, out = run("scan", "--ip", "127.0.0.1", "--ports", "18181,18182",
                        "--no-color", "--no-log-file", "--timeout", "2")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("authentication required"), 2)
        self.assertIn("Cameras found     : 2", out)
        self.assertIn("need login     : 2", out)

    def test_scan_authenticated_reads_model(self):
        code, out = run("scan", "--ip", "127.0.0.1", "--ports", "18181,18182",
                        "-u", "admin", "-p", PW, "--no-color", "--no-log-file", "--timeout", "2")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("DS-2CD2143G2-I"), 2)
        self.assertIn("readable       : 2", out)

    def test_scan_skips_closed_ports_and_writes_file(self):
        for ext in ("yaml", "json", "csv"):
            path = os.path.join(self.tmp, f"found.{ext}")
            # 18199 is closed -> must not appear as a camera
            code, out = run("scan", "--ip", "127.0.0.1", "--ports", "18181,18199",
                            "-u", "admin", "-p", PW, "-o", path,
                            "--no-color", "--no-log-file", "--timeout", "2")
            self.assertEqual(code, 0, out)
            cams = load_cameras(path)
            self.assertEqual([c.effective_port for c in cams], [18181], ext)
            self.assertEqual(cams[0].extra.get("model"), "DS-2CD2143G2-I", ext)

    def test_scan_nothing_to_scan_is_error(self):
        code, _ = run("scan", "--no-color", "--no-log-file")
        self.assertEqual(code, 2)


class DiscoveryHelperTests(unittest.TestCase):
    def test_parse_ports(self):
        self.assertEqual(discovery.parse_ports("80"), [80])
        self.assertEqual(discovery.parse_ports("80,443,80"), [80, 443])
        self.assertEqual(discovery.parse_ports("8000-8002,80"), [80, 8000, 8001, 8002])
        with self.assertRaises(ValueError):
            discovery.parse_ports("70000")
        with self.assertRaises(ValueError):
            discovery.parse_ports("20-10")

    def test_expand_targets(self):
        self.assertEqual(discovery.expand_targets(["10.0.0.5"]), ["10.0.0.5"])
        self.assertEqual(discovery.expand_targets(["10.0.0.1-3"]),
                         ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        self.assertEqual(len(discovery.expand_targets(["10.0.0.0/30"])), 2)   # usable hosts
        self.assertEqual(discovery.expand_targets(["10.0.0.9/32"]), ["10.0.0.9"])
        # de-duplication across overlapping specs
        self.assertEqual(discovery.expand_targets(["10.0.0.1", "10.0.0.1-2"]),
                         ["10.0.0.1", "10.0.0.2"])

    def test_devices_to_records_filters_non_cameras(self):
        cam = discovery.DiscoveredDevice(ip="10.0.0.5", port=80, is_camera=True,
                                         authenticated=True, model="DS-X", name="Cam")
        web = discovery.DiscoveredDevice(ip="10.0.0.6", port=80, tcp_open=True, is_camera=False)
        recs = discovery.devices_to_records([cam, web], username="admin")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["ip"], "10.0.0.5")
        self.assertEqual(recs[0]["username"], "admin")
        self.assertEqual(recs[0]["model"], "DS-X")
        self.assertNotIn("password", recs[0])

    def test_write_camera_file_roundtrip_all_formats(self):
        tmp = tempfile.mkdtemp()
        recs = [{"ip": "10.0.0.5", "port": 8080, "username": "admin", "name": "Cam",
                 "model": "DS-X"},
                {"ip": "10.0.0.6", "https": True, "model": "DS-Y"}]
        for ext in ("csv", "yaml", "json"):
            path = os.path.join(tmp, f"c.{ext}")
            write_camera_file(path, recs, header_comment="test")
            cams = load_cameras(path)
            self.assertEqual([c.ip for c in cams], ["10.0.0.5", "10.0.0.6"], ext)
            self.assertEqual(cams[0].effective_port, 8080, ext)
            self.assertTrue(cams[1].https, ext)
            self.assertEqual(cams[0].extra.get("model"), "DS-X", ext)

    def test_sadp_parse(self):
        xml = (b'<?xml version="1.0" encoding="utf-8"?>'
               b'<ProbeMatch><IPv4Address>192.168.1.64</IPv4Address><HttpPort>80</HttpPort>'
               b'<DeviceDescription>IPC</DeviceDescription><DeviceType>IPCamera</DeviceType>'
               b'<DeviceSN>DS-2CD2143G2-I20230101</DeviceSN><SoftwareVersion>V5.7.3</SoftwareVersion>'
               b'<MAC>bc:ba:c2:00:00:01</MAC></ProbeMatch>')
        dev = discovery._sadp_parse(xml)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.ip, "192.168.1.64")
        self.assertEqual(dev.port, 80)
        self.assertEqual(dev.serial, "DS-2CD2143G2-I20230101")
        self.assertTrue(dev.is_camera)
        self.assertIsNone(discovery._sadp_parse(b"not xml"))


class HelperTests(unittest.TestCase):
    def test_loaders(self):
        tmp = tempfile.mkdtemp()
        files = {
            "c.csv": "ip,username,password,port\n# comment\n10.0.0.1,admin,pw,8080\n10.0.0.2,,,\n",
            "c.yaml": "- ip: 10.0.0.1\n  username: admin\n  password: pw\n  port: 8080\n- ip: 10.0.0.2\n",
            "c.json": '{"cameras": [{"ip": "10.0.0.1", "username": "admin", "password": "pw", "port": 8080},'
                      ' {"ip": "10.0.0.2"}]}',
        }
        for name, content in files.items():
            path = os.path.join(tmp, name)
            with open(path, "w") as fh:
                fh.write(content)
            cams = load_cameras(path)
            self.assertEqual([c.ip for c in cams], ["10.0.0.1", "10.0.0.2"], name)
            self.assertEqual(cams[0].effective_port, 8080)
            self.assertEqual(cams[1].effective_port, 80)
            self.assertIsNone(cams[1].username)
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w") as fh:
            fh.write('[{"username": "x"}]')
        with self.assertRaises(CameraFileError):
            load_cameras(bad)

    def test_ip_range(self):
        self.assertEqual(expand_ip_range("10.0.0.1-3"), ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        self.assertEqual(len(expand_ip_range("10.0.0.0/30")), 2)

    def test_timezone(self):
        tz = "EST5:00:00EDT,M3.2.0/02:00:00,M11.1.0/02:00:00"
        self.assertEqual(parse_timezone(tz)["utc_offset"], "UTC-05:00")
        self.assertTrue(parse_timezone(tz)["dst_enabled"])
        self.assertEqual(build_timezone(tz, dst=False), "EST5:00:00")
        self.assertEqual(parse_timezone("CST-8:00:00")["utc_offset"], "UTC+08:00")
        self.assertEqual(build_timezone("EST5:00:00", dst=True, dst_name="EDT"), tz)

    def test_grid_map(self):
        full = grid_map(18, 22, True)
        self.assertEqual(full[:6], "fffffc")
        self.assertEqual(count_active_cells(full, 18, 22), 396)
        self.assertEqual(count_active_cells(grid_map(18, 22, False), 18, 22), 0)


if __name__ == "__main__":
    unittest.main()
