import json
from pathlib import Path
import tempfile
import unittest

from instagram_pipeline.instagram import (
    AmbiguousPublishError, InstagramConfig, InstagramError, InstagramPublisher,
    _http_request, _journal_lock, refresh_instagram_token,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class InstagramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = Path(self.directory.name) / "reel.mp4"
        self.video.write_bytes(b"test-media-bytes")
        self.journal = Path(self.directory.name) / "publication.json"
        self.config = InstagramConfig("123", "TEST_SECRET", poll_seconds=0, poll_attempts=2)
        self.key = "2026-10-28"

    def publisher(self, responses, config=None):
        transport = FakeTransport(responses)
        return InstagramPublisher(config or self.config, transport=transport, sleeper=lambda _: None), transport

    @staticmethod
    def created(uri="https://rupload.facebook.com/ig-api-upload/v25.0/456"):
        return {"id": "456", "uri": uri}

    def prepare(self, publisher):
        return publisher.prepare_reel(self.video, "Historia comprobada. Recreación con IA.", self.journal, self.key)

    def test_prepare_does_not_publish_then_publish_is_idempotent(self):
        publisher, transport = self.publisher([
            self.created(), {"success": True}, {"status_code": "FINISHED"},
            {"status_code": "FINISHED"}, {"id": "789"},
        ])
        self.assertEqual(self.prepare(publisher)["state"], "ready")
        self.assertFalse(any(url.endswith("media_publish") for _, url, _ in transport.calls))
        result = publisher.publish_prepared(self.journal, self.key)
        self.assertEqual(result["media_id"], "789")
        call_count = len(transport.calls)
        self.assertEqual(publisher.publish_prepared(self.journal, self.key), result)
        self.assertEqual(len(transport.calls), call_count)
        self.assertNotIn("TEST_SECRET", self.journal.read_text())
        self.assertNotIn("TEST_SECRET", repr(self.config))

    def test_lost_publish_reply_never_blindly_retries(self):
        publisher, transport = self.publisher([
            self.created(), {"success": True}, {"status_code": "FINISHED"},
            {"status_code": "FINISHED"}, InstagramError("timeout"),
            {"status_code": "FINISHED"}, {"status_code": "PUBLISHED"},
        ])
        self.prepare(publisher)
        with self.assertRaises(InstagramError):
            publisher.publish_prepared(self.journal, self.key)
        self.assertEqual(json.loads(self.journal.read_text())["state"], "publish_requested")
        with self.assertRaises(AmbiguousPublishError):
            publisher.publish_prepared(self.journal, self.key)
        recovered = publisher.publish_prepared(self.journal, self.key)
        self.assertEqual(recovered["state"], "published")
        self.assertTrue(recovered["reconciled"])
        self.assertEqual(sum(url.endswith("media_publish") for _, url, _ in transport.calls), 1)

    def test_processing_timeout_resumes_existing_container(self):
        publisher, transport = self.publisher([
            self.created(), {"success": True}, {"status_code": "IN_PROGRESS"},
            {"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"},
        ])
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        self.assertEqual(self.prepare(publisher)["state"], "ready")
        self.assertEqual(sum(url.endswith("/media") for _, url, _ in transport.calls), 1)

    def test_media_change_is_rejected(self):
        publisher, transport = self.publisher([self.created(), {"success": True}, {"status_code": "FINISHED"}])
        self.prepare(publisher)
        self.video.write_bytes(b"different-video")
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        self.assertEqual(len(transport.calls), 3)

    def test_account_mismatch_is_rejected_before_network(self):
        publisher, _ = self.publisher([self.created(), {"success": True}, {"status_code": "FINISHED"}])
        self.prepare(publisher)
        wrong, transport = self.publisher([], InstagramConfig("999", "TEST_SECRET"))
        with self.assertRaises(InstagramError):
            wrong.publish_prepared(self.journal, self.key)
        self.assertEqual(transport.calls, [])

    def test_expected_username_mismatch_prevents_creation(self):
        config = InstagramConfig("123", "TEST_SECRET", expected_username="historyfacts")
        publisher, transport = self.publisher([{"id": "123", "username": "someone_else"}], config)
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertFalse(self.journal.exists())

    def test_expected_username_verified_before_upload(self):
        config = InstagramConfig("123", "TEST_SECRET", expected_username="historyfacts")
        publisher, transport = self.publisher([
            {"id": "123", "username": "HistoryFacts"}, self.created(),
            {"success": True}, {"status_code": "FINISHED"},
        ], config)
        self.assertEqual(self.prepare(publisher)["state"], "ready")
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertEqual(transport.calls[0][2]["fields"]["fields"], "id,username")

    def test_untrusted_upload_uri_never_receives_token(self):
        publisher, transport = self.publisher([self.created("https://rupload.facebook.com.evil.test/ig-api-upload/456")])
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        self.assertEqual(len(transport.calls), 1)

    def test_instagram_login_requires_public_url_before_mutation(self):
        publisher, transport = self.publisher([], InstagramConfig("123", "TEST_SECRET", login_mode="instagram"))
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        self.assertFalse(self.journal.exists())
        self.assertEqual(transport.calls, [])

    def test_instagram_login_uses_public_url_not_rupload(self):
        config = InstagramConfig("123", "TEST_SECRET", login_mode="instagram", public_video_url="https://example.org/reel.mp4")
        publisher, transport = self.publisher([{"id": "456"}, {"status_code": "FINISHED"}], config)
        self.assertEqual(self.prepare(publisher)["state"], "ready")
        fields = transport.calls[0][2]["fields"]
        self.assertEqual(fields["video_url"], "https://example.org/reel.mp4")
        self.assertNotIn("upload_type", fields)
        self.assertTrue(all("graph.instagram.com" in url for _, url, _ in transport.calls))

    def test_failed_processing_is_never_published(self):
        publisher, transport = self.publisher([self.created(), {"success": True}, {"status_code": "ERROR"}])
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        with self.assertRaises(InstagramError):
            publisher.publish_prepared(self.journal, self.key)
        self.assertFalse(any(url.endswith("media_publish") for _, url, _ in transport.calls))

    def test_expired_container_is_not_published(self):
        publisher, transport = self.publisher([self.created(), {"success": True}, {"status_code": "FINISHED"}, {"status_code": "EXPIRED"}])
        self.prepare(publisher)
        with self.assertRaises(InstagramError):
            publisher.publish_prepared(self.journal, self.key)
        self.assertFalse(any(url.endswith("media_publish") for _, url, _ in transport.calls))

    def test_connection_loss_creating_container_is_not_blindly_retried(self):
        publisher, transport = self.publisher([InstagramError("timeout")])
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        with self.assertRaises(InstagramError):
            self.prepare(publisher)
        self.assertEqual(len(transport.calls), 1)

    def test_other_process_cannot_use_locked_journal(self):
        with _journal_lock(self.journal):
            with self.assertRaises(InstagramError):
                with _journal_lock(self.journal):
                    self.fail("A second publisher acquired the lock")

    def test_http_transport_rejects_non_meta_hosts(self):
        with self.assertRaises(InstagramError):
            _http_request("POST", "https://example.org/", headers={"Authorization": "secret"})

    def test_refresh_returns_expiry_and_replacement_without_printing(self):
        fake = FakeTransport([{"access_token": "REPLACEMENT", "expires_in": 5184000}])
        refreshed = refresh_instagram_token("OLD", transport=fake)
        self.assertEqual(refreshed["access_token"], "REPLACEMENT")
        self.assertGreater(refreshed["expires_at"], 5184000)

    def test_failed_remote_checkpoint_prevents_publish_request(self):
        publisher, transport = self.publisher([
            self.created(), {"success": True}, {"status_code": "FINISHED"},
            {"status_code": "FINISHED"},
        ])
        self.prepare(publisher)
        def checkpoint(record):
            if record["state"] == "publish_requested":
                raise OSError("Remote durable store unavailable")
        publisher.checkpoint = checkpoint
        with self.assertRaises(OSError):
            publisher.publish_prepared(self.journal, self.key)
        self.assertFalse(any(url.endswith("media_publish") for _, url, _ in transport.calls))

    def test_checkpoint_precedes_each_mutation_and_contains_no_credentials(self):
        snapshots = []
        publisher, transport = self.publisher([
            self.created(), {"success": True}, {"status_code": "FINISHED"},
            {"status_code": "FINISHED"}, {"id": "789"},
        ])
        publisher.checkpoint = lambda record: snapshots.append((len(transport.calls), dict(record)))
        self.prepare(publisher)
        publisher.publish_prepared(self.journal, self.key)
        checkpoints = {record["state"]: count for count, record in snapshots}
        self.assertEqual(checkpoints["create_requested"], 0)
        self.assertEqual(checkpoints["upload_requested"], 1)
        self.assertEqual(checkpoints["publish_requested"], 4)
        self.assertNotIn("TEST_SECRET", json.dumps(snapshots))
        self.assertNotIn("rupload", json.dumps(snapshots))


if __name__ == "__main__":
    unittest.main()
