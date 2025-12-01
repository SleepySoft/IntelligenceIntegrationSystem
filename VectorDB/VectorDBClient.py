import os
import time
import argparse
import requests
from typing import List, Dict, Any, Optional


class VectorDBInitializationError(Exception):
    """Raised when the server reports an initialization failure."""
    pass


class VectorDBClient:
    """
    A Python client for the standalone VectorDB Service.

    Now supports waiting for the backend to complete its heavy initialization.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def get_collection(self, name: str) -> "RemoteCollection":
        return RemoteCollection(self.base_url, name)

    def get_status(self) -> Dict[str, Any]:
        """Check the raw status of the server."""
        try:
            resp = requests.get(f"{self.base_url}/api/status", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            # If server is down, we return a synthetic status
            return {"status": "unreachable", "error": str(e)}

    def wait_until_ready(self, timeout: float = 60.0, poll_interval: float = 2.0) -> bool:
        """
        Blocks until the VectorDB service is fully initialized and ready to accept requests.

        Args:
            timeout (float): Max time to wait in seconds.
            poll_interval (float): Seconds between checks.

        Returns:
            bool: True if ready.

        Raises:
            TimeoutError: If timeout is reached.
            VectorDBInitializationError: If server reports a fatal error.

        Usage:
            try:
                client.wait_until_ready(timeout=60)
                kb = client.get_collection("knowledge_base")
                print(f"Connected. Current docs: {kb.stats()}")

            except TimeoutError:
                print("CRITICAL: Vector Store timed out. Is the backend running?")
                exit(1)

            except VectorDBInitializationError as e:
                print(f"CRITICAL: Vector Store failed to load model: {e}")
                exit(1)
        """
        start_time = time.time()
        print(f"[Client] Waiting for VectorDB at {self.base_url} (Timeout: {timeout}s)...")

        while True:
            # Check timeout
            if (time.time() - start_time) > timeout:
                raise TimeoutError(f"VectorDB service not ready after {timeout} seconds.")

            try:
                # Call the status endpoint
                # Note: We use a short timeout for the request itself so we don't hang
                resp = requests.get(f"{self.base_url}/api/status", timeout=2)

                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")

                    if status == "ready":
                        print(f"[Client] VectorDB is READY.")
                        return True

                    elif status == "error":
                        error_msg = data.get("error", "Unknown error")
                        raise VectorDBInitializationError(f"Server failed to initialize: {error_msg}")

                    elif status == "initializing":
                        # Still loading, just continue loop
                        pass

                # If we get 503, it might be the Flask app is up but our specific status handler logic 
                # (if modified) or intermediate proxies are returning 503.
                # Usually /api/status should return 200 even if initializing.

            except requests.exceptions.ConnectionError:
                # The Flask server itself might not be running yet
                pass
            except Exception as e:
                # Other errors (DNS, etc.)
                print(f"[Client] Warning during poll: {e}")

            # Wait before next retry
            time.sleep(poll_interval)


class RemoteCollection:
    def __init__(self, base_url: str, name: str):
        self.api_url = f"{base_url}/api/collections/{name}"
        self.name = name

    def _handle_response(self, resp: requests.Response) -> Dict:
        """Helper to handle errors, specifically 503s."""
        if resp.status_code == 503:
            raise RuntimeError(
                "VectorDB is initializing. Please call client.wait_until_ready() before performing operations."
            )
        resp.raise_for_status()
        return resp.json()

    def upsert(self, doc_id: str, text: str, metadata: Dict[str, Any] = None) -> Dict:
        """Upserts a document to the remote DB."""
        if metadata is None:
            metadata = {}

        payload = {
            "doc_id": doc_id,
            "text": text,
            "metadata": metadata
        }
        resp = requests.post(f"{self.api_url}/upsert", json=payload)
        return self._handle_response(resp)

    def search(
            self,
            query: str,
            top_n: int = 5,
            score_threshold: float = 0.0,
            filter_criteria: Optional[Dict] = None
    ) -> List[Dict]:
        """Searches the remote DB."""
        payload = {
            "query": query,
            "top_n": top_n,
            "score_threshold": score_threshold,
            "filter_criteria": filter_criteria
        }
        resp = requests.post(f"{self.api_url}/search", json=payload)
        # Search returns a list, not a dict, so handle differently if needed, 
        # but _handle_response expects json output which is fine.
        return self._handle_response(resp)

    def delete(self, doc_id: str) -> bool:
        """Deletes a document by ID."""
        resp = requests.delete(f"{self.api_url}/documents/{doc_id}")
        if resp.status_code == 404:
            return False
        return self._handle_response(resp).get("status") == "success"

    def stats(self) -> Dict:
        """Gets collection stats."""
        resp = requests.get(f"{self.api_url}/stats")
        return self._handle_response(resp)
