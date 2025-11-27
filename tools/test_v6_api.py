#!/usr/bin/env python3
"""Test script for Life360 v6/devices API - rapid iteration testing.

Uses curl_cffi to impersonate Android Chrome for better TLS fingerprinting
and establishes a session by calling other API endpoints first.
"""

import asyncio
from curl_cffi.requests import AsyncSession
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

# Configuration - UPDATE THESE
# Note: Life360 v6 API uses bearer token authentication only (no username/password needed)
BEARER_TOKEN = "your_bearer_token_here"  # Get from HA logs or network capture
DEVICE_ID = ""  # Optional: x-device-id header from network capture
CIRCLE_ID = ""  # Optional: Circle ID for session establishment (from HA config or /v3/circles)

# API endpoints
API_BASE = "https://api-cloudfront.life360.com"
V6_DEVICES_URL = f"{API_BASE}/v6/devices"


async def establish_session(session, bearer_token, device_id, circle_id):
    """
    Establish a session by calling other Life360 API endpoints first.

    This mimics the mobile app behavior of making several API calls before
    accessing v6/devices, which helps establish session cookies and avoid
    Cloudflare blocks.
    """
    if not circle_id:
        print("⚠️  No CIRCLE_ID provided - skipping session establishment")
        return

    print("\n" + "="*80)
    print("ESTABLISHING SESSION (mimicking mobile app behavior)")
    print("="*80)

    # Common headers generator for all requests
    def get_headers(ce_type):
        ce_id = str(uuid.uuid4())
        ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        headers = {
            "Accept": "application/json",
            "Accept-Language": "en_AU",
            "User-Agent": "com.life360.android.safetymapd/KOKO/25.45.0 android/12",
            "Authorization": f"Bearer {bearer_token}",
            "ce-specversion": "1.0",
            "ce-type": ce_type,
            "ce-id": ce_id,
            "ce-time": ce_time,
            "Accept-Encoding": "gzip",
        }

        if device_id:
            headers["x-device-id"] = device_id
            headers["ce-source"] = f"/ANDROID/12/samsung-SM-N920I/{device_id}"

        return headers

    # Step 1: Call /v4/circles/{id}/members (common first call)
    print("\n1. Calling /v4/circles/{id}/members...")
    try:
        members_url = f"{API_BASE}/v4/circles/{circle_id}/members"
        resp = await session.get(
            members_url,
            headers=get_headers("com.life360.circle.members.v1")
        )
        print(f"   Response: {resp.status_code} ({len(resp.text)} bytes)")
        if resp.status_code == 200:
            print(f"   ✓ Session cookie received")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    await asyncio.sleep(0.5)  # Small delay like real app

    # Step 2: Call /v5/circles/devices/locations (gets device locations)
    print("\n2. Calling /v5/circles/devices/locations...")
    try:
        locations_url = f"{API_BASE}/v5/circles/devices/locations"
        resp = await session.get(
            locations_url,
            headers=get_headers("com.life360.cloud.platform.devices.locations.v1"),
            params={"circleId": circle_id}
        )
        print(f"   Response: {resp.status_code} ({len(resp.text)} bytes)")
        if resp.status_code == 200:
            print(f"   ✓ Additional cookies received")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    await asyncio.sleep(0.5)

    print("\n✓ Session established with cookies from previous requests")
    print(f"  Cookies in session: {len(session.cookies)} cookies")


