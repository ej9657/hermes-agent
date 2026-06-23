"""
Tests for document cache utilities in gateway/platforms/base.py.

Covers: get_document_cache_dir, cache_document_from_bytes,
        cleanup_document_cache, SUPPORTED_DOCUMENT_TYPES.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import (
    INLINE_TEXT_DOCUMENT_EXTENSIONS,
    SUPPORTED_DOCUMENT_TYPES,
    cache_document_from_bytes,
    cleanup_document_cache,
    get_document_cache_dir,
)
from gateway.platforms.telegram import TelegramAdapter

# ---------------------------------------------------------------------------
# Fixture: redirect DOCUMENT_CACHE_DIR to a temp directory for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path, monkeypatch):
    """Point the module-level DOCUMENT_CACHE_DIR to a fresh tmp_path."""
    monkeypatch.setattr(
        "gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path / "doc_cache"
    )


# ---------------------------------------------------------------------------
# TestGetDocumentCacheDir
# ---------------------------------------------------------------------------

class TestGetDocumentCacheDir:
    def test_creates_directory(self, tmp_path):
        cache_dir = get_document_cache_dir()
        assert cache_dir.exists()
        assert cache_dir.is_dir()

    def test_returns_existing_directory(self):
        first = get_document_cache_dir()
        second = get_document_cache_dir()
        assert first == second
        assert first.exists()


# ---------------------------------------------------------------------------
# TestCacheDocumentFromBytes
# ---------------------------------------------------------------------------

class TestCacheDocumentFromBytes:
    def test_basic_caching(self):
        data = b"hello world"
        path = cache_document_from_bytes(data, "test.txt")
        assert os.path.exists(path)
        assert Path(path).read_bytes() == data

    def test_filename_preserved_in_path(self):
        path = cache_document_from_bytes(b"data", "report.pdf")
        assert "report.pdf" in os.path.basename(path)

    def test_empty_filename_uses_fallback(self):
        path = cache_document_from_bytes(b"data", "")
        assert "document" in os.path.basename(path)

    def test_unique_filenames(self):
        p1 = cache_document_from_bytes(b"a", "same.txt")
        p2 = cache_document_from_bytes(b"b", "same.txt")
        assert p1 != p2

    def test_path_traversal_blocked(self):
        """Malicious directory components are stripped — only the leaf name survives."""
        path = cache_document_from_bytes(b"data", "../../etc/passwd")
        basename = os.path.basename(path)
        assert "passwd" in basename
        # Must NOT contain directory separators
        assert ".." not in basename
        # File must reside inside the cache directory
        cache_dir = get_document_cache_dir()
        assert Path(path).resolve().is_relative_to(cache_dir.resolve())

    def test_null_bytes_stripped(self):
        path = cache_document_from_bytes(b"data", "file\x00.pdf")
        basename = os.path.basename(path)
        assert "\x00" not in basename
        assert "file.pdf" in basename

    def test_dot_dot_filename_handled(self):
        """A filename that is literally '..' falls back to 'document'."""
        path = cache_document_from_bytes(b"data", "..")
        basename = os.path.basename(path)
        assert "document" in basename

    def test_none_filename_uses_fallback(self):
        path = cache_document_from_bytes(b"data", None)
        assert "document" in os.path.basename(path)


# ---------------------------------------------------------------------------
# TestCleanupDocumentCache
# ---------------------------------------------------------------------------

class TestCleanupDocumentCache:
    def test_removes_old_files(self, tmp_path):
        cache_dir = get_document_cache_dir()
        old_file = cache_dir / "old.txt"
        old_file.write_text("old")
        # Set modification time to 48 hours ago
        old_mtime = time.time() - 48 * 3600
        os.utime(old_file, (old_mtime, old_mtime))

        removed = cleanup_document_cache(max_age_hours=24)
        assert removed == 1
        assert not old_file.exists()

    def test_keeps_recent_files(self):
        cache_dir = get_document_cache_dir()
        recent = cache_dir / "recent.txt"
        recent.write_text("fresh")

        removed = cleanup_document_cache(max_age_hours=24)
        assert removed == 0
        assert recent.exists()

    def test_returns_removed_count(self):
        cache_dir = get_document_cache_dir()
        old_time = time.time() - 48 * 3600
        for i in range(3):
            f = cache_dir / f"old_{i}.txt"
            f.write_text("x")
            os.utime(f, (old_time, old_time))

        assert cleanup_document_cache(max_age_hours=24) == 3

    def test_empty_cache_dir(self):
        assert cleanup_document_cache(max_age_hours=24) == 0


# ---------------------------------------------------------------------------
# TestSupportedDocumentTypes
# ---------------------------------------------------------------------------

class TestSupportedDocumentTypes:
    def test_all_extensions_have_mime_types(self):
        for ext, mime in SUPPORTED_DOCUMENT_TYPES.items():
            assert ext.startswith("."), f"{ext} missing leading dot"
            assert "/" in mime, f"{mime} is not a valid MIME type"

    @pytest.mark.parametrize(
        "ext",
        [".pdf", ".md", ".txt", ".html", ".htm", ".zip", ".docx", ".xlsx", ".pptx"],
    )
    def test_expected_extensions_present(self, ext):
        assert ext in SUPPORTED_DOCUMENT_TYPES

    @pytest.mark.parametrize("ext", [".html", ".htm", ".md", ".txt"])
    def test_inline_text_extensions_are_supported_documents(self, ext):
        assert ext in SUPPORTED_DOCUMENT_TYPES
        assert ext in INLINE_TEXT_DOCUMENT_EXTENSIONS

    def test_all_inline_text_extensions_are_supported_documents(self):
        assert INLINE_TEXT_DOCUMENT_EXTENSIONS <= set(SUPPORTED_DOCUMENT_TYPES)


class TestTelegramHtmlDocumentHandling:
    @pytest.mark.asyncio
    async def test_small_html_document_is_cached_and_injected_into_event_text(self):
        html_bytes = b"<html><body><h1>Prompt Gallery</h1></body></html>"
        document = _FakeTelegramDocument(
            file_name="gallery.html",
            mime_type="text/html",
            file_size=len(html_bytes),
            content=html_bytes,
        )
        message = _fake_telegram_message(document=document, caption="review this")
        update = SimpleNamespace(message=message, update_id=123)
        adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***", extra={}))
        adapter.handle_message = AsyncMock()

        await adapter._handle_media_message(update, context=None)

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.await_args.args[0]
        assert event.media_types == ["text/html"]
        assert event.media_urls
        assert Path(event.media_urls[0]).read_bytes() == html_bytes
        assert event.text.startswith("[Content of gallery.html]:\n")
        assert "<h1>Prompt Gallery</h1>" in event.text
        assert event.text.endswith("\n\nreview this")


class _FakeTelegramFile:
    def __init__(self, content: bytes):
        self._content = content
        self.file_path = "gallery.html"

    async def download_as_bytearray(self):
        return bytearray(self._content)


class _FakeTelegramDocument:
    def __init__(self, *, file_name: str, mime_type: str, file_size: int, content: bytes):
        self.file_name = file_name
        self.mime_type = mime_type
        self.file_size = file_size
        self._content = content

    async def get_file(self):
        return _FakeTelegramFile(self._content)


def _fake_telegram_message(*, document, caption: str = ""):
    return SimpleNamespace(
        audio=None,
        caption=caption,
        chat=SimpleNamespace(
            id=42,
            type="private",
            title=None,
            full_name="Erik",
            is_forum=False,
        ),
        date=datetime.now(timezone.utc),
        document=document,
        forum_topic_created=None,
        from_user=SimpleNamespace(id=7, full_name="Erik"),
        is_topic_message=False,
        media_group_id=None,
        message_id=99,
        message_thread_id=None,
        photo=None,
        quote=None,
        reply_to_message=None,
        sticker=None,
        text="",
        video=None,
        voice=None,
    )
