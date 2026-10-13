#!/usr/bin/env python3
"""Seed Firestore with initial operational metrics for ops-analytics-agent."""

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-04-f710916fc1eb"
COLLECTION_NAME = "operational_metrics"

METRICS = [
    {
        "id": "api_latency_p95",
        "name": "API Latency (p95)",
        "category": "infrastructure",
        "value": 142.5,
        "unit": "ms",
        "threshold": 200.0,
        "status": "HEALTHY",
        "description": "95th percentile latency across public API endpoints",
        "tags": ["api", "performance", "sla"],
        "history": [135.0, 138.2, 140.1, 142.5],
    },
    {
        "id": "service_uptime_percentage",
        "name": "Service Availability",
        "category": "infrastructure",
        "value": 99.98,
        "unit": "%",
        "threshold": 99.90,
        "status": "HEALTHY",
        "description": "Monthly overall uptime SLA across production services",
        "tags": ["uptime", "sla", "reliability"],
        "history": [99.99, 99.97, 99.98, 99.98],
    },
    {
        "id": "error_rate_5xx",
        "name": "HTTP 5xx Error Rate",
        "category": "infrastructure",
        "value": 0.85,
        "unit": "%",
        "threshold": 0.50,
        "status": "WARNING",
        "description": "Percentage of outbound HTTP responses resulting in 5xx errors",
        "tags": ["errors", "reliability", "ingress"],
        "history": [0.12, 0.35, 0.65, 0.85],
    },
    {
        "id": "daily_active_users",
        "name": "Daily Active Users",
        "category": "business",
        "value": 48250,
        "unit": "users",
        "threshold": 40000,
        "status": "HEALTHY",
        "description": "Unique active users performing at least one action today",
        "tags": ["business", "growth", "users"],
        "history": [45100, 46200, 47800, 48250],
    },
    {
        "id": "database_cpu_utilization",
        "name": "Primary Database CPU",
        "category": "infrastructure",
        "value": 88.4,
        "unit": "%",
        "threshold": 80.0,
        "status": "CRITICAL",
        "description": "CPU utilization on the primary relational database cluster",
        "tags": ["database", "infrastructure", "capacity"],
        "history": [65.0, 72.3, 81.0, 88.4],
    },
    {
        "id": "checkout_conversion_rate",
        "name": "Checkout Conversion Rate",
        "category": "business",
        "value": 3.82,
        "unit": "%",
        "threshold": 3.50,
        "status": "HEALTHY",
        "description": "Ratio of completed purchase transactions over cart checkouts initiated",
        "tags": ["business", "ecommerce", "revenue"],
        "history": [3.65, 3.70, 3.78, 3.82],
    },
]

def seed():
    print(f"Connecting to Firestore for project: {PROJECT_ID}...")
    db = firestore.Client(project=PROJECT_ID)
    collection = db.collection(COLLECTION_NAME)
    
    for metric in METRICS:
        doc_id = metric["id"]
        data = {k: v for k, v in metric.items() if k != "id"}
        collection.document(doc_id).set(data)
        print(f"  ✓ Seeded metric: {doc_id} -> {data['name']} ({data['value']} {data['unit']}) [{data['status']}]")
    
    print("\n✅ Seeding complete!")

if __name__ == "__main__":
    seed()
