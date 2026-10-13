# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import os
import time
from typing import Any, Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types
import requests

from .a2ui_utils import a2ui_callback

MODEL = "gemini-3.8-flash"
PROJECT_ID = "qwiklabs-gcp-04-f710916fc1eb"
COLLECTION_NAME = "operational_metrics"
BUCKET_NAME = "ops-analytics-charts-qwiklabs-gcp-04-f710916fc1eb"
AGENT_ENGINE_RESOURCE = "projects/411660209632/locations/us-east1/reasoningEngines/8714655494473515008"
SANDBOX_RESOURCE = "projects/411660209632/locations/us-east1/reasoningEngines/8714655494473515008/sandboxEnvironments/2360633872649027584"

# Initialize Firestore and Storage clients
db = firestore.Client(project=PROJECT_ID)
storage_client = storage.Client(project=PROJECT_ID)

# Configure Sandbox Code Executor using created sandbox from Agent Engine
sandbox_executor = AgentEngineSandboxCodeExecutor(
    sandbox_resource_name=SANDBOX_RESOURCE
)


def list_operational_metrics(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List operational metrics from Firestore, optionally filtered by category.

    Args:
        category: Optional category filter, e.g. 'infrastructure' or 'business'.

    Returns:
        A list of dictionaries, each containing metric details (id, name, value, unit, threshold, status, tags).
    """
    col = db.collection(COLLECTION_NAME)
    if category:
        docs = col.where("category", "==", category.strip().lower()).stream()
    else:
        docs = col.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)
    return results


def get_operational_metric(metric_id: str) -> Dict[str, Any]:
    """Retrieve details and historical data for a specific operational metric.

    Args:
        metric_id: The unique identifier of the metric (e.g. 'api_latency_p95', 'error_rate_5xx').

    Returns:
        A dictionary with the metric's details, or an error message if not found.
    """
    doc = db.collection(COLLECTION_NAME).document(metric_id.strip()).get()
    if not doc.exists:
        return {"error": f"Metric with ID '{metric_id}' not found."}
    data = doc.to_dict()
    data["id"] = doc.id
    return data


def record_operational_metric(
    metric_id: str,
    name: str,
    category: str,
    value: float,
    unit: str,
    threshold: float,
    status: str,
    description: str = "",
) -> Dict[str, Any]:
    """Record or update an operational metric in Firestore.

    Args:
        metric_id: Unique identifier for the metric (e.g., 'cache_hit_ratio').
        name: Human-readable name of the metric (e.g., 'Cache Hit Ratio').
        category: Category of the metric ('infrastructure' or 'business').
        value: Current numeric reading/value of the metric.
        unit: Measurement unit (e.g., 'ms', '%', 'users', 'qps').
        threshold: The threshold or SLA target value.
        status: Status indicator, typically 'HEALTHY', 'WARNING', or 'CRITICAL'.
        description: Brief description of what this metric measures.

    Returns:
        A dictionary confirming the saved metric data.
    """
    doc_ref = db.collection(COLLECTION_NAME).document(metric_id.strip())
    existing = doc_ref.get()

    history = []
    if existing.exists:
        existing_data = existing.to_dict()
        history = existing_data.get("history", [])

    history.append(value)
    if len(history) > 10:
        history = history[-10:]

    data = {
        "name": name,
        "category": category.lower(),
        "value": value,
        "unit": unit,
        "threshold": threshold,
        "status": status.upper(),
        "description": description,
        "history": history,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    doc_ref.set(data, merge=True)
    data["id"] = metric_id.strip()
    return {"message": f"Metric '{metric_id}' successfully saved.", "metric": data}


def analyze_metric_trend(metric_id: str) -> Dict[str, Any]:
    """Analyze the historical trend, calculate statistics, and detect threshold anomalies for a metric.

    Args:
        metric_id: The unique ID of the metric to analyze (e.g., 'error_rate_5xx', 'database_cpu_utilization').

    Returns:
        A dictionary containing statistical summary:
        - current_value: latest reading
        - historical_readings: list of past data points
        - min_value, max_value, mean_value, change_pct (percentage change from first to last reading)
        - direction: 'RISING', 'FALLING', or 'STABLE'
        - threshold_breached: bool indicating whether current value breaches threshold
        - anomaly_detected: bool indicating whether the metric is trending upward toward/beyond threshold
    """
    doc = db.collection(COLLECTION_NAME).document(metric_id.strip()).get()
    if not doc.exists:
        return {"error": f"Metric '{metric_id}' not found."}

    data = doc.to_dict()
    history = data.get("history", [])
    current_val = float(data.get("value", 0.0))
    threshold = float(data.get("threshold", 0.0))
    unit = data.get("unit", "")
    status = data.get("status", "HEALTHY")

    if not history:
        history = [current_val]

    min_val = min(history)
    max_val = max(history)
    mean_val = sum(history) / len(history)

    first_val = history[0]
    last_val = history[-1]
    if first_val != 0:
        change_pct = ((last_val - first_val) / abs(first_val)) * 100.0
    else:
        change_pct = 0.0

    if change_pct > 2.0:
        direction = "RISING"
    elif change_pct < -2.0:
        direction = "FALLING"
    else:
        direction = "STABLE"

    # Threshold evaluation
    threshold_breached = False
    if "error" in metric_id or "latency" in metric_id or "cpu" in metric_id:
        threshold_breached = current_val > threshold
    elif "uptime" in metric_id or "conversion" in metric_id:
        threshold_breached = current_val < threshold
    else:
        threshold_breached = status in ("WARNING", "CRITICAL")

    anomaly_detected = threshold_breached or (direction == "RISING" and "error" in metric_id)

    return {
        "metric_id": metric_id,
        "name": data.get("name"),
        "unit": unit,
        "current_value": current_val,
        "threshold": threshold,
        "readings_count": len(history),
        "history": history,
        "min_value": round(min_val, 2),
        "max_value": round(max_val, 2),
        "mean_value": round(mean_val, 2),
        "change_pct": round(change_pct, 2),
        "trend_direction": direction,
        "threshold_breached": threshold_breached,
        "anomaly_detected": anomaly_detected,
        "status": status,
    }


def fetch_github_repository_metrics(repo: str = "google/adk-docs") -> Dict[str, Any]:
    """Fetch live operational health and repository metrics from GitHub's public API.

    Args:
        repo: Repository slug in the format 'owner/repo' (defaults to 'google/adk-docs').

    Returns:
        A dictionary with live repo metrics (stars, open issues, forks, watchers, last push date).
    """
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "ops-analytics-agent"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    clean_repo = repo.strip().replace("https://github.com/", "").rstrip("/")
    url = f"https://api.github.com/repos/{clean_repo}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return {"error": f"Failed to fetch metrics for {clean_repo}: HTTP {resp.status_code}", "details": resp.text[:200]}

        data = resp.json()
        return {
            "repository": data.get("full_name"),
            "open_issues_count": data.get("open_issues_count"),
            "stargazers_count": data.get("stargazers_count"),
            "forks_count": data.get("forks_count"),
            "watchers_count": data.get("watchers_count"),
            "pushed_at": data.get("pushed_at"),
            "updated_at": data.get("updated_at"),
            "is_archived": data.get("archived", False),
            "description": data.get("description"),
        }
    except Exception as e:
        return {"error": f"Exception while fetching GitHub repository metrics: {str(e)}"}


def geocode_address(address: str) -> Dict[str, Any]:
    """Convert a physical address or place name into geographic coordinates (latitude and longitude).

    Args:
        address: The address, city, or location name to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A dictionary containing formatted address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not set."}

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address.strip(), "key": api_key}

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") != "OK":
            return {"error": f"Geocoding failed: {data.get('status')}", "details": data.get("error_message")}

        result = data["results"][0]
        location = result["geometry"]["location"]
        return {
            "formatted_address": result.get("formatted_address"),
            "latitude": location.get("lat"),
            "longitude": location.get("lng"),
            "place_id": result.get("place_id"),
        }
    except Exception as e:
        return {"error": f"Exception during geocoding: {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
    max_results: int = 5,
) -> Dict[str, Any]:
    """Find nearby places of a given type around a coordinate using Places API (New).

    Args:
        latitude: Latitude coordinate of the center point.
        longitude: Longitude coordinate of the center point.
        place_type: Primary place type to search for (e.g. 'restaurant', 'cafe', 'bank', 'hospital', 'data_center').
        radius_meters: Search radius in meters (default 1000.0m, up to 50000.0m).
        max_results: Maximum number of places to return (between 1 and 20, default 5).

    Returns:
        A dictionary with a list of places containing name, formatted address, and coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not set."}

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types",
    }
    payload = {
        "includedTypes": [place_type.strip().lower()],
        "maxResultCount": min(max(max_results, 1), 20),
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": float(radius_meters),
            }
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            return {"error": f"Places search failed: HTTP {resp.status_code}", "details": resp.text[:200]}

        data = resp.json()
        places = []
        for p in data.get("places", []):
            display_name = p.get("displayName", {}).get("text", "")
            loc = p.get("location", {})
            places.append({
                "name": display_name,
                "address": p.get("formattedAddress", ""),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
            })
        return {
            "center": {"latitude": latitude, "longitude": longitude},
            "place_type": place_type,
            "places_count": len(places),
            "places": places,
        }
    except Exception as e:
        return {"error": f"Exception during nearby places search: {str(e)}"}


async def generate_metric_visual_badge(
    metric_name: str,
    status: str,
    current_value: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Generate an operational visual status badge or infographic snapshot for a metric.

    Uses the gemini-3.1-flash-lite-image model in the global region to generate an image,
    saves it to the local playground Artifacts panel via tool_context.save_artifact,
    and uploads the image bytes to the public Cloud Storage bucket, returning the public HTTPS URL.

    Args:
        metric_name: Name of the metric (e.g. 'API Latency (p95)', 'HTTP 5xx Error Rate', 'Primary Database CPU').
        status: Current health status ('HEALTHY', 'WARNING', or 'CRITICAL').
        current_value: Formatted value with unit (e.g. '142.5 ms', '0.85%', '88.4%').
        tool_context: ADK tool context for saving artifacts into the playground.

    Returns:
        A dictionary with the public image URL, artifact filename, and generation metadata.
    """
    prompt = (
        f"A clean, modern, flat vector tech badge and operational status card for '{metric_name}'. "
        f"Prominently displays the status '{status}' and reading '{current_value}'. "
        f"Color scheme matching status: emerald green for HEALTHY, amber orange for WARNING, vibrant red for CRITICAL. "
        "Professional SRE monitoring dashboard aesthetic, minimalist UI icon, white background, high resolution."
    )

    try:
        # Generate image using gemini-3.1-flash-lite-image in global region
        genai_client = genai.Client(vertexai=True, location="global")
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=[types.Modality.IMAGE]
            ),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        for candidate in response.candidates:
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.data:
                        image_bytes = part.inline_data.data
                        if part.inline_data.mime_type:
                            mime_type = part.inline_data.mime_type
                        break
            if image_bytes:
                break

        if not image_bytes:
            return {"error": "Failed to extract generated image bytes from model response."}

        # 1. Save with tool_context.save_artifact for Playground Artifacts panel
        ext = "png" if "png" in mime_type else "jpg"
        clean_name = metric_name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
        filename = f"{clean_name}_{int(time.time())}.{ext}"

        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        artifact_version = await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same bytes to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return {
            "metric_name": metric_name,
            "status": status,
            "current_value": current_value,
            "public_image_url": public_url,
            "artifact_filename": filename,
            "artifact_version": artifact_version,
            "message": "Image badge successfully generated, saved as artifact, and published to Cloud Storage.",
        }
    except Exception as e:
        return {"error": f"Exception during image generation or upload: {str(e)}"}


