from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

import markdown
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer


logger = logging.getLogger(__name__)

RENDERER_VERSION = "post-fragment-v1"
FileSignature = tuple[int, int, int, int]


@dataclass(frozen=True)
class RenderedPost:
    """An immutable post snapshot safe to share between request threads."""

    post_id: str
    source_path: Path
    source_hash: str
    renderer_version: str
    html_fragment: str
    modified_ns: int
    changed_ns: int
    file_size: int
    inode: int

    @property
    def etag(self) -> str:
        payload = f"{self.renderer_version}:{self.source_hash}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def signature(self) -> FileSignature:
        return self.modified_ns, self.changed_ns, self.file_size, self.inode


@dataclass
class _PendingChange:
    first_seen: float
    last_seen: float
    signature: Optional[FileSignature]


class _PostEventHandler(FileSystemEventHandler):
    def __init__(self, manager: "PostManager"):
        self.manager = manager

    def _notify(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self.manager.notify_path_changed(Path(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        self._notify(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._notify(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._notify(event)

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        self._notify(event)
        self.manager.notify_path_changed(Path(event.dest_path))


class PostManager:
    """Compile Markdown posts into hot-reloadable, immutable memory snapshots.

    Filesystem events use trailing-edge debouncing. Repeated events carrying the
    same file signature do not extend the quiet period, which prevents browser
    requests and noisy editors from postponing a rebuild indefinitely. A maximum
    wait bounds latency when an editor continuously writes different versions.
    """

    def __init__(
            self,
            posts_root: str | Path,
            *,
            watch_enabled: bool = True,
            debounce_seconds: float = 2.0,
            max_wait_seconds: float = 30.0,
            renderer: Optional[Callable[[str], str]] = None,
    ):
        if debounce_seconds <= 0:
            raise ValueError("debounce_seconds must be positive")
        if max_wait_seconds < debounce_seconds:
            raise ValueError("max_wait_seconds must be >= debounce_seconds")

        self.posts_root = Path(posts_root).resolve()
        self.watch_enabled = bool(watch_enabled)
        self.debounce_seconds = float(debounce_seconds)
        self.max_wait_seconds = float(max_wait_seconds)
        self._renderer = renderer or self._render_markdown

        self._posts: dict[str, RenderedPost] = {}
        self._failed_signatures: dict[str, Optional[FileSignature]] = {}
        self._failed_hashes: dict[str, str] = {}
        self._snapshot_lock = threading.RLock()
        # Rendering is cheap for posts and serializing it guarantees that a
        # request fallback and the watcher cannot compile the same version twice.
        self._compile_lock = threading.Lock()

        self._pending: dict[str, _PendingChange] = {}
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._observer: Optional[Observer] = None
        self._lifecycle_lock = threading.Lock()
        self._started = False

        self._render_attempts = 0
        self._render_successes = 0
        self._coalesced_events = 0

    @staticmethod
    def _render_markdown(content: str) -> str:
        return markdown.markdown(content, extensions=["fenced_code", "tables"])

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            self.posts_root.mkdir(parents=True, exist_ok=True)

            self._worker = threading.Thread(
                target=self._change_worker,
                name="PostHotReload",
                daemon=True,
            )
            self._worker.start()

            if self.watch_enabled:
                try:
                    observer = Observer()
                    observer.schedule(
                        _PostEventHandler(self), str(self.posts_root), recursive=True)
                    observer.start()
                    self._observer = observer
                except Exception:
                    logger.exception(
                        "Failed to start post filesystem observer; request-time "
                        "revalidation remains active.")

            self._refresh_all()
            logger.info(
                "PostManager started: root=%s posts=%d watch=%s debounce=%.3fs max_wait=%.3fs",
                self.posts_root, len(self._posts), self._observer is not None,
                self.debounce_seconds, self.max_wait_seconds,
            )

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started:
                return
            self._started = False
            self._stop_event.set()
            with self._condition:
                self._condition.notify_all()

            observer = self._observer
            self._observer = None
            if observer is not None:
                observer.stop()
                observer.join(timeout=3)

            worker = self._worker
            self._worker = None
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=3)

    def normalize_post_id(self, raw_post_id: str) -> Optional[str]:
        value = str(raw_post_id or "").strip().replace("\\", "/").strip("/")
        if value.lower().endswith(".md"):
            value = value[:-3]
        if not value:
            return None

        candidate = (self.posts_root / f"{value}.md").resolve()
        try:
            relative = candidate.relative_to(self.posts_root)
        except ValueError:
            return None
        if relative.suffix.lower() != ".md":
            return None
        return relative.as_posix()[:-3]

    def _post_id_from_path(self, source_path: Path) -> Optional[str]:
        try:
            relative = source_path.resolve().relative_to(self.posts_root)
        except (OSError, ValueError):
            return None
        if relative.suffix.lower() != ".md":
            return None
        return relative.as_posix()[:-3]

    def _path_for_post_id(self, post_id: str) -> Path:
        return self.posts_root / f"{post_id}.md"

    @staticmethod
    def _file_signature(path: Path) -> Optional[FileSignature]:
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            return None
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino

    def get(self, raw_post_id: str) -> Optional[RenderedPost]:
        post_id = self.normalize_post_id(raw_post_id)
        if post_id is None:
            return None

        source_path = self._path_for_post_id(post_id)
        signature = self._file_signature(source_path)
        with self._snapshot_lock:
            snapshot = self._posts.get(post_id)
            failed_signature = self._failed_signatures.get(post_id, object())

        if snapshot is None and signature is not None:
            # New files should be available on their first request. The compile
            # routine verifies that the file stayed stable while being read.
            if failed_signature != signature:
                self._refresh_post(post_id)
        elif snapshot is not None and signature != snapshot.signature:
            # Safety net for dropped watchdog events. Identical signatures are
            # coalesced, so request traffic cannot keep resetting the debounce.
            self._schedule(post_id, signature)
        elif snapshot is not None and signature is not None:
            # Some filesystems can preserve mtime/ctime for an extremely fast,
            # same-size rewrite. Posts are small and requested infrequently, so
            # hashing here is a cheap correctness fallback. A hash mismatch only
            # schedules the debounced worker; it never renders in the request.
            try:
                current_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            except OSError:
                current_hash = ""
            if current_hash and current_hash != snapshot.source_hash:
                self._schedule(post_id, signature)

        with self._snapshot_lock:
            return self._posts.get(post_id)

    def notify_path_changed(self, source_path: Path) -> None:
        post_id = self._post_id_from_path(source_path)
        if post_id is None:
            return
        self._schedule(post_id, self._file_signature(self._path_for_post_id(post_id)))

    def _schedule(
            self, post_id: str, signature: Optional[FileSignature]) -> None:
        with self._snapshot_lock:
            failed_signature = self._failed_signatures.get(post_id, object())
            failed_hash = self._failed_hashes.get(post_id)
        if failed_signature == signature:
            # Metadata can stay unchanged after a very fast same-size rewrite.
            # Suppress a known-bad save only while its content hash is also the
            # same, otherwise allow the recovered content through the debounce.
            try:
                current_hash = hashlib.sha256(
                    self._path_for_post_id(post_id).read_bytes()).hexdigest()
            except OSError:
                current_hash = None
            if current_hash == failed_hash:
                return

        now = time.monotonic()
        with self._condition:
            pending = self._pending.get(post_id)
            if pending is None:
                self._pending[post_id] = _PendingChange(now, now, signature)
            elif pending.signature == signature:
                self._coalesced_events += 1
                return
            else:
                pending.last_seen = now
                pending.signature = signature
                self._coalesced_events += 1
            self._condition.notify()

    def _change_worker(self) -> None:
        while not self._stop_event.is_set():
            ready: list[str] = []
            with self._condition:
                while not self._pending and not self._stop_event.is_set():
                    self._condition.wait()
                if self._stop_event.is_set():
                    return

                now = time.monotonic()
                next_due: Optional[float] = None
                for post_id, pending in self._pending.items():
                    due = min(
                        pending.last_seen + self.debounce_seconds,
                        pending.first_seen + self.max_wait_seconds,
                    )
                    if due <= now:
                        ready.append(post_id)
                    elif next_due is None or due < next_due:
                        next_due = due

                if not ready:
                    timeout = max(0.0, (next_due or now) - now)
                    self._condition.wait(timeout=timeout)
                    continue

                for post_id in ready:
                    self._pending.pop(post_id, None)

            for post_id in ready:
                self._refresh_post(post_id)

    def _refresh_all(self) -> None:
        discovered: set[str] = set()
        for source_path in self.posts_root.rglob("*.md"):
            post_id = self._post_id_from_path(source_path)
            if post_id is None:
                continue
            discovered.add(post_id)
            self._refresh_post(post_id)

        with self._snapshot_lock:
            for post_id in set(self._posts) - discovered:
                self._posts.pop(post_id, None)
                self._failed_signatures.pop(post_id, None)
                self._failed_hashes.pop(post_id, None)

    def _refresh_post(self, post_id: str) -> None:
        with self._compile_lock:
            self._refresh_post_locked(post_id)

    def _refresh_post_locked(self, post_id: str) -> None:
        source_path = self._path_for_post_id(post_id)
        signature_before = self._file_signature(source_path)
        if signature_before is None:
            with self._snapshot_lock:
                self._posts.pop(post_id, None)
                self._failed_signatures.pop(post_id, None)
                self._failed_hashes.pop(post_id, None)
            logger.info("Post removed: %s", post_id)
            return

        source_hash = ""
        try:
            source_bytes = source_path.read_bytes()
            signature_after = self._file_signature(source_path)
            if signature_after != signature_before:
                self._schedule(post_id, signature_after)
                return

            source_hash = hashlib.sha256(source_bytes).hexdigest()
            with self._snapshot_lock:
                previous = self._posts.get(post_id)
            if (previous is not None
                    and previous.source_hash == source_hash
                    and previous.renderer_version == RENDERER_VERSION):
                with self._snapshot_lock:
                    self._posts[post_id] = replace(
                        previous,
                        modified_ns=signature_before[0],
                        changed_ns=signature_before[1],
                        file_size=signature_before[2],
                        inode=signature_before[3],
                    )
                    self._failed_signatures.pop(post_id, None)
                    self._failed_hashes.pop(post_id, None)
                return

            self._render_attempts += 1
            html_fragment = self._renderer(source_bytes.decode("utf-8"))
            snapshot = RenderedPost(
                post_id=post_id,
                source_path=source_path,
                source_hash=source_hash,
                renderer_version=RENDERER_VERSION,
                html_fragment=html_fragment,
                modified_ns=signature_before[0],
                changed_ns=signature_before[1],
                file_size=signature_before[2],
                inode=signature_before[3],
            )
            with self._snapshot_lock:
                self._posts[post_id] = snapshot
                self._failed_signatures.pop(post_id, None)
                self._failed_hashes.pop(post_id, None)
                self._render_successes += 1
            logger.info("Post hot-reloaded: %s hash=%s", post_id, source_hash[:12])
        except Exception:
            # Keep serving the last known-good snapshot. Remember the failed
            # signature so requests do not repeatedly compile the same bad save.
            with self._snapshot_lock:
                self._failed_signatures[post_id] = signature_before
                self._failed_hashes[post_id] = source_hash
            logger.exception("Post compilation failed; keeping last good version: %s", post_id)

    def stats(self) -> dict[str, int | bool]:
        with self._snapshot_lock, self._condition:
            return {
                "posts": len(self._posts),
                "pending": len(self._pending),
                "render_attempts": self._render_attempts,
                "render_successes": self._render_successes,
                "coalesced_events": self._coalesced_events,
                "watching": self._observer is not None,
            }

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        """Testing/diagnostic helper: wait until the debounce queue is empty."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._condition:
                if not self._pending:
                    return True
            time.sleep(0.01)
        return False
