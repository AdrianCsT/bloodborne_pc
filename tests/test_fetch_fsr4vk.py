"""tools/fetch_fsr4vk.py: pinned download, resume, hash checks (a local server, synthetic zip)."""
from paths import ROOT
import hashlib
import http.server
import importlib.util
import io
import tempfile
import os
import threading
import unittest
from unittest import mock
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("fetch_fsr4vk", ROOT / "tools/fetch_fsr4vk.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)

MEMBERS = {
    "OptiScaler/amd_fidelityfx_upscaler_vk.dll": b"MZ" + bytes(range(256)) * 40,
    "LICENSES/GPL-3.0.txt": b"gpl",
    "LICENSES/AMD-FidelityFX-SDK-MIT.md": b"mit",
    "LICENSES/Zstandard-BSD.txt": b"bsd",
}


def make_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class Server(http.server.ThreadingHTTPServer):
    """Serves one body and honours Range; `cut_after` ends the first full response early."""

    def __init__(self, body, cut_after=None):
        super().__init__(("127.0.0.1", 0), Handler)
        self.body, self.cut_after, self.ranges = body, cut_after, []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body, start = self.server.body, 0
        header = self.headers.get("Range")
        self.server.ranges.append(header)
        if header:
            start = int(header.split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        data = body[start:]
        if self.server.cut_after is not None and not header:
            data = data[:self.server.cut_after]
            self.server.cut_after = None
        self.wfile.write(data)


class FetchFsr4VkTest(unittest.TestCase):
    def patch(self, **values):
        """Patches module pins for this test only; they are restored when it ends."""
        for name, value in values.items():
            patcher = mock.patch.object(fetch, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def pin(self, body, members, port, **overrides):
        """Points the module's pins at a local server and a synthetic zip."""
        values = dict(
            ZIP_URL=f"http://127.0.0.1:{port}/fsr4vk.zip",
            ZIP_SIZE=len(body),
            ZIP_SHA256=hashlib.sha256(body).hexdigest(),
            FILES=tuple(
                (name, name.split("/", 1)[1] if name.startswith("OptiScaler/") else name, len(data),
                 hashlib.sha256(data).hexdigest())
                for name, data in members.items()))
        values.update(overrides)
        self.patch(**values)

    def serve(self, body, cut_after=None):
        server = Server(body, cut_after)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server

    def test_download_extracts_and_verifies(self):
        body = make_zip(MEMBERS)
        server = self.serve(body)
        self.pin(body, MEMBERS, server.server_address[1])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "fsr4vk"
            self.assertFalse(fetch.files_ok(target))
            fetch.fetch(target)
            self.assertTrue(fetch.files_ok(target))
            self.assertEqual((target / "amd_fidelityfx_upscaler_vk.dll").read_bytes(),
                             MEMBERS["OptiScaler/amd_fidelityfx_upscaler_vk.dll"])
            self.assertIn("github.com/dvj5411/fsr4vk", (target / "SOURCE.txt").read_text())
            self.assertFalse((target / ".download").exists())
            requests = len(server.ranges)
            fetch.fetch(target)  # verified files: no second download
            self.assertEqual(len(server.ranges), requests)

    def test_resumes_an_interrupted_download(self):
        body = make_zip(MEMBERS)
        server = self.serve(body, cut_after=len(body) // 3)
        self.pin(body, MEMBERS, server.server_address[1])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            fetch.fetch(target)
            self.assertTrue(fetch.files_ok(target))
            self.assertEqual(server.ranges[0], None)
            self.assertEqual(server.ranges[1], f"bytes={len(body) // 3}-")

    def test_wrong_zip_is_rejected_and_deleted(self):
        body = make_zip(MEMBERS)
        server = self.serve(body)
        self.pin(body, MEMBERS, server.server_address[1], ZIP_SHA256="0" * 64)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(fetch.FetchError):
                fetch.fetch(Path(folder))
            self.assertFalse(list(Path(folder).rglob("*.part")))
            self.assertFalse(fetch.files_ok(Path(folder)))

    def test_changed_member_is_rejected(self):
        body = make_zip(MEMBERS)
        server = self.serve(body)
        self.pin(body, MEMBERS, server.server_address[1])
        name = "OptiScaler/amd_fidelityfx_upscaler_vk.dll"
        self.patch(FILES=tuple((n, d, size, "f" * 64 if n == name else sha) for n, d, size, sha in fetch.FILES))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(fetch.FetchError):
                fetch.fetch(Path(folder))
            self.assertFalse((Path(folder) / "amd_fidelityfx_upscaler_vk.dll").exists())

    def test_pins_are_the_published_release(self):
        self.assertEqual(fetch.ZIP_SHA256, "3dd2fe7a6b14a1d045c23aa51b63cb45a53576d64eba5f96cb0e334e55ce9827")
        self.assertEqual(len(fetch.FILES[0][3]), 64)
        self.assertTrue(fetch.ZIP_URL.startswith("https://github.com/dvj5411/fsr4vk/releases/download/"))
        self.assertEqual(fetch.ZIP_URL, f"https://github.com/dvj5411/fsr4vk/releases/download/{fetch.RELEASE}/{fetch.ZIP_NAME}")
        self.assertEqual(fetch.ZIP_NAME, f"fsr4vk-{fetch.RELEASE}.zip")

    def test_changed_pins_do_not_leak_between_tests(self):
        self.assertEqual(fetch.ZIP_SIZE, 20452070)
        self.assertEqual(len(fetch.FILES), 4)

    def test_default_folder_is_next_to_the_executable(self):
        """The runtime (Fsr4Vk::Directory) and bb-gpu-capabilities look in fsr4vk beside bb-probe.exe: bin/
        in a package, else out/ (run.py's find_executable)."""
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ):
            os.environ.pop("BB_FSR4VK_DIR", None)
            port = Path(folder)
            self.assertEqual(fetch.default_dir(port), port / "out" / "fsr4vk")
            (port / "out").mkdir()
            (port / "out" / "bb-probe.exe").write_bytes(b"")
            self.assertEqual(fetch.default_dir(port), port / "out" / "fsr4vk")
            (port / "bin").mkdir()
            (port / "bin" / "bb-probe.exe").write_bytes(b"")
            self.assertEqual(fetch.default_dir(port), port / "bin" / "fsr4vk")
            os.environ["BB_FSR4VK_DIR"] = str(port / "elsewhere")
            self.assertEqual(fetch.default_dir(port), port / "elsewhere")


if __name__ == "__main__":
    unittest.main()