async def test_v6_api(
    bearer_token: str,
    device_id: Optional[str] = None,
    circle_id: Optional[str] = None,
    activation_states: str = "activated,pending,pending_disassociated"
):
    """
    Test the v6/devices API with various authentication combinations.

    Args:
        bearer_token: Bearer token from Life360 auth
        device_id: Optional x-device-id header
        circle_id: Optional circle ID for session establishment
        activation_states: Activation states to filter by
    """
    print("\n" + "="*80)
    print("Testing v6/devices API")
    print("="*80)

    # Generate dynamic CloudEvents headers (REQUIRED by v6 API!)
    ce_id = str(uuid.uuid4())
    ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Build headers matching mobile app exactly
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en_AU",
        "User-Agent": "com.life360.android.safetymapd/KOKO/25.45.0 android/12",
        "Authorization": f"Bearer {bearer_token}",
        # CloudEvents headers - REQUIRED
        "ce-specversion": "1.0",
        "ce-type": "com.life360.device.devices.v1",
        "ce-id": ce_id,
        "ce-time": ce_time,
        "Accept-Encoding": "gzip",
    }

    if device_id:
        headers["x-device-id"] = device_id
        # Add ce-source when we have device_id (matches mobile app format)
        headers["ce-source"] = f"/ANDROID/12/samsung-SM-N920I/{device_id}"
        print(f"✓ Using x-device-id: {device_id}")
        print(f"✓ Using ce-source: {headers['ce-source']}")
    else:
        print("✗ No x-device-id header")
        print("✗ No ce-source header (requires device_id)")

    print(f"\nCloudEvents Headers:")
    print(f"  ce-id: {ce_id}")
    print(f"  ce-time: {ce_time}")

    # Build URL with query params
    params = {"activationStates": activation_states}

    print(f"\nRequest:")
    print(f"  URL: {V6_DEVICES_URL}")
    print(f"  Params: {params}")
    print(f"  TLS Fingerprint: Android Chrome (curl-impersonate)")
    print(f"  HTTP/2: Enabled")
    print(f"  Cookies: Enabled")
    print(f"\n  Headers:")
    for k, v in headers.items():
        if k == "Authorization":
            # v already contains "Bearer {token}", just truncate the token part
            print(f"    {k}: {v[:27]}...")  # "Bearer " + first 20 chars of token
        else:
            print(f"    {k}: {v}")

    try:
        # Use curl_cffi with Android Chrome impersonation (best TLS fingerprint match)
        async with AsyncSession(impersonate="chrome110") as session:
            # First establish session like mobile app does
            if circle_id:
                await establish_session(session, bearer_token, device_id, circle_id)

            print("\n" + "="*80)
            print("NOW CALLING v6/devices" + (" WITH ESTABLISHED SESSION" if circle_id else ""))
            print("="*80)

            response = await session.get(
                V6_DEVICES_URL,
                headers=headers,
                params=params
            )
            print(f"\n{'='*80}")
            print(f"Response Status: {response.status_code}")
            print(f"HTTP Version: HTTP/2")
            print(f"{'='*80}")

            # Print response headers
            print("\nResponse Headers:")
            for k, v in response.headers.items():
                print(f"  {k}: {v}")

            # Get response body
            response_text = response.text

            print(f"\nResponse Body ({len(response_text)} bytes):")
            print("-" * 80)

            # Try to parse as JSON
            try:
                response_data = json.loads(response_text)
                print(json.dumps(response_data, indent=2))

                # Analyze the structure
                if response.status_code == 200:
                    print("\n" + "="*80)
                    print("SUCCESS! Analyzing response structure...")
                    print("="*80)

                    if "data" in response_data:
                        data = response_data["data"]
                        if "items" in data:
                            items = data["items"]
                            print(f"\n✓ Found {len(items)} devices")

                            for i, item in enumerate(items):
                                print(f"\nDevice {i+1}:")
                                print(f"  id: {item.get('id')}")
                                print(f"  name: {item.get('name')}")
                                print(f"  provider: {item.get('provider')}")

                                type_data = item.get('typeData', {})
                                if type_data:
                                    print(f"  Tile BLE ID: {type_data.get('deviceId')}")
                                    print(f"  Auth Key: {type_data.get('authKey')}")
                        else:
                            print("\n✗ No 'items' key in data")
                    else:
                        print("\n✗ No 'data' key in response")

            except json.JSONDecodeError:
                print(response_text)
                print("\n✗ Response is not valid JSON")

            return response.status_code, response_data if response.status_code == 200 else None

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return None, None


async def main():
    """Run all test scenarios."""

    print("\n" + "="*80)
    print("Life360 v6/devices API Test Suite")
    print("="*80)

    if BEARER_TOKEN == "your_bearer_token_here":
        print("\n❌ ERROR: Please update BEARER_TOKEN in the script!")
        print("\nTo get your bearer token:")
        print("1. Check Home Assistant logs for 'Authorization: Bearer ...'")
        print("2. Or extract from captured network flows")
        print("3. Update the BEARER_TOKEN variable at the top of this script")
        return

    # Test scenarios
    scenarios = [
        {
            "name": "Test 1: With x-device-id header",
            "device_id": DEVICE_ID if DEVICE_ID else None,
        },
        {
            "name": "Test 2: Without x-device-id header",
            "device_id": None,
        },
    ]

    results = []

    for scenario in scenarios:
        if scenario["device_id"] is None and DEVICE_ID:
            continue  # Skip the without-header test if we have a device_id

        print(f"\n\n{'#'*80}")
        print(f"# {scenario['name']}")
        print(f"{'#'*80}")

        status, data = await test_v6_api(
            bearer_token=BEARER_TOKEN,
            device_id=scenario.get("device_id"),
            circle_id=CIRCLE_ID if CIRCLE_ID else None,
        )

        results.append({
            "scenario": scenario["name"],
            "status": status,
            "success": status == 200,
            "data": data,
        })

        # Wait a bit between tests
        await asyncio.sleep(2)

    # Summary
    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)

    for result in results:
        status_emoji = "✅" if result["success"] else "❌"
        print(f"{status_emoji} {result['scenario']}: HTTP {result['status']}")

    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
