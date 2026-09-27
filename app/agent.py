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

import base64
import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import (
    AgentEngineSandboxCodeExecutor,
)
from google.adk.code_executors.code_execution_utils import CodeExecutionInput
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

load_dotenv()

# CRITICAL: Hardcode the GCP Project ID and Bucket Name as strings.
# Do NOT use google.auth.default() or GOOGLE_CLOUD_PROJECT env var,
# as those return the project NUMBER on Agent Platform, breaking Firestore resolution.
FIRESTORE_PROJECT = "qwiklabs-gcp-02-a60a9ebc8d0d"
GCS_BUCKET = "travel-concierge-assets-qwiklabs-gcp-02-a60a9ebc8d0d"
MEMORY_BANK_ID = "425514848240009216"

# Initialize Firestore client with explicit project ID
db = firestore.Client(project=FIRESTORE_PROJECT)

# Configure Agent Engine Sandbox Code Executor from deployment_metadata.json if available
deployment_metadata_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json"
)
code_executor = None
if os.path.exists(deployment_metadata_path):
    try:
        with open(deployment_metadata_path) as f:
            meta = json.load(f)
        remote_id = meta.get("remote_agent_runtime_id")
        if remote_id:
            code_executor = AgentEngineSandboxCodeExecutor(
                agent_engine_resource_name=remote_id
            )
    except Exception as e:
        print(f"Warning: Could not initialize AgentEngineSandboxCodeExecutor: {e}")


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends session events to Memory Bank for durable memory extraction after each agent turn."""
    await callback_context.add_session_to_memory()
    return None


def execute_python_in_sandbox(code: str, tool_context: ToolContext = None) -> str:
    """Executes Python code safely in an Agent Engine sandbox environment.

    Args:
        code: The Python code string to execute.
        tool_context: ADK context automatically injected.

    Returns:
        The output or errors resulting from the Python code execution.
    """
    if not code_executor:
        return "Error: AgentEngineSandboxCodeExecutor is not initialized."

    try:
        inp = CodeExecutionInput(code=code)
        res = code_executor.execute_code(tool_context, inp)
        output = res.stdout
        if res.stderr:
            output += f"\nErrors:\n{res.stderr}"
        return output or "Code executed successfully with no stdout."
    except Exception as e:
        return f"Error executing code in Agent Engine sandbox: {str(e)}"


async def generate_destination_image(
    prompt: str, tool_context: ToolContext = None
) -> str:
    """Generates a travel postcard or scenic image for a destination using gemini-3.1-flash-lite-image in the global region.

    Args:
        prompt: Detailed visual description of the destination image to generate (e.g. 'Kyoto bamboo grove at sunrise').
        tool_context: ADK context automatically injected to save artifacts for Playground display.

    Returns:
        The public HTTPS URL of the generated image hosted in Cloud Storage.
    """
    try:
        genai_client = genai.Client(
            vertexai=True, project=FIRESTORE_PROJECT, location="global"
        )
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"Generate a scenic travel image: {prompt}",
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        candidate_part = response.candidates[0].content.parts[0]
        image_bytes = candidate_part.inline_data.data
        mime_type = candidate_part.inline_data.mime_type or "image/jpeg"

        unique_id = uuid.uuid4().hex[:8]
        filename = f"destination_{unique_id}.jpg"

        # (1) Save with tool_context.save_artifact for Playground Artifacts panel
        if tool_context and hasattr(tool_context, "save_artifact"):
            artifact_part = types.Part.from_bytes(
                data=image_bytes, mime_type=mime_type
            )
            await tool_context.save_artifact(
                filename=filename, artifact=artifact_part
            )

        # (2) Upload image bytes directly to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT)
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET}/{filename}"
        return f"Successfully generated destination image. Public URL: {public_url}"
    except Exception as e:
        return f"Error generating destination image: {str(e)}"


async def generate_destination_video(
    prompt: str, tool_context: ToolContext = None
) -> str:
    """Generates a short travel video clip for a destination or attraction using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Args:
        prompt: Detailed visual description of the destination video to generate (e.g. 'Cinematic drone shot over Kyoto bamboo forest at sunrise').
        tool_context: ADK context automatically injected to save artifacts for Playground display.

    Returns:
        The public HTTPS URL of the generated video hosted in Cloud Storage.
    """
    try:
        genai_client = genai.Client(
            vertexai=True, project=FIRESTORE_PROJECT, location="global"
        )
        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=f"Generate a short scenic travel video: {prompt}",
            response_format={
                "type": "video",
                "aspect_ratio": "16:9",
            },
        )

        video_data = interaction.output_video.data
        if isinstance(video_data, str):
            video_bytes = base64.b64decode(video_data)
        else:
            video_bytes = video_data
        mime_type = getattr(interaction.output_video, "mime_type", None) or "video/mp4"

        unique_id = uuid.uuid4().hex[:8]
        filename = f"destination_video_{unique_id}.mp4"

        # (1) Save with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        if tool_context and hasattr(tool_context, "save_artifact"):
            artifact_part = types.Part.from_bytes(
                data=video_bytes, mime_type=mime_type
            )
            await tool_context.save_artifact(
                filename=filename, artifact=artifact_part
            )

        # (2) Upload video bytes directly to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT)
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET}/{filename}"
        return f"Successfully generated destination video. Public URL: {public_url}"
    except Exception as e:
        return f"Error generating destination video: {str(e)}"


def geocode_address(address: str) -> str:
    """Uses Google Maps Geocoding API to turn a street address or location name into latitude and longitude coordinates.

    Args:
        address: The address or location to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA' or 'Eiffel Tower, Paris').

    Returns:
        Formatted string containing the matched address, latitude, and longitude.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY is not configured in environment variables."

    try:
        encoded_addr = urllib.parse.quote(address)
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_addr}&key={api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            status = data.get("status")
            if status != "OK" or not data.get("results"):
                return f"Geocoding failed for address '{address}'. Status: {status}"

            res_0 = data["results"][0]
            formatted_addr = res_0.get("formatted_address", address)
            location = res_0.get("geometry", {}).get("location", {})
            lat = location.get("lat")
            lng = location.get("lng")

            return (
                f"Geocoding Result for '{address}':\n"
                f"- Formatted Address: {formatted_addr}\n"
                f"- Location Coordinates: latitude={lat}, longitude={lng}"
            )
    except Exception as e:
        return f"Error calling Geocoding API for '{address}': {str(e)}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
) -> str:
    """Uses Google Places API (New) searchNearby REST endpoint to find nearby places of a given type around coordinates.

    Args:
        latitude: Latitude coordinate of search center (e.g. 37.42248).
        longitude: Longitude coordinate of search center (e.g. -122.08559).
        place_type: Type of place to search for (e.g. 'restaurant', 'cafe', 'tourist_attraction', 'museum', 'lodging').
        radius_meters: Search radius around center coordinates in meters (default: 1000.0).

    Returns:
        Formatted list of nearby places including name, address, rating, and coordinates.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY is not configured in environment variables."

    try:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        payload = {
            "includedTypes": [place_type.strip().lower()],
            "maxResultCount": 5,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(radius_meters),
                }
            },
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.rating",
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            places = data.get("places", [])
            if not places:
                return f"No nearby places of type '{place_type}' found within {radius_meters}m of [{latitude}, {longitude}]."

            results = []
            for p in places:
                name = p.get("displayName", {}).get("text", "N/A")
                addr = p.get("formattedAddress", "N/A")
                loc = p.get("location", {})
                rating = p.get("rating", "N/A")
                p_lat = loc.get("latitude")
                p_lng = loc.get("longitude")
                results.append(
                    f"- {name} (Rating: {rating}/5.0)\n  Address: {addr}\n  Location: latitude={p_lat}, longitude={p_lng}"
                )

            return (
                f"Found {len(places)} nearby places of type '{place_type}' near [{latitude}, {longitude}]:\n"
                + "\n".join(results)
            )
    except Exception as e:
        return f"Error calling Places API (New) for [{latitude}, {longitude}]: {str(e)}"


def search_destinations(query: str = "", tag: str = "") -> str:
    """Searches travel destinations stored in the Firestore database.

    Args:
        query: Optional search keyword to match against destination names, countries, or descriptions.
        tag: Optional tag to filter destinations (e.g. 'food', 'culture', 'nature', 'history').

    Returns:
        A formatted list of matching destinations.
    """
    collection_ref = db.collection("destinations")
    docs = collection_ref.stream()

    matches = []
    for doc in docs:
        data = doc.to_dict()
        name = data.get("name", "")
        country = data.get("country", "")
        desc = data.get("description", "")
        tags = data.get("tags", [])

        # Check filter matching
        matches_query = not query or (
            query.lower() in name.lower()
            or query.lower() in country.lower()
            or query.lower() in desc.lower()
        )
        matches_tag = not tag or any(tag.lower() in t.lower() for t in tags)

        if matches_query and matches_tag:
            matches.append(
                f"- ID: {doc.id} | {name}, {country} ({data.get('recommended_days', 3)} days, ~${data.get('avg_daily_budget_usd', 0)}/day) - Tags: {', '.join(tags)}\n  {desc}"
            )

    if not matches:
        return f"No destinations found matching query='{query}', tag='{tag}'."

    return "Found matching destinations:\n" + "\n".join(matches)


def get_destination_details(destination_id: str) -> str:
    """Gets detailed information for a specific travel destination by ID from Firestore.

    Args:
        destination_id: The unique ID of the destination (e.g. 'tokyo-japan', 'paris-france').

    Returns:
        Destination details or error message if not found.
    """
    doc_ref = db.collection("destinations").document(destination_id)
    doc = doc_ref.get()

    if not doc.exists:
        return f"Destination with ID '{destination_id}' not found in database."

    data = doc.to_dict()
    return (
        f"Destination Details for '{data.get('name')}':\n"
        f"- ID: {doc.id}\n"
        f"- Name: {data.get('name')}\n"
        f"- Country: {data.get('country')}\n"
        f"- Description: {data.get('description')}\n"
        f"- Recommended Duration: {data.get('recommended_days')} days\n"
        f"- Estimated Daily Budget: ${data.get('avg_daily_budget_usd')} USD\n"
        f"- Tags: {', '.join(data.get('tags', []))}"
    )


def add_destination(
    destination_id: str,
    name: str,
    country: str,
    description: str,
    recommended_days: int,
    tags: str,
    avg_daily_budget_usd: float,
) -> str:
    """Adds a new travel destination or updates an existing one in the Firestore database.

    Args:
        destination_id: Unique string ID for the destination (e.g. 'kyoto-japan', 'barcelona-spain').
        name: Name of the destination city or region (e.g. 'Kyoto').
        country: Country where the destination is located (e.g. 'Japan').
        description: Brief overview of key highlights and attractions.
        recommended_days: Recommended number of days to stay.
        tags: Comma-separated list of category tags (e.g. 'temples, food, culture').
        avg_daily_budget_usd: Estimated average daily budget in USD.

    Returns:
        Success message confirming the destination was saved to Firestore.
    """
    tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
    doc_data = {
        "id": destination_id,
        "name": name,
        "country": country,
        "description": description,
        "recommended_days": recommended_days,
        "tags": tag_list,
        "avg_daily_budget_usd": avg_daily_budget_usd,
    }

    db.collection("destinations").document(destination_id).set(doc_data)
    return f"Successfully saved destination '{name}' ({destination_id}) to Firestore database."


def get_live_weather(location: str) -> str:
    """Fetches live, real-time weather conditions and temperatures for any city or location worldwide.

    Args:
        location: The name of the city or destination (e.g. 'Tokyo', 'Paris', 'New York').

    Returns:
        A summary of current weather conditions, temperature, humidity, and wind speed.
    """
    try:
        encoded_loc = urllib.parse.quote(location)
        url = f"https://wttr.in/{encoded_loc}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            current = data["current_condition"][0]
            temp_c = current["temp_C"]
            temp_f = current["temp_F"]
            desc = current["weatherDesc"][0]["value"].strip()
            humidity = current["humidity"]
            wind = current["windspeedKmph"]
            return (
                f"Current live weather for {location}:\n"
                f"- Temperature: {temp_c}°C ({temp_f}°F)\n"
                f"- Condition: {desc}\n"
                f"- Humidity: {humidity}%\n"
                f"- Wind Speed: {wind} km/h"
            )
    except Exception as e:
        return f"Unable to fetch live weather for '{location}': {str(e)}"


def convert_currency(
    amount: float, from_currency: str = "USD", to_currency: str = "EUR"
) -> str:
    """Converts money from one currency to another using real live exchange rates published by the European Central Bank.

    Args:
        amount: The monetary amount to convert (e.g. 100.0, 5000.0).
        from_currency: 3-letter currency code to convert from (e.g. 'USD', 'EUR', 'GBP').
        to_currency: 3-letter currency code to convert to (e.g. 'JPY', 'EUR', 'CAD', 'GBP').

    Returns:
        A formatted string with the converted amount and current exchange rate.
    """
    from_curr = from_currency.upper().strip()
    to_curr = to_currency.upper().strip()

    if from_curr == to_curr:
        return f"{amount:.2f} {from_curr} is equal to {amount:.2f} {to_curr}."

    try:
        url = f"https://api.frankfurter.app/latest?amount={amount}&from={from_curr}&to={to_curr}"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            rates = data.get("rates", {})
            if to_curr not in rates:
                return f"Unable to find exchange rate for target currency '{to_curr}'."

            converted = rates[to_curr]
            rate = converted / amount if amount > 0 else 0.0
            return (
                f"Currency Conversion ({data.get('date', 'latest')}):\n"
                f"- {amount:,.2f} {from_curr} = {converted:,.2f} {to_curr}\n"
                f"- Exchange Rate: 1 {from_curr} = {rate:.4f} {to_curr}"
            )
    except Exception as e:
        return f"Unable to convert {from_curr} to {to_curr}: {str(e)}"


def get_current_time(query: str) -> str:
    """Simulates getting current time for a location.

    Args:
        query: City or timezone query.

    Returns:
        Current time string.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    elif "tokyo" in query.lower():
        tz_identifier = "Asia/Tokyo"
    elif "paris" in query.lower():
        tz_identifier = "Europe/Paris"
    else:
        tz_identifier = "UTC"

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


