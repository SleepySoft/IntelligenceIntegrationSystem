#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Vector Index Rebuild and Search Tool (Client Mode)

This tool connects to the standalone VectorDB Service via HTTP to rebuild indexes
from MongoDB and perform interactive searches.

It uses the IntelligenceVectorDBEngine to ensure data format and metadata
consistency with the main service.
"""

import sys
import time
import argparse
import traceback
import copy
from typing import Optional

# --- New Architecture Imports ---
from ServiceComponent.IntelligenceHubDefines import ArchivedData
from VectorDB.VectorDBClient import VectorDBClient, RemoteCollection
from ServiceComponent.IntelligenceVectorDBEngine import IntelligenceVectorDBEngine

# --- Configuration ---
# MongoDB (Source)
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB_NAME = "IntelligenceIntegrationSystem"
MONGO_COLLECTION_NAME = "intelligence_archived"

# VectorDB Service (Destination)
VECTOR_SERVICE_URL = "http://localhost:8001"

# Collections Configuration
# Note: Chunk configs are handled by the server/client creation logic
COLLECTION_FULL_TEXT = "intelligence_full_text"
CONFIG_FULL_TEXT = {"chunk_size": 512, "chunk_overlap": 50}

COLLECTION_SUMMARY = "intelligence_summary"
CONFIG_SUMMARY = {"chunk_size": 256, "chunk_overlap": 30}

SEARCH_SCORE_THRESHOLD = 0.5
SEARCH_TOP_N = 5


# --- Helper Functions ---

def connect_to_mongo():
    """Connects to MongoDB and returns the collection object."""
    from pymongo import MongoClient
    try:
        client = MongoClient(MONGO_URI)
        client.server_info()  # Test connection
        db = client[MONGO_DB_NAME]
        collection = db[MONGO_COLLECTION_NAME]
        print(f"[Mongo] Successfully connected to: {MONGO_DB_NAME}.{MONGO_COLLECTION_NAME}")
        return collection
    except Exception as e:
        print(f"[Mongo] Failed to connect: {e}")
        return None


def safe_get_collection(client: VectorDBClient, name: str, config: dict) -> RemoteCollection:
    """Helper to create or get a collection via Client."""
    # This creates the collection if missing, or updates config if exists
    return client.create_collection(
        name=name,
        chunk_size=config["chunk_size"],
        chunk_overlap=config["chunk_overlap"]
    )


# --- Core Logic Functions ---

def func_rebuild(
        engine_full_text: IntelligenceVectorDBEngine,
        engine_summary: IntelligenceVectorDBEngine,
        mode: str = "incremental"
):
    """
    Fetches data from MongoDB and rebuilds vector indexes using the Engine.
    """
    from tqdm import tqdm

    print(f"\n--- Starting Vector Index Build (Mode: {mode}) ---")

    # 1. Handle Recreation Mode
    if mode == "recreate":
        print("Mode 'recreate' selected. Clearing existing remote collections...")
        try:
            engine_full_text.collection.clear()
            engine_summary.collection.clear()
            print("Remote collections cleared.")
        except Exception as e:
            print(f"Error clearing collections: {e}")
            return

    # 2. Connect Source
    collection = connect_to_mongo()
    if collection is None:
        return

    try:
        total_docs = collection.count_documents({})
    except Exception as e:
        print(f"Error counting documents: {e}")
        total_docs = 0

    if total_docs == 0:
        print("No documents found in MongoDB. Nothing to build.")
        return

    print(f"Found {total_docs} documents to process.")

    processed_count = 0
    skipped_error_count = 0
    batch_size = 100
    last_id = None

    with tqdm(total=total_docs, desc="Upserting Documents") as pbar:
        while True:
            # Batch Query
            query = {}
            if last_id:
                query['_id'] = {'$gt': last_id}

            try:
                batch_docs = list(
                    collection.find(query)
                    .sort('_id', 1)
                    .limit(batch_size)
                )
            except Exception as e:
                print(f"\nFATAL: MongoDB Error: {e}")
                break

            if not batch_docs:
                break

            # Process Batch
            for doc in batch_docs:
                try:
                    # 1. Convert Mongo Dict -> ArchivedData Pydantic Model
                    # We remove _id because Pydantic usually doesn't expect the Mongo ObjectId
                    # unless explicitly defined.
                    doc_clean = {k: v for k, v in doc.items() if k != '_id'}

                    try:
                        # Validation might fail if data is corrupt
                        archived_data = ArchivedData(**doc_clean)
                    except Exception as validation_e:
                        # print(f"Validation error for doc {doc.get('UUID')}: {validation_e}")
                        skipped_error_count += 1
                        continue

                    if not archived_data.UUID:
                        skipped_error_count += 1
                        continue

                    # 2. Process 'intelligence_summary'
                    # Standard usage: Engine extracts Title/Brief/Text + Metadata
                    engine_summary.upsert(archived_data, data_type='summary')

                    # 3. Process 'intelligence_full_text'
                    # Requirement: Index the RAW_DATA content.
                    # Trick: We reuse the Engine to ensure Metadata (Time, Rate, etc.) is consistent,
                    # but we temporarily swap the text content to raw_data.
                    raw_content = None
                    if archived_data.RAW_DATA:
                        raw_content = archived_data.RAW_DATA.get('content')

                    if raw_content and isinstance(raw_content, str):
                        # Create a shallow copy to avoid modifying the original used above
                        data_for_full = copy.copy(archived_data)
                        # Override fields so Engine uses Raw Data as the embedding text
                        data_for_full.EVENT_TITLE = ""
                        data_for_full.EVENT_BRIEF = ""
                        data_for_full.EVENT_TEXT = raw_content

                        engine_full_text.upsert(data_for_full, data_type='full')

                    processed_count += 1

                except Exception as e:
                    # print(f"\nError processing doc {doc.get('UUID', 'N/A')}: {e}")
                    skipped_error_count += 1

                finally:
                    pbar.update(1)

            last_id = batch_docs[-1]['_id']

    print("\n--- Build Complete ---")
    print(f"Processed / Upserted: {processed_count}")
    print(f"Skipped (Validation/Empty): {skipped_error_count}")

    # Optional: Print stats from remote
    try:
        stats_s = engine_summary.collection.stats()
        stats_f = engine_full_text.collection.stats()
        print(f"Remote Stats [Summary]:   {stats_s}")
        print(f"Remote Stats [FullText]:  {stats_f}")
    except:
        pass


def func_search(engine_full_text: IntelligenceVectorDBEngine, engine_summary: IntelligenceVectorDBEngine):
    """
    Interactive search using the Engine's query interface.
    """
    print("\n--- Starting Interactive Search (type 'q' to quit) ---")

    while True:
        query_text = input("\nEnter search query: ")
        if query_text.lower() == 'q':
            break

        mode = input("Search [f]ull text, [s]ummary, or [b]oth? (f/s/b): ").lower()
        if mode == 'q':
            break

        # Optional: Add filters for testing metadata support
        # e.g., rate_threshold = 0.5

        results_full = []
        results_summary = []

        try:
            if mode in ['f', 'b']:
                results_full = engine_full_text.query(
                    text=query_text,
                    rate_threshold=SEARCH_SCORE_THRESHOLD  # Using Engine's native filter support
                    # Note: Engine query returns list of dicts from RemoteCollection
                )

            if mode in ['s', 'b']:
                results_summary = engine_summary.query(
                    text=query_text,
                    rate_threshold=SEARCH_SCORE_THRESHOLD
                )
        except Exception as e:
            print(f"Search failed: {e}")
            continue

        # Extract IDs for intersection logic
        # The engine/client returns 'doc_id' in results
        uuids_full = {res['doc_id'] for res in results_full}
        uuids_summary = {res['doc_id'] for res in results_summary}

        print("\n--- Search Results (Top 5) ---")

        def print_res(label, results):
            print(f"[{label}] Found {len(results)} matches:")
            for res in results:
                # Handle potentially different result structures from different versions
                score = res.get('score', 0.0)
                doc_id = res.get('doc_id')
                content = res.get('content', '') or res.get('chunk_text', '')
                meta = res.get('metadata', {})

                print(f"  - UUID: {doc_id} (Score: {score:.4f})")
                print(f"    Time: {meta.get('pub_timestamp', 'N/A')}")
                print(f"    Text: {content[:80].replace(chr(10), ' ')}...")

        if mode == 'f':
            print_res("Full Text", results_full)
        elif mode == 's':
            print_res("Summary", results_summary)
        elif mode == 'b':
            intersection = uuids_full.intersection(uuids_summary)
            print(f"Intersection Count: {len(intersection)}")
            print(f"IDs in Both: {intersection}")
            print("-" * 20)
            print_res("Full Text Hits", results_full)
            print("-" * 20)
            print_res("Summary Hits", results_summary)


# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Vector Index Rebuild Tool (Client Mode)")
    parser.add_argument('actions', nargs='+', choices=['rebuild', 'search'], help="Action to perform")
    parser.add_argument('--full', action='store_true', help="If rebuilding, clear existing data first (Recreate mode).")
    args = parser.parse_args()

    # 1. Initialize Client
    print(f"\n[Main]: Connecting to VectorDB Service at {VECTOR_SERVICE_URL}...")
    client = VectorDBClient(base_url=VECTOR_SERVICE_URL)

    try:
        # Wait for service to be ready (load models etc.)
        client.wait_until_ready(timeout=30)
        print("[Main]: VectorDB Service is connected and ready.")
    except Exception as e:
        print(f"[Main]: Failed to connect to VectorDB Service: {e}")
        sys.exit(1)

    # 2. Initialize Collections & Engines
    # We use safe_get_collection to ensure they exist with correct config
    print("[Main]: Initializing remote collections...")

    try:
        # Summary Collection
        col_summary = safe_get_collection(client, COLLECTION_SUMMARY, CONFIG_SUMMARY)
        engine_summary = IntelligenceVectorDBEngine(col_summary)

        # Full Text Collection
        col_full = safe_get_collection(client, COLLECTION_FULL_TEXT, CONFIG_FULL_TEXT)
        engine_full = IntelligenceVectorDBEngine(col_full)

    except Exception as e:
        print(f"[Main]: Error initializing collections: {e}")
        sys.exit(1)

    # 3. Route Actions
    if 'rebuild' in args.actions:
        mode = "recreate" if args.full else "incremental"
        if mode == "recreate":
            confirm = input(
                f"WARNING: This will DELETE ALL DATA in '{COLLECTION_SUMMARY}' and '{COLLECTION_FULL_TEXT}'. Confirm? (yes/no): ")
            if confirm.lower() != 'yes':
                print("Aborted.")
                sys.exit(0)

        func_rebuild(engine_full, engine_summary, mode)

    if 'search' in args.actions:
        func_search(engine_full, engine_summary)

    print("\n[Main]: Done.")


if __name__ == "__main__":
    main()
