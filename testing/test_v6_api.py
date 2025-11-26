#!/usr/bin/env python3
"""Test script for Life360 v6/devices API - rapid iteration testing."""

import asyncio
import aiohttp
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

# Configuration - UPDATE THESE
# Note: Life360 v6 API uses bearer token authentication only (no username/password needed)
BEARER_TOKEN = "your_bearer_token_here"  # Get from HA logs or network capture
DEVICE_ID = ""  # Optional: x-device-id header from network capture

# API endpoint
V6_DEVICES_URL = "https://api-cloudfront.life360.com/v6/devices"

async def test_v6_api(
    bearer_token: str,
    device_id: Optional[str] = None,
    activation_states: str = "activated,pending,pending_disassociated"
):
    """
    Test the v6/devices API with various authentication combinations.

    Args:
        bearer_token: Bearer token from Life360 auth
        device_id: Optional x-device-id header
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
    print(f"  Headers:")
    for k, v in headers.items():
        if k == "Authorization":
            # v already contains "Bearer {token}", just truncate the token part
            print(f"    {k}: {v[:27]}...")  # "Bearer " + first 20 chars of token
        else:
            print(f"    {k}: {v}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                V6_DEVICES_URL,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                print(f"\n{'='*80}")
                print(f"Response Status: {response.status} {response.reason}")
                print(f"{'='*80}")

                # Print response headers
                print("\nResponse Headers:")
                for k, v in response.headers.items():
                    print(f"  {k}: {v}")

                # Get response body
                response_text = await response.text()

                print(f"\nResponse Body ({len(response_text)} bytes):")
                print("-" * 80)

                # Try to parse as JSON
                try:
                    response_data = json.loads(response_text)
                    print(json.dumps(response_data, indent=2))

                    # Analyze the structure
                    if response.status == 200:
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

                return response.status, response_data if response.status == 200 else None

    except aiohttp.ClientError as e:
        print(f"\n✗ HTTP Error: {e}")
        return None, None
    except Exception as e:
        print(f"\n✗ Unexpected Error: {e}")
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
        print("2. Or extract from the flows.7z file you provided")
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