# WRITE: after each turn, send the session to Memory Bank for extraction.
async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are an Operational Data Analytics & Location Intelligence Assistant. "
        "You remember the user's stated preferences, operational thresholds, user allergies or dietary preferences, "
        "and facts from previous conversations and use them to personalize your responses. "
        "You help engineers, SREs, and business stakeholders inspect operational health, query system metrics, "
        "analyze metric trends and anomalies, generate visual status badges, geocode addresses, and find nearby operational facilities using your tools. "
        "You can also safely write and execute Python code in your Agent Engine sandbox to perform complex calculations, simulations, or statistical aggregations."
    ),
    workflow_description=(
        "When answering queries: "
        "- Use `list_operational_metrics` to discover current operational metrics or filter by category ('infrastructure', 'business'). "
        "- Use `get_operational_metric` to view detailed readings for a specific metric. "
        "- Use `analyze_metric_trend` whenever the user asks about trends, historical changes, anomalies, statistics, or performance over time. "
        "- Use `generate_metric_visual_badge` when asked to generate, create, visualize, or produce an image or visual badge for a metric or operational status. "
        "- Use `fetch_github_repository_metrics` when asked about live repository metrics, open issue backlogs, community activity, or external devops health. "
        "- Use `geocode_address` to resolve physical addresses, data centers, or regional offices into geographic coordinates. "
        "- Use `find_nearby_places` to locate facilities, amenities, or nearby establishments given latitude and longitude. "
        "- Use `record_operational_metric` when the user asks to log, update, or adjust a metric or threshold. "
        "- Highlight metrics that are in WARNING or CRITICAL status or have breached thresholds. "
        "- When performing mathematical computations, statistical calculations, percentiles, or code execution tasks, write executable Python code inside a ```python ... ``` code block. "
        "Analyze the request and return structured UI when appropriate."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=sandbox_executor,
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        list_operational_metrics,
        get_operational_metric,
        analyze_metric_trend,
        generate_metric_visual_badge,
        fetch_github_repository_metrics,
        geocode_address,
        find_nearby_places,
        record_operational_metric,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)


