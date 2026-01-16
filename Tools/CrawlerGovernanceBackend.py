import os
import time
import urllib
import threading

from typing import Optional
from urllib.parse import urljoin

from flask_cors import CORS
from flask import Flask, jsonify, request, send_file, render_template

# Import the core logic from the previous step
from Tools.governance_core_v3 import GovernanceManager, Status

self_path = os.path.dirname(os.path.abspath(__file__))


class CrawlerGovernanceBackend:
    def __init__(self,
                 governor: GovernanceManager,
                 host: Optional[str] = "0.0.0.0",
                 port: Optional[int] = 8002,
                 app: Optional[Flask] = None,
                 base_url: Optional[str] = ''
                 ):
        """
        Initialize the Governance Backend Service

        Args:
            governor: Instance of GovernanceManager
            host: Host address for the web service
            port: Port for the web service
            base_url: The prefix of URL
        """
        self.governor = governor
        self.host = host
        self.port = port
        self.app = app
        self.base_url = base_url

        self.own_app = not app
        self.flask_thread = None

        # Last validation time for health checks
        self.last_validation_time = time.time()

    def start_service(self, blocking: bool = False):
        """
        Start the Flask web service either in blocking mode or in a background thread.

        Args:
            blocking: If True, runs in foreground and blocks execution;
                     if False, runs in background thread
        """
        if not self.app:
            self.app = Flask(__name__)
            self.app.secret_key = os.urandom(24)  # Secret key for session management
            CORS(self.app)

        self._register_routes(wrapper=None)

        # Set up template and static file serving
        self.app.template_folder = self_path

        if self.own_app:
            if blocking:
                # Run in foreground (blocking)
                print(f"Starting Governance API Server on http://{self.host}:{self.port}")
                self.app.run(debug=True, host=self.host, port=self.port, use_reloader=False, threaded=True)
            else:
                # Run in background thread (non-blocking)
                def run_flask():
                    """Run Flask app in a separate thread"""
                    print(f"Starting Governance API Server on http://{self.host}:{self.port}")
                    self.app.run(
                        debug=True,
                        host=self.host,
                        port=self.port,
                        use_reloader=False,
                        threaded=True
                    )

                # Create and start daemon thread
                self.flask_thread = threading.Thread(
                    target=run_flask,
                    daemon=True
                )
                self.flask_thread.start()

                # Wait briefly for server initialization
                time.sleep(1)
                print(f"Flask server running in background on http://{self.host}:{self.port}")

    def _register_routes(self, wrapper):
        """
        Register all governance routes to the Flask application

        Args:
            wrapper: Optional wrapper function for routes
        """

        def maybe_wrap(fn):
            return wrapper(fn) if wrapper else fn

        def build_url(endpoint: str) -> str:
            return urljoin(self.base_url, endpoint)

        # Dashboard and monitoring endpoints
        self.app.add_url_rule(build_url('/'), 'read_root', maybe_wrap(self.read_root))

        self.app.add_url_rule(build_url('/api/dashboard/stats'), 'get_dashboard_stats',
                              maybe_wrap(self.get_dashboard_stats), methods=['GET'])

        # 'get_tasks' now reflects the Group/List hierarchy
        self.app.add_url_rule(build_url('/api/groups'), 'get_groups',
                              maybe_wrap(self.get_groups), methods=['GET'])

        self.app.add_url_rule(build_url('/api/logs'), 'get_logs',
                              maybe_wrap(self.get_logs), methods=['GET'])

        # Lookup file by URL Hash (since we store paths now)
        self.app.add_url_rule(build_url('/api/snapshot/<url_hash>'), 'get_snapshot',
                              maybe_wrap(self.get_snapshot), methods=['GET'])

        self.app.add_url_rule(build_url('/api/status/recent'), 'get_recent_statuses',
                              maybe_wrap(self.get_recent_statuses), methods=['GET'])

        # --- System Control Endpoints ---
        self.app.add_url_rule(build_url('/api/control/<action>'), 'system_control',
                              maybe_wrap(self.system_control), methods=['POST'])

        # --- RPC Endpoints (For External Scripts) ---
        self.app.add_url_rule(build_url('/rpc/register_group'), 'rpc_register_group',
                              maybe_wrap(self.rpc_register_group), methods=['POST'])
        self.app.add_url_rule(build_url('/rpc/should_crawl'), 'rpc_should_crawl',
                              maybe_wrap(self.rpc_should_crawl), methods=['POST'])
        self.app.add_url_rule(build_url('/rpc/report_result'), 'rpc_report_result',
                              maybe_wrap(self.rpc_report_result), methods=['POST'])

    # ------------------------------------------ Web Service Methods ------------------------------------------

    def read_root(self):
        """Render the main dashboard page"""
        return render_template("governance_frontend.html")

    def get_dashboard_stats(self):
        """Aggregates stats using the new schema (crawl_status)."""
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        db = self.governor.db

        # 1. Active Spiders (Count distinct spiders in status table)
        spiders = db.fetch_one("SELECT count(DISTINCT spider_name) as cnt FROM crawl_status")

        # 2. Global Success Rate (From Log history, last 1000)
        success = db.fetch_one("""
            SELECT 
                avg(CASE WHEN status=2 THEN 1 ELSE 0 END) as rate, -- Status.SUCCESS=2
                count(*) as total
            FROM crawl_log ORDER BY id DESC LIMIT 1000
        """)

        # 3. Network Errors Today (Temp Fail = 3)
        net_errors = db.fetch_one("""
            SELECT count(*) as cnt FROM crawl_log 
            WHERE status=3 AND created_at > date('now')
        """)

        # 4. Current Queue (Pending=0)
        pending = db.fetch_one("SELECT count(*) as cnt FROM crawl_status WHERE status=0")

        return jsonify({
            "active_spiders": spiders['cnt'],
            "success_rate": round((success['rate'] or 0) * 100, 1),
            "total_requests": success['total'],
            "network_errors": net_errors['cnt'],
            "pending_count": pending['cnt']
        })

    def get_groups(self):
        """
        UPDATED: Fetches the dashboard summary using the Governor's logic.
        Replaces the old 'get_tasks'.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        spider_filter = request.args.get('spider')

        # Leverage the robust aggregation logic in the core
        summary = self.governor.get_dashboard_summary(spider_filter)
        return jsonify(summary)

    def get_logs(self):
        """Fetch streaming logs"""
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        limit = request.args.get('limit', 100, type=int)
        status = request.args.get('status', type=int)
        spider = request.args.get('spider')

        query = "SELECT * FROM crawl_log"
        params = []
        conditions = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        if spider:
            conditions.append("spider_name = ?")
            params.append(spider)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = self.governor.db.fetch_all(query, tuple(params))
        return jsonify([dict(row) for row in rows])

    def get_snapshot(self, url_hash: str):
        """
        UPDATED: Look up file path from DB using url_hash.
        """
        if not url_hash.isalnum():
            return jsonify({"error": "Invalid hash"}), 400

        # Query DB for the file path associated with this URL hash
        row = self.governor.db.fetch_one("SELECT file_path FROM crawl_status WHERE url_hash = ?", (url_hash,))

        if not row or not row['file_path']:
            return jsonify({"error": "Snapshot not found in registry"}), 404

        file_path = row['file_path']

        if not os.path.exists(file_path):
            return jsonify({"error": "File deleted or moved"}), 404

        return send_file(file_path)

    def get_recent_statuses(self):
        """
        [NEW] Fetch the latest status of unique URLs, sorted by activity time.
        This provides the "State View" where each URL appears only once.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        limit = request.args.get('limit', 100, type=int)
        spider = request.args.get('spider')
        status = request.args.get('status', type=int)

        query = "SELECT * FROM crawl_status"
        conditions = []
        params = []

        # Filter Logic
        if spider:
            conditions.append("spider_name = ?")
            params.append(spider)

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        # Optional: Hide 'PENDING' (0) records in this view if they haven't run yet?
        # usually user cares about what JUST happened.
        # Let's keep them but maybe filter by 'last_run_at IS NOT NULL'
        conditions.append("last_run_at IS NOT NULL")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # KEY CHANGE: Sort by last_run_at DESC
        query += " ORDER BY last_run_at DESC LIMIT ?"
        params.append(limit)

        rows = self.governor.db.fetch_all(query, tuple(params))
        return jsonify([dict(row) for row in rows])

    def system_control(self, action: str):
        """
        NEW: Handle PAUSE, RESUME, IMMEDIATE signals.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        action = action.upper()

        if action == "PAUSE":
            self.governor.pause()
        elif action == "RESUME":
            self.governor.resume()
        elif action == "IMMEDIATE":
            self.governor.trigger_immediate()
        else:
            return jsonify({"error": "Unknown action"}), 400

        return jsonify({"status": "ok", "action": action})

    # ------------------------------------------ RPC Endpoints ------------------------------------------
    # These endpoints allow external python scripts to use the governance logic
    # without touching the DB file directly.

    def rpc_register_group(self):
        """
        Maps to register_group_metadata.
        Replaces rpc_register_task.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data"}), 400

        # Expects: group_path (or group), list_url (optional), name (optional)
        group_path = data.get('group_path') or data.get('group')  # Compatibility
        if not group_path:
            return jsonify({"error": "Missing group_path"}), 400

        list_url = data.get('list_url') or data.get('url')  # Compatibility
        friendly_name = data.get('name')

        self.governor.register_group_metadata(group_path, list_url, friendly_name)
        return jsonify({"status": "registered"})

    def rpc_should_crawl(self):
        """
        Unified check logic. No more TaskType distinction needed in arguments.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        data = request.get_json()
        if not data or 'url' not in data:
            return jsonify({"error": "Missing url"}), 400

        # Max retries can be passed, or default to 3
        max_retries = data.get('max_retries', 3)

        result = self.governor.should_crawl(data['url'], max_retries)
        return jsonify({"should_crawl": result})

    def rpc_report_result(self):
        """
        Maps to _handle_task_finish using Fallback Mode (log_id=None).
        Stateless reporting for external scripts.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data"}), 400

        required = ['url', 'group_path', 'status']
        for f in required:
            if f not in data:
                return jsonify({"error": f"Missing {f}"}), 400

        # Auto-extract spider name if not provided (Governor handles it, but we can pass explicit)
        spider_name = data.get('spider')
        if not spider_name:
            # Let Governor extract from group_path
            spider_name = self.governor._extract_spider_name(data['group_path'])

        # Stateless call: We don't have a log_id from a previous session,
        # so we pass None. Governor will create a new log entry + update status.
        self.governor._handle_task_finish(
            log_id=None,
            url=data['url'],
            spider=spider_name,
            group_path=data['group_path'],
            status=Status(data['status']),
            duration=data.get('duration', 0.0),
            http_code=data.get('http_code', 0),
            state_msg=data.get('error_msg') or data.get('state_msg'),
            file_path=data.get('file_path')
        )
        return jsonify({"status": "acked"})


# ----------------------------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    # Example usage
    gov = GovernanceManager()
    backend = CrawlerGovernanceBackend(gov, host="0.0.0.0", port=8002)
    backend.start_service(blocking=True)
