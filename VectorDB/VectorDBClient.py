# client.py
import requests
from typing import List, Dict, Any, Optional

class VectorDBClient:
    """
    A Python client for the standalone VectorDB Service.
    
    Acts as a remote proxy for `VectorCollectionRepo`.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def get_collection(self, name: str) -> "RemoteCollection":
        return RemoteCollection(self.base_url, name)


class RemoteCollection:
    def __init__(self, base_url: str, name: str):
        self.api_url = f"{base_url}/api/v1/collections/{name}"
        self.name = name

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
        resp.raise_for_status()
        return resp.json()

    def search(
        self, 
        query: str, 
        top_n: int = 5, 
        filter_criteria: Optional[Dict] = None
    ) -> List[Dict]:
        """Searches the remote DB."""
        payload = {
            "query": query,
            "top_n": top_n,
            "filter_criteria": filter_criteria
        }
        resp = requests.post(f"{self.api_url}/search", json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete(self, doc_id: str) -> bool:
        """Deletes a document by ID."""
        resp = requests.delete(f"{self.api_url}/documents/{doc_id}")
        return resp.status_code == 200

    def stats(self) -> Dict:
        """Gets collection stats."""
        resp = requests.get(f"{self.api_url}/stats")
        resp.raise_for_status()
        return resp.json()

# --- Usage Example ---
if __name__ == "__main__":
    # 1. Connect
    client = VectorDBClient(base_url="http://localhost:8000")
    kb = client.get_collection("my_knowledge_base")

    # 2. Upsert
    print("Upserting...")
    kb.upsert(
        doc_id="tutorial_01", 
        text="Vector databases are great for semantic search.", 
        metadata={"tag": "tech"}
    )

    # 3. Search
    print("Searching...")
    results = kb.search("semantic search", top_n=1)
    for r in results:
        print(f"Found: {r['doc_id']} (Score: {r['score']:.4f})")