# Initialize A2UI Schema Manager with version 0.8 and Basic Catalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are the Travel Concierge Agent. You assist travelers in discovering destinations, "
        "remembering personal traveler preferences and critical health/dietary details (specifically ALL user allergies, "
        "food intolerances, and dietary restrictions) across sessions, generating visual postcard images, "
        "geocoding addresses, finding nearby places, checking live weather, converting currencies, "
        "executing Python calculations in an Agent Engine sandbox, planning trip itineraries, and "
        "managing travel options stored in Firestore. "
        "ALWAYS pay strict attention to any allergies mentioned by the user, ensure they are stored in memory, "
        "and proactively filter or warn about dining and travel recommendations that may contain listed allergens."
    ),
    workflow_description="Analyze the user request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
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
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=code_executor,
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        execute_python_in_sandbox,
        generate_destination_image,
        generate_destination_video,
        geocode_address,
        find_nearby_places,
        search_destinations,
        get_destination_details,
        add_destination,
        get_live_weather,
        convert_currency,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)


def memory_bank_service_builder():
    """Builds the VertexAiMemoryBankService for deployed container runs on Agent Runtime."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT,
        location="us-central1",
        agent_engine_id=MEMORY_BANK_ID,
    )


app = App(
    root_agent=root_agent,
    name="app",
)
