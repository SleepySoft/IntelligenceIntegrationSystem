#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Vector Index Rebuild and Search Tool

This tool uses the VectorDB to rebuild indexes from MongoDB
and perform interactive searches.

Startup is fast as all heavy loading is deferred to a background thread.
"""

# --- Lightweight Imports ---
import sys
import time
import argparse
import traceback
from typing import Optional

# --- (Requirement 1) Fast Import ---
from VectorDB.VectorDBService import VectorDBService, VectorStoreManager

# --- Configuration ---
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB_NAME = "IntelligenceIntegrationSystem"
MONGO_COLLECTION_NAME = "intelligence_archived"

VECTOR_DB_PATH = "./vector_stores"
MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'

COLLECTION_FULL_TEXT = "intelligence_full_text"
COLLECTION_SUMMARY = "intelligence_summary"

SEARCH_SCORE_THRESHOLD = 0.5
SEARCH_TOP_N = 5


# --- Helper Functions (with lazy imports) ---

def connect_to_mongo() -> Optional["MongoClient"]:
    """Connects to MongoDB and returns the collection object."""
    # Lazy import
    from pymongo import MongoClient
    try:
        client = MongoClient(MONGO_URI)
        client.server_info()  # Test connection
        db = client[MONGO_DB_NAME]
        collection = db[MONGO_COLLECTION_NAME]
        print(f"Successfully connected to MongoDB: {MONGO_DB_NAME}.{MONGO_COLLECTION_NAME}")
        return collection
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        return None


# --- Core Logic Functions ---

def func_rebuild(store_full_text: VectorStoreManager, store_summary: VectorStoreManager):
    """
    Fetches all data from MongoDB and rebuilds the vector indexes.
    (Updated with robust batch processing)
    """
    # Lazy import
    from tqdm import tqdm

    print("\n--- Starting Vector Index Rebuild ---")

    collection = connect_to_mongo()
    if collection is None:
        return

    try:
        total_docs = collection.count_documents({})
    except Exception as e:
        print(f"Error counting documents: {e}")
        total_docs = 0

    if total_docs == 0:
        print("No documents found in MongoDB collection. Nothing to rebuild.")
        return

    print(f"Found {total_docs} documents to process.")
    processed_count = 0
    skipped_count = 0

    batch_size = 500  # 每次从 Mongo 拉取 500 个文档
    last_id = None  # 跟踪上一批的最后一个 _id

    # 使用 'upsert' in add_document 是幂等的，所以我们只是处理所有。
    with tqdm(total=total_docs, desc="Rebuilding Indexes") as pbar:
        while True:
            # 1. 构建查询以获取下一批
            query = {}
            if last_id:
                query['_id'] = {'$gt': last_id}

            # 2. 拉取一个批次 (这是一个短暂、全新的游标)
            try:
                batch_docs = list(
                    collection.find(query)
                    .sort('_id', 1)  # 必须按 _id 排序
                    .limit(batch_size)
                )
            except Exception as e:
                print(f"\nFATAL: Error fetching batch from MongoDB: {e}")
                print(traceback.format_exc())
                break  # 退出 while 循环

            # 3. 检查是否所有批次都已处理完毕
            if not batch_docs:
                break  # 没有更多文档了，退出 while 循环

            # 4. (这是你的原始逻辑) 循环处理内存中的这一小批文档
            for doc in batch_docs:
                try:
                    uuid = doc.get('UUID')
                    if not uuid:
                        skipped_count += 1
                        continue  # 继续处理批次中的下一个文档

                    # 1. Process 'intelligence_full_text'
                    raw_data = doc.get('RAW_DATA', {}).get('content')
                    if raw_data:
                        store_full_text.add_document(str(raw_data), uuid)

                    # 2. Process 'intelligence_summary'
                    title = doc.get('EVENT_TITLE', '') or ''
                    brief = doc.get('EVENT_BRIEF', '') or ''
                    text = doc.get('EVENT_TEXT', '') or ''

                    text_summary = f"{title}\n{brief}\n{text}".strip()

                    if text_summary:
                        store_summary.add_document(text_summary, uuid)

                    processed_count += 1

                except Exception as e:
                    print(f"\nError processing doc {doc.get('UUID', 'N/A')}: {e}")
                    skipped_count += 1

                finally:
                    pbar.update(1)

            last_id = batch_docs[-1]['_id']

    print("\n--- Rebuild Complete ---")
    print(f"Successfully processed/updated: {processed_count}")
    print(f"Skipped (e.g., no UUID): {skipped_count}")
    print(f"Total chunks in '{COLLECTION_FULL_TEXT}': {store_full_text.count()}")
    print(f"Total chunks in '{COLLECTION_SUMMARY}': {store_summary.count()}")


def func_search(store_full_text: VectorStoreManager, store_summary: VectorStoreManager):
    """
    Starts an interactive search loop.
    """
    print("\n--- Starting Interactive Search (type 'q' to quit) ---")

    while True:
        query_text = input("\nEnter search query: ")
        if query_text.lower() == 'q':
            break

        mode = input("Search [f]ull text, [s]ummary, or [b]oth (intersection)? (f/s/b): ").lower()
        if mode == 'q':
            break

        results_full = []
        results_summary = []

        if mode in ['f', 'b']:
            results_full = store_full_text.search(
                query_text,
                top_n=SEARCH_TOP_N,
                score_threshold=SEARCH_SCORE_THRESHOLD
            )

        if mode in ['s', 'b']:
            results_summary = store_summary.search(
                query_text,
                top_n=SEARCH_TOP_N,
                score_threshold=SEARCH_SCORE_THRESHOLD
            )

        uuids_full = {res['doc_id'] for res in results_full}
        uuids_summary = {res['doc_id'] for res in results_summary}

        print("\n--- Search Results ---")

        if mode == 'f':
            print(f"Found {len(uuids_full)} matching UUIDs in FULL TEXT (threshold > {SEARCH_SCORE_THRESHOLD}):")
            for res in results_full:
                print(f"  - UUID: {res['doc_id']} (Score: {res['score']:.4f})")
                print(f"    Chunk: {res['chunk_text'][:80]}...")

        elif mode == 's':
            print(f"Found {len(uuids_summary)} matching UUIDs in SUMMARY (threshold > {SEARCH_SCORE_THRESHOLD}):")
            for res in results_summary:
                print(f"  - UUID: {res['doc_id']} (Score: {res['score']:.4f})")
                print(f"    Chunk: {res['chunk_text'][:80]}...")

        elif mode == 'b':
            intersection = uuids_full.intersection(uuids_summary)
            print(f"Found {len(intersection)} matching UUIDs in BOTH (Intersection):")
            print(f"  {intersection}")

            print(f"\nDetails (Full Text Hits): {uuids_full}")
            print(f"Details (Summary Hits):   {uuids_summary}")

        else:
            print("Invalid mode. Please enter 'f', 's', or 'b'.")


# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Vector Index Rebuild and Search Tool")
    parser.add_argument(
        'actions',
        nargs='+',
        choices=['rebuild', 'search'],
        help="Action(s) to perform. 'rebuild' rebuilds the index. 'search' starts interactive search."
    )
    args = parser.parse_args()

    # --- Handle Rebuild (Pre-delete) ---
    if 'rebuild' in args.actions:
        print("--- REBUILD ACTION REQUESTED ---")
        confirm = input(
            "ARE YOU SURE? This will DELETE existing data "
            f"in '{COLLECTION_FULL_TEXT}' and '{COLLECTION_SUMMARY}'. (type 'yes' to confirm): "
        )
        if confirm.lower() == 'yes':
            print("Proceeding with deletion...")
            try:
                # Lazy import chromadb just for this
                import chromadb
                temp_client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
                temp_client.delete_collection(name=COLLECTION_FULL_TEXT)
                print(f"Deleted old collection: {COLLECTION_FULL_TEXT}")
                temp_client.delete_collection(name=COLLECTION_SUMMARY)
                print(f"Deleted old collection: {COLLECTION_SUMMARY}")
            except Exception as e:
                print(f"Note: Could not delete collections (may not exist): {e}")
            print("Old collections cleared.")
        else:
            print("Rebuild cancelled.")
            if 'search' not in args.actions:
                sys.exit(0)

    # --- Initialize Service (Non-blocking) ---
    print("\n[Main]: Initializing vector service (non-blocking)...")
    service = VectorDBService(
        db_path=VECTOR_DB_PATH,
        model_name=MODEL_NAME
    )

    # --- Wait for Service to be Ready ---
    print("[Main]: Waiting for shared components (model, db client) to load...")
    while True:
        status_info = service.get_status()
        status = status_info["status"]

        if status == "ready":
            print("[Main]: Vector service is READY.")
            break
        if status == "error":
            print(f"[Main]: FATAL: Failed to load service: {status_info['error']}")
            sys.exit(1)

        print("[Main]: Loader thread is working...")
        time.sleep(2)

    # --- Service is loaded. Get store managers (fast) ---
    print("[Main]: Initializing vector store managers (this is fast)...")
    try:
        store_full = service.get_store(
            collection_name=COLLECTION_FULL_TEXT,
            chunk_size=512
        )

        store_summary = service.get_store(
            collection_name=COLLECTION_SUMMARY,
            chunk_size=256  # Summaries are shorter
        )
    except Exception as e:
        print(f"[Main]: FATAL: Failed to get store managers: {e}")
        sys.exit(1)

    # --- Route to Core Logic ---
    if 'rebuild' in args.actions:
        func_rebuild(store_full, store_summary)

    if 'search' in args.actions:
        func_search(store_full, store_summary)

    print("\n[Main]: Tool finished.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e))
        print(traceback.format_exc())
