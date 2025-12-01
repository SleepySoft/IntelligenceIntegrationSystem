import time
import logging
import threading
from typing import List, Dict, Any, Union, Optional


logger = logging.getLogger(__name__)


# Note: Heavy imports (chromadb, sentence_transformers) are delayed inside methods
# or imported at module level depending on startup preference.
# Here we keep them lazy-loaded inside the class to speed up module import.

class VectorStorageEngine:
    """
    VectorStorageEngine: The heavy-lifting engine.

    Responsibilities:
    1. Manages the connection to the Vector DB (ChromaDB).
    2. Loads and holds the Embedding Model in memory (SentenceTransformer).
    3. Acts as a factory for VectorCollectionRepo instances.

    This class is thread-safe. You should typically create one instance of this
    per application lifecycle, but multiple instances are allowed (e.g., for different DB paths).
    """

    def __init__(self, db_path: str, model_name: str):
        """
        Initializes the engine. This operation is blocking and heavy.

        Args:
            db_path (str): File system path for the persistent vector database.
            model_name (str): HuggingFace model name for embeddings.
        """
        self._db_path = db_path
        self._model_name = model_name

        # --- State Management ---
        self._status = "initializing"  # initializing, ready, error
        self._error_message = None
        self._ready_event = threading.Event()  # For blocking waits
        self._lock = threading.RLock()

        # Resources (Initially None)
        self._client = None
        self._model = None
        self._repos = {}

        # Start loading in background
        self._init_thread = threading.Thread(
            target=self._load_heavy_resources,
            name="VectorEngineInit",
            daemon=True
        )
        self._init_thread.start()

        logger.info(f"Engine instance created. Initialization started in background.")

    def _load_heavy_resources(self):
        """Internal method to load libraries and models."""
        try:
            logger.info("Importing heavy libraries...")
            # Lazy imports
            import chromadb
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading ChromaDB from {self._db_path}...")
            self._client = chromadb.PersistentClient(path=self._db_path)

            logger.info(f"Loading Model {self._model_name}...")
            self._model = SentenceTransformer(self._model_name)

            # Mark as Ready
            with self._lock:
                self._status = "ready"
                self._ready_event.set()

            logger.info("VectorStorageEngine is READY.")

        except Exception as e:
            logger.error(f"FATAL: Engine initialization failed: {e}")
            with self._lock:
                self._status = "error"
                self._error_message = str(e)
                # We do NOT set the ready event, so waiters will timeout or handle status manually

    def get_status(self) -> Dict[str, Any]:
        """Returns the current lifecycle status."""
        return {
            "status": self._status,
            "error": self._error_message,
            "db_path": self._db_path,
            "model": self._model_name
        }

    def is_ready(self) -> bool:
        return self._status == "ready"

    def wait_until_ready(self, timeout: float = None) -> bool:
        """
        Blocks until the engine is ready.
        Returns True if ready, False if timed out or errored.
        """
        if self._status == "ready":
            return True
        if self._status == "error":
            return False

        return self._ready_event.wait(timeout=timeout)

    def get_repository(self, collection_name: str, chunk_size: int = 512,
                       chunk_overlap: int = 50) -> "VectorCollectionRepo":
        """
        Factory method to get or create a repository for a specific collection.
        Thread-safe.

        Args:
            collection_name (str): Unique name of the collection.
            chunk_size (int): Character limit for text chunks.
            chunk_overlap (int): Character overlap between chunks.

        Returns:
            VectorCollectionRepo: The handler for the requested collection.
        """
        if not self.is_ready():
            raise RuntimeError(
                f"Engine is not ready (Status: {self._status}). "
                f"Error: {self._error_message}"
            )

        with self._lock:
            # Return existing repo if already created with this engine
            if collection_name in self._repos:
                return self._repos[collection_name]

            # We assume VectorCollectionRepo is imported or available
            # To avoid circular imports, you might define VectorCollectionRepo in the same file
            # or import it inside the method if it's in a separate module.
            # Assuming it's in the same file for this snippet context:
            repo = VectorCollectionRepo(
                client=self._client,
                model=self._model,
                collection_name=collection_name,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            self._repos[collection_name] = repo
            return repo


class VectorCollectionRepo:
    """
    VectorCollectionRepo: Manages a specific collection of documents.

    Responsibilities:
    1. Text chunking and splitting.
    2. CRUD operations for documents (Add, Search, Delete).
    3. Managing the relationship between `doc_id` (User concept) and `chunk_id` (DB concept).
    """

    def __init__(
            self,
            client: Any,  # Typed as Any to avoid strict dependency on top-level import
            model: Any,
            collection_name: str,
            chunk_size: int,
            chunk_overlap: int
    ):
        """
        Initialized by VectorStorageEngine. Do not instantiate directly.
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        self._client = client
        self._model = model
        self._collection_name = collection_name

        # Get or create the actual Chroma collection
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ". ", " ", ""]
        )

        print(f"[VectorRepo] Repository ready for collection: '{collection_name}'")

    def _vectorize(self, text: Union[str, List[str]]) -> Any:
        """Internal helper to generate embeddings."""
        return self._model.encode(text)

    def upsert_document(self, doc_id: str, text: str, metadata: Dict[str, Any] = None) -> List[str]:
        """
        Upserts a document: fully replaces any existing document with the same doc_id.

        CRITICAL: This method performs a "Delete-then-Insert" strategy.
        It first deletes ALL existing chunks associated with `doc_id` to ensure
        no stale chunks remain (which happens if the new document is shorter than the old one).

        Args:
            doc_id (str): Unique identifier for the document.
            text (str): The full text content.
            metadata (Dict): Searchable metadata (e.g., {"timestamp": 123}).

        Returns:
            List[str]: The list of generated chunk IDs.
        """
        if not text:
            return []

        if metadata is None:
            metadata = {}

        # 1. Clean up OLD data first (The "Delete" part of Upsert)
        # We must remove all chunks with this original_doc_id.
        # If we skip this, and the new text is shorter than the old text,
        # the extra chunks from the old version will remain as "ghost" data.
        try:
            self._collection.delete(where={"original_doc_id": doc_id})
        except Exception as e:
            # It's acceptable if the doc didn't exist, but other errors should be logged
            print(f"[VectorRepo] Warning: Failed to clear old data for {doc_id} (might be new): {e}")

        # 2. Split new text
        chunks = self._text_splitter.split_text(text)
        if not chunks:
            print(f"[VectorRepo] Warning: Document {doc_id} resulted in empty chunks.")
            return []

        # 3. Prepare new data
        chunk_ids = []
        chunk_metadatas = []

        for i, chunk_text in enumerate(chunks):
            # ID format: doc_id#chunk_index
            c_id = f"{doc_id}#chunk_{i}"
            chunk_ids.append(c_id)

            # Construct metadata
            meta = {
                "original_doc_id": doc_id,  # Link chunk back to parent doc
                "chunk_index": i,
                "total_chunks": len(chunks)
            }
            # Merge user metadata
            meta.update(metadata)
            chunk_metadatas.append(meta)

        # 4. Generate Embeddings
        embeddings = self._vectorize(chunks).tolist()

        # 5. Insert new data
        # We use upsert here just to be safe, though add would work since we deleted.
        try:
            self._collection.upsert(
                ids=chunk_ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=chunk_metadatas
            )
            return chunk_ids
        except Exception as e:
            print(f"[VectorRepo] Error upserting document {doc_id}: {e}")
            return []

    def exists(self, doc_id: str) -> bool:
        """Checks if a document (any of its chunks) exists in the DB."""
        try:
            # Minimal query to check existence
            result = self._collection.get(
                where={"original_doc_id": doc_id},
                limit=1,
                include=[]  # We don't need data, just the check
            )
            return len(result["ids"]) > 0
        except Exception:
            return False

    def delete_document(self, doc_id: str) -> bool:
        """
        Deletes all chunks associated with the given doc_id.
        """
        try:
            # Delete based on metadata filter
            self._collection.delete(where={"original_doc_id": doc_id})
            return True
        except Exception as e:
            print(f"[VectorRepo] Error deleting document {doc_id}: {e}")
            return False

    def search(
            self,
            query_text: str,
            top_n: int = 5,
            score_threshold: float = 0.0,
            filter_criteria: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Semantic search with metadata filtering and deduplication.

        Args:
            query_text (str): The search query.
            top_n (int): Number of unique documents to return.
            score_threshold (float): Minimum similarity score (0 to 1).
            filter_criteria (Dict): MongoDB-style filter (e.g., {"category": "news"}).

        Returns:
            List[Dict]: List of result objects containing doc_id, score, text, metadata.
        """
        query_vector = self._vectorize(query_text).tolist()

        # Request more chunks than top_n because multiple chunks might come from same doc
        fetch_k = top_n * 3

        try:
            results = self._collection.query(
                query_embeddings=[query_vector],
                n_results=fetch_k,
                where=filter_criteria,  # Apply metadata filtering at DB level
                include=["metadatas", "documents", "distances"]
            )
        except Exception as e:
            print(f"[VectorRepo] Search failed: {e}")
            return []

        # Parse results
        # Chroma returns lists of lists (batch format), we usually query one at a time.
        if not results['ids'] or not results['ids'][0]:
            return []

        ids = results['ids'][0]
        distances = results['distances'][0]
        metadatas = results['metadatas'][0]
        documents = results['documents'][0]

        # Standardize results into a list of dicts
        raw_candidates = []
        for i in range(len(ids)):
            score = 1.0 - distances[i]  # Convert distance to similarity

            if score < score_threshold:
                continue

            raw_candidates.append({
                "doc_id": metadatas[i].get("original_doc_id", "unknown"),
                "chunk_id": ids[i],
                "score": score,
                "content": documents[i],
                "metadata": metadatas[i]
            })

        # Deduplicate: Keep only the highest scoring chunk per original_doc_id
        unique_docs_map = {}
        for candidate in raw_candidates:
            d_id = candidate["doc_id"]
            if d_id not in unique_docs_map:
                unique_docs_map[d_id] = candidate
            else:
                # If this chunk has a higher score than the one we already have, replace it
                if candidate["score"] > unique_docs_map[d_id]["score"]:
                    unique_docs_map[d_id] = candidate

        # Sort by score descending and take top N
        final_results = sorted(
            unique_docs_map.values(),
            key=lambda x: x["score"],
            reverse=True
        )

        return final_results[:top_n]

    def clear(self):
        """WARNING: Deletes all data in this collection."""
        try:
            self._client.delete_collection(self._collection_name)
            # Re-init handle
            self._collection = self._client.create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            print(f"[VectorRepo] Error clearing collection: {e}")

    def count(self) -> int:
        """Returns total chunk count."""
        return self._collection.count()
