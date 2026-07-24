import os
import tempfile
import time
import unittest
from unittest.mock import patch

from core import webhook


class TestWebhookAsync(unittest.TestCase):

    def test_validate(self):
        # Validate correct Discord webhook URLs
        valid_url = "https://discord.com/api/webhooks/1234567890/token_abc123"
        self.assertTrue(webhook.validate(valid_url)["valid"])

        valid_canary = "https://canary.discord.com/api/webhooks/987654321/token_xyz"
        self.assertTrue(webhook.validate(valid_canary)["valid"])

        # Validate invalid URLs
        self.assertFalse(webhook.validate("")["valid"])
        self.assertFalse(webhook.validate("http://discord.com/api/webhooks/123/token")["valid"])
        self.assertFalse(webhook.validate("https://google.com/api/webhooks/123/token")["valid"])
        self.assertFalse(webhook.validate("https://discord.com/invalid/path")["valid"])

    def test_send_async_enqueue(self):
        # Verify non-blocking asynchronous dispatch
        url = "https://discord.com/api/webhooks/1234567890/token_abc123"
        embed = {"title": "Test Win Notification"}

        with patch("core.webhook._dispatch_request") as mock_dispatch:
            mock_dispatch.return_value = ({"ok": True, "reason": ""}, False, 0.0)

            res = webhook.send(url, embed, content="Victory!", silent=True)
            self.assertEqual(res, {"ok": True, "reason": "queued"})

            # Wait for background worker processing
            time.sleep(0.2)
            self.assertTrue(mock_dispatch.called)

    def test_send_file_caches_bytes_on_deletion(self):
        # Verify screenshot bytes are cached before physical file deletion
        url = "https://discord.com/api/webhooks/1234567890/token_abc123"
        embed = {"title": "Debug Screenshot"}

        # Create temporary image file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
            tmp_path = tmp.name

        try:
            with patch("core.webhook._dispatch_request") as mock_dispatch:
                mock_dispatch.return_value = ({"ok": True, "reason": ""}, False, 0.0)

                # Call send_file and immediately delete the file from disk
                res = webhook.send_file(url, embed, screenshot_path=tmp_path, content="Stuck debug")
                self.assertEqual(res, {"ok": True, "reason": "queued"})

                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

                # Wait for background worker processing and verify byte payload
                time.sleep(0.2)
                self.assertTrue(mock_dispatch.called)

                args, kwargs = mock_dispatch.call_args
                passed_file_bytes = args[2]
                self.assertEqual(passed_file_bytes, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
