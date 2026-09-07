import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from scrapers.registry import source_registry
from web.app import create_app


def make_client(tmp_dir, token="test-token", **kwargs):
    config_path = Path(tmp_dir) / "config.yaml"
    config_path.write_text("mongodb:\n  enable: false\n", encoding="utf-8")
    app = create_app(token=token, config_path=str(config_path), **kwargs)
    return TestClient(app), config_path


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_rejects_missing_or_wrong_token(self):
        client, _ = make_client(self.tmp.name)
        self.assertEqual(401, client.get("/api/status").status_code)
        self.assertEqual(
            401,
            client.get("/api/status", headers={"X-Token": "wrong"}).status_code,
        )

    def test_accepts_valid_token_via_header_and_query(self):
        client, _ = make_client(self.tmp.name)
        self.assertEqual(
            200,
            client.get("/api/status", headers={"X-Token": "test-token"}).status_code,
        )
        self.assertEqual(
            200,
            client.get("/api/status?token=test-token").status_code,
        )

    def test_without_token_rejects_non_loopback_client(self):
        # TestClient 的 client host 是 "testclient"，非回环地址
        client, _ = make_client(self.tmp.name, token="")
        response = client.get("/api/status")
        self.assertEqual(401, response.status_code)
        self.assertIn("token", response.json()["detail"])

    def test_index_page_is_public(self):
        client, _ = make_client(self.tmp.name)
        response = client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn("爬虫管理", response.text)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client, _ = make_client(self.tmp.name)
        self.headers = {"X-Token": "test-token"}

    def test_status_lists_registered_sources(self):
        data = self.client.get("/api/status", headers=self.headers).json()
        names = {source["name"] for source in data["sources"]}
        self.assertEqual(set(source_registry.names()), names)
        for source in data["sources"]:
            self.assertIn("enabled", source)
            self.assertIn("running", source)

    def test_runs_and_failures_empty_without_mongodb(self):
        runs = self.client.get("/api/runs", headers=self.headers).json()
        failures = self.client.get("/api/failures", headers=self.headers).json()
        self.assertEqual([], runs["runs"])
        self.assertEqual([], failures["failures"])


class ConfigEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client, self.config_path = make_client(self.tmp.name)
        self.headers = {"X-Token": "test-token"}

    def test_read_returns_file_content(self):
        data = self.client.get("/api/config", headers=self.headers).json()
        self.assertEqual(str(self.config_path), data["path"])
        self.assertIn("mongodb", data["content"])

    def test_write_validates_yaml(self):
        response = self.client.put(
            "/api/config",
            headers=self.headers,
            json={"content": "mongodb: [broken"},
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("YAML", response.json()["detail"])
        # 原文件未被破坏
        self.assertIn("enable: false", self.config_path.read_text(encoding="utf-8"))

    def test_write_rejects_non_mapping(self):
        response = self.client.put(
            "/api/config",
            headers=self.headers,
            json={"content": "- just\n- a list\n"},
        )
        self.assertEqual(400, response.status_code)

    def test_write_saves_and_backs_up(self):
        new_content = "mongodb:\n  enable: true\n"
        response = self.client.put(
            "/api/config",
            headers=self.headers,
            json={"content": new_content},
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["restart_required"])
        self.assertEqual(
            new_content,
            self.config_path.read_text(encoding="utf-8"),
        )
        backup = self.config_path.with_suffix(".yaml.bak")
        self.assertIn("enable: false", backup.read_text(encoding="utf-8"))


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client, _ = make_client(self.tmp.name)
        self.headers = {"X-Token": "test-token"}

    def test_crawl_rejects_unknown_source(self):
        response = self.client.post(
            "/api/actions/crawl",
            headers=self.headers,
            json={"source": "nope"},
        )
        self.assertEqual(400, response.status_code)

    def test_backfill_validates_page_range(self):
        response = self.client.post(
            "/api/actions/backfill-pages",
            headers=self.headers,
            json={"source": "x1080x", "start_page": 5, "end_page": 2},
        )
        self.assertEqual(400, response.status_code)

    def test_backfill_rejects_unsupported_source(self):
        response = self.client.post(
            "/api/actions/backfill-pages",
            headers=self.headers,
            json={"source": "javbee", "end_page": 3},
        )
        self.assertEqual(400, response.status_code)

    def test_restart_uses_injected_func(self):
        called = threading.Event()
        client, _ = make_client(self.tmp.name, restart_func=called.set)
        response = client.post("/api/actions/restart", headers=self.headers)
        self.assertEqual(200, response.status_code)
        self.assertTrue(called.wait(timeout=3))


class RegistryLockTests(unittest.TestCase):
    def test_run_returns_already_running_when_locked(self):
        import asyncio

        name = source_registry.names()[0]
        lock = source_registry._run_locks[name]
        self.assertTrue(lock.acquire(blocking=False))
        try:
            result = asyncio.run(
                source_registry.run(name, {}, force=True, dry_run=True)
            )
            self.assertEqual("already_running", result["status"])
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
