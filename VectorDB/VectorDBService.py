# vector_service.py
"""
Vector Database Service Core

This file provides a thread-safe, non-blocking service
for managing and accessing vector stores.

- VectorDB: A singleton-like class that loads heavy resources
  (model, db client) ONCE in a background thread.
- VectorStoreManager: A lightweight wrapper for a *single* collection.
"""

import threading
import time
from typing import List, Dict, Any, Union, Optional

# --- Global State for Shared Resources ---
# This dictionary holds the single, shared instances of heavy components.
_SHARED_COMPONENTS = {
    "client": None,
    "model": None,
    "status": "initializing",  # States: initializing, ready, error
    "error": None
}

# This lock protects the _SHARED_COMPONENTS dictionary during initialization
_init_lock = threading.Lock()
_init_thread = None


class VectorDBService:
    """
    A non-blocking service that manages shared heavy resources
    (model, db client) and provides access to VectorStoreManagers.

    This class should be instantiated ONCE at application startup.
    """

    def __init__(self, db_path: str, model_name: str):
        """
        Starts the non-blocking initialization of shared resources.

        Args:
            db_path (str): Path for PersistentClient (e.g., "./vector_stores")
            model_name (str): Name of the sentence-transformer model.
        """
        global _init_thread

        with _init_lock:
            # Prevent re-initialization if already called
            if _init_thread:
                return

            _SHARED_COMPONENTS["status"] = "initializing"

            # (Requirement 1) Start initialization in a single background thread
            _init_thread = threading.Thread(
                target=self._initialize,
                args=(db_path, model_name),
                daemon=True
            )
            _init_thread.start()

    def _initialize(self, db_path: str, model_name: str):
        """
        [Background Thread] Performs lazy imports and heavy-lifting.
        """
        try:
            # 1. Heavy Imports (Deferred to background thread)
            print(f"[VectorService BG]: Importing heavy libraries...")
            import chromadb
            from sentence_transformers import SentenceTransformer
            print(f"[VectorService BG]: Heavy libraries imported.")

            # 2. Heavy Initialization
            print(f"[VectorService BG]: Initializing ChromaDB client at {db_path}...")
            client = chromadb.PersistentClient(path=db_path)
            print(f"[VectorService BG]: ChromaDB client loaded.")

            print(f"[VectorService BG]: Loading SentenceTransformer model '{model_name}'...")
            model = SentenceTransformer(model_name)
            print(f"[VectorService BG]: SentenceTransformer model loaded.")

            # 3. Safely update the global shared state
            with _init_lock:
                _SHARED_COMPONENTS["client"] = client
                _SHARED_COMPONENTS["model"] = model
                _SHARED_COMPONENTS["status"] = "ready"

            print(f"[VectorService BG]: All shared components are ready.")

        except Exception as e:
            print(f"[VectorService BG]: FATAL: Shared component loading failed: {e}")
            with _init_lock:
                _SHARED_COMPONENTS["status"] = "error"
                _SHARED_COMPONENTS["error"] = str(e)

    def get_status(self) -> Dict[str, Optional[str]]:
        """
        Returns the current initialization status of the shared resources.

        Returns:
            dict: A dictionary with "status" and "error" keys.
                  Status can be "initializing", "ready", or "error".
        """
        with _init_lock:
            return {
                "status": _SHARED_COMPONENTS["status"],
                "error": _SHARED_COMPONENTS["error"]
            }

    def get_store(self, collection_name: str, chunk_size: int = 512, chunk_overlap: int = 50) -> "VectorStoreManager":
        """
        Gets a VectorStoreManager (a collection handle).

        This method will FAIL if the service is not 'ready'.
        The caller MUST check get_status() before calling this.

        Args:
            collection_name (str): The name of the collection to get/create.
            chunk_size (int): The target size for text chunks.
            chunk_overlap (int): The overlap between consecutive chunks.

        Returns:
            VectorStoreManager: A lightweight manager instance for the collection.
        """
        with _init_lock:
            status = _SHARED_COMPONENTS["status"]
            if status != "ready":
                raise RuntimeError(
                    f"VectorDB is not ready (status: {status}). "
                    f"Check get_status() before calling get_store()."
                )

            client = _SHARED_COMPONENTS["client"]
            model = _SHARED_COMPONENTS["model"]

        # Create the lightweight manager. This is fast.
        return VectorStoreManager(
            chroma_client=client,
            embedding_model=model,
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )


