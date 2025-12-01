# demo_usage.py
import time
import os

try:
    from VectorDBClient import VectorDBClient
except ImportError:
    from .VectorDBClient import VectorDBClient


def print_separator(title):
    print(f"\n{'=' * 20} {title} {'=' * 20}")


def print_results(results):
    if not results:
        print("  (No results found)")
        return
    for i, res in enumerate(results):
        print(f"  {i + 1}. [Score: {res['score']:.4f}] ID: {res['doc_id']}")
        print(f"     Text: {res['content'][:80]}...")  # Truncate for display
        print(f"     Meta: {res['metadata']}")
        print("-" * 40)


def main():
    # 1. Initialize Client
    # Points to the local server we just started
    client = VectorDBClient(base_url="http://localhost:8001")
    collection_name = "demo_knowledge_base"

    print_separator("1. Connecting to Service")
    try:
        # Blocks until the server has loaded the heavy AI models
        client.wait_until_ready(timeout=60)
        kb = client.get_collection(collection_name)
        print("Successfully connected to VectorDB Service!")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    # 2. Preparation: Clean slate
    # We clear the collection to ensure the demo is reproducible
    print_separator("2. Cleaning old demo data")
    kb_stats = kb.stats()
    if kb_stats.get('chunk_count', 0) > 0:
        print(f"Found existing data ({kb_stats['chunk_count']} chunks). Clearing...")
        # Note: In a real app, use delete() or clear endpoint carefully
        # Here we assume the server has the clear endpoint or we just ignore
        # (For this demo, let's just proceed to upsert, upsert replaces same IDs)
        pass

        # 3. Insert Test Data (Upsert)
    print_separator("3. Upserting Documents")

    # Dataset: A mix of IT support, HR policies, and General info
    documents = [
        {
            "id": "it_vpn_guide",
            "text": "To connect to the corporate VPN, download the Cisco AnyConnect client. Use your AD credentials. If connection fails, check if your MFA token is active.",
            "meta": {"category": "IT", "type": "guide", "author": "admin"}
        },
        {
            "id": "it_password_policy",
            "text": "Passwords must be 12 characters long, contain special symbols, and expire every 90 days. Contact helpdesk for resets.",
            "meta": {"category": "IT", "type": "policy", "author": "security_team"}
        },
        {
            "id": "hr_leave_policy",
            "text": "Annual leave requests must be submitted 2 weeks in advance via the Workday portal. Unused leave carries over up to 5 days.",
            "meta": {"category": "HR", "type": "policy", "author": "hr_dept"}
        },
        {
            "id": "hr_remote_work",
            "text": "Employees are allowed to work remotely 2 days a week. Please align with your manager on specific days.",
            "meta": {"category": "HR", "type": "policy", "author": "hr_dept"}
        },
        {
            "id": "general_holiday_party",
            "text": "The annual holiday party will be held at the Roof Garden on Dec 20th. Pizza and drinks provided!",
            "meta": {"category": "General", "type": "announcement", "author": "events_team"}
        }
    ]

    for doc in documents:
        print(f"Upserting: {doc['id']}...")
        kb.upsert(
            doc_id=doc["id"],
            text=doc["text"],
            metadata=doc["meta"]
        )

    print("Data ingestion complete.")

    # 4. Semantic Search Tests
    print_separator("4. Test: Semantic Search (No Filters)")

    query = "I can't login to the network"
    print(f"Query: '{query}'")
    # Should match VPN guide or Password policy
    results = kb.search(query, top_n=2)
    print_results(results)

    print_separator("5. Test: Semantic Search + Metadata Filter")

    query = "policy details"
    filter_criteria = {"category": "HR"}  # We only want HR policies, not IT passwords

    print(f"Query: '{query}'")
    print(f"Filter: {filter_criteria} (Expecting HR docs only)")

    results = kb.search(
        query,
        top_n=3,
        filter_criteria=filter_criteria
    )
    print_results(results)

    # Verification: Ensure no IT docs appear
    for r in results:
        if r['metadata'].get('category') == 'IT':
            print("❌ TEST FAILED: Found IT document in HR filter!")

    print_separator("6. Test: Cross-Language Semantic Capabilities")
    # Even though docs are English, the model often understands simple queries in other languages
    query = "如何申请年假 (How to apply for annual leave)"
    print(f"Query: '{query}' (Chinese)")

    results = kb.search(query, top_n=1)
    print_results(results)

    # 7. Backup Test
    print_separator("7. Test: Database Backup")
    import requests

    # We use requests directly here because client.py might not have exposed raw file download helper yet,
    # or we can use the endpoint we defined.
    backup_url = f"{client.base_url}/api/admin/backup"
    print(f"Requesting backup from: {backup_url}")

    try:
        resp = requests.get(backup_url, stream=True)
        if resp.status_code == 200:
            filename = "demo_backup.zip"
            with open(filename, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"✅ Backup successfully downloaded to: {os.path.abspath(filename)}")
            print(f"   Size: {os.path.getsize(filename)} bytes")
        else:
            print(f"❌ Backup failed: {resp.text}")
    except Exception as e:
        print(f"Backup error: {e}")

    print_separator("Demo Complete")


if __name__ == "__main__":
    main()
