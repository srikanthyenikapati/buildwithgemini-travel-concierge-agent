# Copyright 2026 Google LLC
# Seed script for Travel Concierge Agent Firestore collection

from google.cloud import firestore

# CRITICAL: Hardcode the GCP Project ID as a string.
# Do NOT use google.auth.default() or GOOGLE_CLOUD_PROJECT env var,
# as those return the project NUMBER on Agent Platform, breaking Firestore resolution.
FIRESTORE_PROJECT = "qwiklabs-gcp-02-a60a9ebc8d0d"

SAMPLE_DESTINATIONS = [
    {
        "id": "tokyo-japan",
        "name": "Tokyo",
        "country": "Japan",
        "description": "A bustling metropolis blending ultra-modern skyscrapers with historic temples and world-class culinary scenes.",
        "recommended_days": 5,
        "tags": ["culture", "food", "city", "shopping"],
        "avg_daily_budget_usd": 180.0,
    },
    {
        "id": "paris-france",
        "name": "Paris",
        "country": "France",
        "description": "The City of Light, famed for art galleries, iconic landmarks like the Eiffel Tower, and historic cafes.",
        "recommended_days": 4,
        "tags": ["art", "romance", "culture", "food"],
        "avg_daily_budget_usd": 220.0,
    },
    {
        "id": "kyoto-japan",
        "name": "Kyoto",
        "country": "Japan",
        "description": "Japan's cultural capital, featuring serene Zen gardens, bamboo groves, and thousands of classical Buddhist temples.",
        "recommended_days": 3,
        "tags": ["temples", "nature", "culture", "history"],
        "avg_daily_budget_usd": 150.0,
    },
    {
        "id": "rome-italy",
        "name": "Rome",
        "country": "Italy",
        "description": "An open-air museum filled with ancient Roman ruins, Renaissance architecture, and legendary gelato.",
        "recommended_days": 4,
        "tags": ["history", "food", "architecture", "culture"],
        "avg_daily_budget_usd": 170.0,
    },
    {
        "id": "reykjavik-iceland",
        "name": "Reykjavik",
        "country": "Iceland",
        "description": "Gateway to geysers, thermal lagoons, volcanic landscapes, and spectacular Northern Lights displays.",
        "recommended_days": 3,
        "tags": ["nature", "adventure", "scenery", "wellness"],
        "avg_daily_budget_usd": 250.0,
    },
]


def seed_database():
    print(f"Connecting to Firestore for project: '{FIRESTORE_PROJECT}'...")
    db = firestore.Client(project=FIRESTORE_PROJECT)
    collection_ref = db.collection("destinations")

    for dest in SAMPLE_DESTINATIONS:
        doc_id = dest["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(dest)
        print(f"  ✓ Seeded destination: {dest['name']} ({doc_id})")

    print("\nFirestore seeding complete!")


if __name__ == "__main__":
    seed_database()
