import time
from pathlib import Path

from flask import Flask, render_template
from markupsafe import Markup

from ServiceComponent.PostManager import PostManager


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_nested_posts_with_same_basename_have_distinct_ids(tmp_path):
    root = tmp_path / "posts"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "index.md").write_text("# Alpha", encoding="utf-8")
    (root / "b" / "index.md").write_text("# Beta", encoding="utf-8")

    manager = PostManager(root, watch_enabled=False)
    manager.start()
    try:
        alpha = manager.get("a/index")
        beta = manager.get("b/index.md")
        assert alpha is not None and alpha.post_id == "a/index"
        assert beta is not None and beta.post_id == "b/index"
        assert "Alpha" in alpha.html_fragment
        assert "Beta" in beta.html_fragment
        assert alpha.etag != beta.etag
        assert manager.get("../outside") is None
    finally:
        manager.stop()


def test_watchdog_debounces_editor_write_burst(tmp_path):
    root = tmp_path / "posts"
    root.mkdir()
    render_calls = []

    def renderer(content):
        render_calls.append(content)
        return f"<p>{content}</p>"

    manager = PostManager(
        root,
        watch_enabled=True,
        debounce_seconds=0.15,
        max_wait_seconds=0.6,
        renderer=renderer,
    )
    manager.start()
    try:
        post_path = root / "draft.md"
        for version in range(8):
            post_path.write_text(f"version-{version}", encoding="utf-8")
            time.sleep(0.02)

        assert _wait_until(lambda: manager.stats()["render_successes"] == 1)
        snapshot = manager.get("draft")
        assert snapshot is not None
        assert snapshot.html_fragment == "<p>version-7</p>"
        assert render_calls == ["version-7"]
        assert manager.stats()["coalesced_events"] > 0
    finally:
        manager.stop()


def test_request_fallback_does_not_extend_debounce_for_same_version(tmp_path):
    root = tmp_path / "posts"
    root.mkdir()
    post_path = root / "index.md"
    post_path.write_text("old", encoding="utf-8")

    manager = PostManager(
        root,
        watch_enabled=False,
        debounce_seconds=0.3,
        max_wait_seconds=1.0,
        renderer=lambda content: f"<{content}>",
    )
    manager.start()
    try:
        assert manager.get("index").html_fragment == "<old>"
        post_path.write_text("new", encoding="utf-8")

        # Repeated requests observe one signature. They must not reset the
        # trailing-edge timer and postpone the refresh indefinitely.
        assert manager.get("index").html_fragment == "<old>"
        with manager._condition:
            initial_last_seen = manager._pending["index"].last_seen

        for _ in range(10):
            assert manager.get("index").html_fragment == "<old>"
            time.sleep(0.01)

        with manager._condition:
            assert manager._pending["index"].last_seen == initial_last_seen

        assert _wait_until(
            lambda: manager.get("index").html_fragment == "<new>", timeout=1.5)
        assert manager.stats()["render_attempts"] == 2
    finally:
        manager.stop()


def test_same_content_auto_save_updates_signature_without_rendering(tmp_path):
    root = tmp_path / "posts"
    root.mkdir()
    post_path = root / "index.md"
    post_path.write_text("unchanged", encoding="utf-8")

    manager = PostManager(
        root,
        watch_enabled=False,
        debounce_seconds=0.05,
        max_wait_seconds=0.2,
    )
    manager.start()
    try:
        original = manager.get("index")
        assert manager.stats()["render_attempts"] == 1

        time.sleep(0.01)
        post_path.write_text("unchanged", encoding="utf-8")
        manager.notify_path_changed(post_path)
        assert _wait_until(
            lambda: manager.get("index").signature != original.signature)

        assert manager.stats()["render_attempts"] == 1
        assert manager.get("index").source_hash == original.source_hash
    finally:
        manager.stop()


def test_fixed_wrapper_renders_new_snapshot_without_jinja_template_reload(tmp_path):
    root = tmp_path / "posts"
    root.mkdir()
    post_path = root / "index.md"
    post_path.write_text("# Before", encoding="utf-8")
    manager = PostManager(
        root,
        watch_enabled=False,
        debounce_seconds=0.05,
        max_wait_seconds=0.2,
    )
    manager.start()

    template_root = Path(__file__).resolve().parent.parent / "templates"
    app = Flask(__name__, template_folder=str(template_root))

    @app.get("/post/<path:post_id>")
    def show_post(post_id):
        post = manager.get(post_id)
        return render_template(
            "post.html",
            post_title=post.post_id,
            post_content=Markup(post.html_fragment),
        )

    try:
        client = app.test_client()
        assert b"Before" in client.get("/post/index").data

        post_path.write_text("# After", encoding="utf-8")
        manager.notify_path_changed(post_path)
        assert _wait_until(
            lambda: "After" in manager.get("index").html_fragment)

        # Jinja caches post.html, but the Markdown fragment is request data and
        # therefore changes without reloading or invalidating that template.
        response = client.get("/post/index")
        assert b"After" in response.data
        assert b"Before" not in response.data
    finally:
        manager.stop()


def test_failed_save_keeps_last_good_and_is_not_retried_until_change(
        tmp_path, monkeypatch):
    root = tmp_path / "posts"
    root.mkdir()
    post_path = root / "index.md"
    post_path.write_text("good", encoding="utf-8")

    manager = PostManager(
        root,
        watch_enabled=False,
        debounce_seconds=0.05,
        max_wait_seconds=0.2,
    )
    manager.start()
    try:
        original = manager.get("index")
        fixed_signature = original.signature
        monkeypatch.setattr(
            manager,
            "_file_signature",
            lambda path: fixed_signature if path.exists() else None,
        )

        # Simulate a filesystem/editor combination that preserves every piece
        # of metadata across same-size rewrites.
        post_path.write_bytes(b"\xff\xfe\xff\xfe")
        manager.notify_path_changed(post_path)
        assert _wait_until(lambda: manager.stats()["render_attempts"] == 2)

        assert manager.get("index") == original
        attempts_after_failure = manager.stats()["render_attempts"]
        for _ in range(5):
            manager.notify_path_changed(post_path)
            manager.get("index")
        time.sleep(0.1)
        assert manager.stats()["render_attempts"] == attempts_after_failure

        post_path.write_text("back", encoding="utf-8")
        manager.notify_path_changed(post_path)
        assert _wait_until(
            lambda: "back" in manager.get("index").html_fragment)
    finally:
        manager.stop()