class VectorStoreManager:
    """
    A lightweight, stateless manager for a *single* ChromaDB collection.
    It receives its (heavy) dependencies from the VectorDB.
    """

    def __init__(
            self,
            chroma_client: "chromadb.Client",
            embedding_model: "SentenceTransformer",
            collection_name: str,
            chunk_size: int,
            chunk_overlap: int
    ):
        """
        Initializes the collection manager. This is a fast operation.
        """
        # Lazy import for text splitter (lightweight)
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        self.client = chroma_client
        self.model = embedding_model

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ". ", " ", ""]
        )

        print(f"[VectorStoreManager] Handle created for collection: '{collection_name}'")

    def vectorize_text(self, text: Union[str, List[str]]) -> "np.ndarray":
        """Vectorizes text without saving."""
        # We need numpy, but sentence_transformers already imported it,
        # and encode() returns a numpy array.
        return self.model.encode(text)

    def add_document(self, text: str, doc_id: str) -> List[str]:
        """Adds or updates a document with chunking."""
        chunks = self.text_splitter.split_text(text)

        if not chunks:
            print(f"Warning: Document {doc_id} produced no chunks.")
            return []

        chunk_ids = [f"{doc_id}#chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {"original_doc_id": doc_id, "chunk_index": i, "total_chunks": len(chunks)}
            for i in range(len(chunks))
        ]

        embeddings = self.vectorize_text(chunks).tolist()

        try:
            # This is an "upsert" - it will add or update existing IDs.
            self.collection.upsert(
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
                ids=chunk_ids
            )
            return chunk_ids
        except Exception as e:
            print(f"Error upserting document {doc_id}: {e}")
            return []

    def delete_document(self, doc_id: str) -> bool:
        """Deletes all chunks associated with a single document ID."""
        try:
            self.collection.delete(where={"original_doc_id": doc_id})
            return True
        except Exception as e:
            print(f"Error deleting {doc_id}: {e}")
            return False

    def search(
            self,
            query_text: str,
            top_n: int = 5,
            score_threshold: float = None,
            where_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Searches the collection, filters, and de-duplicates by doc_id."""
        query_vector = self.vectorize_text(query_text).tolist()

        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=top_n * 3,  # Get extra results to ensure top_n *documents*
            where=where_filter,
            include=["metadatas", "documents", "distances"]
        )

        chunk_results = []
        if not results['ids'][0]:
            return []

        for i in range(len(results['ids'][0])):
            distance = results['distances'][0][i]
            similarity_score = 1.0 - distance

            chunk_results.append({
                "chunk_id": results['ids'][0][i],
                "doc_id": results['metadatas'][0][i]['original_doc_id'],
                "score": similarity_score,
                "chunk_text": results['documents'][0][i]
            })

        if score_threshold is not None:
            chunk_results = [r for r in chunk_results if r['score'] >= score_threshold]

        if not chunk_results:
            return []

        # De-duplicate by doc_id, keeping only the best score
        final_doc_results = {}
        for r in chunk_results:
            doc_id = r['doc_id']
            if doc_id not in final_doc_results or r['score'] > final_doc_results[doc_id]['score']:
                final_doc_results[doc_id] = r

        # Sort and take top_n *documents*
        final_list = sorted(
            final_doc_results.values(),
            key=lambda x: x['score'],
            reverse=True
        )
        return final_list[:top_n]

    def count(self) -> int:
        """Returns the total number of chunks in this collection."""
        return self.collection.count()
