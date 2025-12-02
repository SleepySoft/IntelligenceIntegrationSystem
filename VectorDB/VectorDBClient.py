import time
import requests
from typing import List, Dict, Any, Optional


class VectorDBInitializationError(Exception):
    """Raised when the server reports an initialization failure."""
    pass


class VectorDBClient:
    """
    A Python client for the standalone VectorDB Service.
    """

    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url.rstrip("/")

    def get_status(self) -> Dict[str, Any]:
        """Check the raw status of the server."""
        try:
            resp = requests.get(f"{self.base_url}/api/status", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            return {"status": "unreachable", "error": str(e)}

    def wait_until_ready(self, timeout: float = 60.0, poll_interval: float = 2.0) -> bool:
        """
        Blocks until the VectorDB service is fully initialized and ready.
        """
        start_time = time.time()
        print(f"[Client] Waiting for VectorDB at {self.base_url} (Timeout: {timeout}s)...")

        while True:
            if (time.time() - start_time) > timeout:
                raise TimeoutError(f"VectorDB service not ready after {timeout} seconds.")

            try:
                resp = requests.get(f"{self.base_url}/api/status", timeout=2)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")

                    if status == "ready":
                        print(f"[Client] VectorDB is READY.")
                        return True
                    elif status == "error":
                        raise VectorDBInitializationError(f"Server failed: {data.get('error')}")
                    # If initializing, loop again
            except requests.exceptions.ConnectionError:
                pass
            except Exception as e:
                print(f"[Client] Warning during poll: {e}")

            time.sleep(poll_interval)

    def create_collection(self, name: str, chunk_size: int = 512, chunk_overlap: int = 50) -> "RemoteCollection":
        """
        Explicitly creates a collection or updates its configuration.
        MUST be called before upserting if the collection does not exist.

        Args:
            name (str): Collection name.
            chunk_size (int): Text splitter chunk size.
            chunk_overlap (int): Text splitter overlap.

        Returns:
            RemoteCollection: A handle to the collection.
        """
        url = f"{self.base_url}/api/collections"
        payload = {
            "name": name,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap
        }
        resp = requests.post(url, json=payload)

        # --- 优化错误处理 ---
        if resp.status_code == 503:
            raise RuntimeError(
                "VectorDB is initializing. Please call client.wait_until_ready() before creating collections."
            )
        # ------------------

        resp.raise_for_status()
        return RemoteCollection(self.base_url, name)

    def get_collection(self, name: str) -> "RemoteCollection":
        """
        Gets a handle to an EXISTING collection.
        Note: This does not verify existence immediately. Operations will fail if it doesn't exist.
        """
        return RemoteCollection(self.base_url, name)

    def list_collections(self) -> List[str]:
        """Lists all available collections."""
        resp = requests.get(f"{self.base_url}/api/collections")
        resp.raise_for_status()
        return resp.json().get("collections", [])


class RemoteCollection:
    def __init__(self, base_url: str, name: str):
        self.api_url = f"{base_url}/api/collections/{name}"
        self.name = name

    def _handle_response(self, resp: requests.Response) -> Any:
        """Helper to handle errors, specifically 503 (Loading) and 404 (Not Found)."""
        if resp.status_code == 503:
            raise RuntimeError(
                "VectorDB is initializing. Please call client.wait_until_ready()."
            )
        if resp.status_code == 404:
            raise ValueError(
                f"Collection '{self.name}' not found. Please create it first using client.create_collection()."
            )

        try:
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            # Try to get backend error message
            try:
                err_msg = resp.json().get("error", str(e))
            except:
                err_msg = str(e)
            raise RuntimeError(f"VectorDB Error: {err_msg}")

    def upsert(self, doc_id: str, text: str, metadata: Dict[str, Any] = None) -> Dict:
        """
        Upserts a document.
        Will FAIL if collection was not created via create_collection().
        """
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
        return self._handle_response(resp)

    def delete(self, doc_id: str) -> bool:
        """Deletes a document by ID."""
        resp = requests.delete(f"{self.api_url}/documents/{doc_id}")
        if resp.status_code == 404:
            return False
        res = self._handle_response(resp)
        return res.get("status") == "success"

    def stats(self) -> Dict:
        """Gets collection stats."""
        resp = requests.get(f"{self.api_url}/stats")
        return self._handle_response(resp)

    def clear(self) -> bool:
        """Clears all data in collection."""
        resp = requests.post(f"{self.api_url}/clear")
        res = self._handle_response(resp)
        return res.get("status") == "cleared"
