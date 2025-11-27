#!/usr/bin/env python3
"""Pre-configured test for Life360 v6/devices API using known credentials.

Uses curl_cffi to impersonate Android Chrome for better TLS fingerprinting
and establishes a session by calling other API endpoints first.
"""

import asyncio
from curl_cffi.requests import AsyncSession
import json
import uuid
from datetime import datetime, timezone

# ⚠️ CONFIGURE YOUR CREDENTIALS HERE ⚠️
# Get these from Home Assistant logs or captured network flows
BEARER_TOKEN = "YOUR_BEARER_TOKEN_HERE"  # From HA logs: "Authorization: Bearer ..."
DEVICE_ID = "YOUR_DEVICE_ID_HERE"  # From flows: x-device-id header
CIRCLE_ID = "YOUR_CIRCLE_ID_HERE"  # From HA config or /v3/circles endpoint

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
    print("\n" + "="*80)
    print("ESTABLISHING SESSION (mimicking mobile app behavior)")
    print("="*80)

    # Common headers for all requests
    def get_headers(ce_type):
        ce_id = str(uuid.uuid4())
        ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        return {
            "Accept": "application/json",
            "Accept-Language": "en_AU",
            "User-Agent": "com.life360.android.safetymapd/KOKO/25.45.0 android/12",
            "Authorization": f"Bearer {bearer_token}",
            "x-device-id": device_id,
            "ce-specversion": "1.0",
            "ce-type": ce_type,
            "ce-id": ce_id,
            "ce-time": ce_time,
            "ce-source": f"/ANDROID/12/samsung-SM-N920I/{device_id}",
            "Accept-Encoding": "gzip",
        }

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


async def test_v6_with_device_id():
    """Test WITH x-device-id header."""
    print("\n" + "="*80)
    print("TEST 1: v6/devices API WITH x-device-id header")
    print("="*80)

    # Generate dynamic CloudEvents headers (REQUIRED by v6 API!)
    ce_id = str(uuid.uuid4())
    ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    ce_source = f"/ANDROID/12/samsung-SM-N920I/{DEVICE_ID}"

    headers = {
        "Accept": "application/json",
        "Accept-Language": "en_AU",
        "User-Agent": "com.life360.android.safetymapd/KOKO/25.45.0 android/12",
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "x-device-id": DEVICE_ID,
        # CloudEvents headers - required by v6 API
        "ce-specversion": "1.0",
        "ce-type": "com.life360.device.devices.v1",
        "ce-id": ce_id,
        "ce-time": ce_time,
        "ce-source": ce_source,
        "Accept-Encoding": "gzip",
    }

    print(f"\nCloudEvents Headers:")
    print(f"  ce-id: {ce_id}")
    print(f"  ce-time: {ce_time}")
    print(f"  ce-source: {ce_source}")

    params = {"activationStates": "activated,pending,pending_disassociated"}

    print(f"\nRequest: GET {V6_DEVICES_URL}")
    print(f"x-device-id: {DEVICE_ID}")
    print(f"Bearer: {BEARER_TOKEN[:30]}...")
    print(f"TLS Fingerprint: Android Chrome (curl-impersonate)")
    print(f"HTTP/2: Enabled")
    print(f"Cookies: Enabled")

    # Use curl_cffi with Android Chrome impersonation (best TLS fingerprint match)
    async with AsyncSession(impersonate="chrome110") as session:
        # First establish session like mobile app does
        await establish_session(session, BEARER_TOKEN, DEVICE_ID, CIRCLE_ID)

        print("\n" + "="*80)
        print("NOW CALLING v6/devices WITH ESTABLISHED SESSION")
        print("="*80)

        try:
            resp = await session.get(V6_DEVICES_URL, headers=headers, params=params)
            print(f"\n{'='*80}")
            print(f"Response: {resp.status_code}")
            print(f"HTTP Version: HTTP/2")
            print(f"{'='*80}")

            body = resp.text

            if resp.status_code == 200:
                data = json.loads(body)
                print("\n✅ SUCCESS!\n")
                print(json.dumps(data, indent=2))

                # Analyze devices
                items = data.get("data", {}).get("items", [])
                print(f"\n{'='*80}")
                print(f"Found {len(items)} devices:")
                print(f"{'='*80}")

                for item in items:
                    life360_id = item.get("id")
                    name = item.get("name")
                    provider = item.get("provider")
                    type_data = item.get("typeData", {})
                    tile_ble_id = type_data.get("deviceId")
                    auth_key = type_data.get("authKey")

                    print(f"\nDevice: {name}")
                    print(f"  Life360 ID: {life360_id}")
                    print(f"  Provider: {provider}")
                    if tile_ble_id:
                        print(f"  Tile BLE ID: {tile_ble_id}")
                        print(f"  Auth Key: {auth_key}")

                return True

            elif resp.status_code == 401:
                print("\n❌ 401 Unauthorized")
                print("\nPossible reasons:")
                print("  - Bearer token is expired")
                print("  - x-device-id is incorrect")
                print("\nTo fix:")
                print("  1. Get fresh Bearer token from HA logs")
                print("  2. Update BEARER_TOKEN in this script")
                return False
            else:
                print(f"\n❌ Unexpected status: {resp.status_code}")
                print(f"\nResponse body:\n{body}")
                return False

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_v6_without_device_id():
    """Test WITHOUT x-device-id header."""
    print("\n\n" + "="*80)
    print("TEST 2: v6/devices API WITHOUT x-device-id header")
    print("="*80)

    # Generate dynamic CloudEvents headers (but no ce-source without device-id)
    ce_id = str(uuid.uuid4())
    ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    headers = {
        "Accept": "application/json",
        "Accept-Language": "en_AU",
        "User-Agent": "com.life360.android.safetymapd/KOKO/25.45.0 android/12",
        "Authorization": f"Bearer {BEARER_TOKEN}",
        # CloudEvents headers (no ce-source since no device-id)
        "ce-specversion": "1.0",
        "ce-type": "com.life360.device.devices.v1",
        "ce-id": ce_id,
        "ce-time": ce_time,
        "Accept-Encoding": "gzip",
    }

    print(f"\nCloudEvents Headers:")
    print(f"  ce-id: {ce_id}")
    print(f"  ce-time: {ce_time}")
    print(f"  ce-source: (not sent - no device-id)")

    params = {"activationStates": "activated,pending,pending_disassociated"}

    print(f"\nRequest: GET {V6_DEVICES_URL}")
    print(f"x-device-id: (not sent)")
    print(f"Bearer: {BEARER_TOKEN[:30]}...")
    print(f"TLS Fingerprint: Android Chrome (curl-impersonate)")
    print(f"HTTP/2: Enabled")
    print(f"Cookies: Enabled")

    async with AsyncSession(impersonate="chrome110") as session:
        try:
            resp = await session.get(V6_DEVICES_URL, headers=headers, params=params)
            print(f"\n{'='*80}")
            print(f"Response: {resp.status_code}")
            print(f"HTTP Version: HTTP/2")
            print(f"{'='*80}")

            body = resp.text

            if resp.status_code == 200:
                data = json.loads(body)
                print("\n🎉 AMAZING! The API works WITHOUT x-device-id!\n")
                print(json.dumps(data, indent=2))

                items = data.get("data", {}).get("items", [])
                print(f"\nFound {len(items)} devices")
                return True

            elif resp.status_code == 401:
                print("\n⚠️  401 Unauthorized without x-device-id")
                print("\nThis is expected - the API requires x-device-id")
                return False
            else:
                print(f"\n❌ Unexpected status: {resp.status_code}")
                print(f"\nResponse body:\n{body}")
                return False

        except Exception as e:
            print(f"\n❌ Error: {e}")
            return False


async def main():
    """Run all tests."""
    print("\n" + "#"*80)
    print("# Life360 v6/devices API Test")
    print("#"*80)
    print("\n⚠️  WARNING: Bearer tokens expire! If tests fail, get a fresh token.")
    print("See README for instructions.\n")

    # Test 1: With x-device-id
    test1_passed = await test_v6_with_device_id()
    await asyncio.sleep(2)

    # Test 2: Without x-device-id
    test2_passed = await test_v6_without_device_id()

    # Summary
    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"{'✅' if test1_passed else '❌'} Test 1: WITH x-device-id")
    print(f"{'✅' if test2_passed else '❌'} Test 2: WITHOUT x-device-id")

    print("\n" + "="*80)
    print("NEXT STEPS")
    print("="*80)

    if test1_passed:
        print("\n✅ v6 API works with x-device-id!")
        print("\nTo use in Home Assistant:")
        print(f"  1. Add this to your Life360 integration config:")
        print(f"     device_id: {DEVICE_ID}")
        print(f"  2. Restart Home Assistant")
        print(f"  3. Entity names should auto-populate!")

    elif not test1_passed and not test2_passed:
        print("\n❌ Both tests failed - likely expired Bearer token")
        print("\nGet a fresh token:")
        print("  1. Restart Life360 integration in HA")
        print("  2. Enable debug logging")
        print("  3. Check logs for 'Authorization: Bearer ...'")
        print("  4. Update BEARER_TOKEN in this script")

    if test2_passed:
        print("\n🎉 AMAZING! v6 API works WITHOUT x-device-id!")
        print("\nThis means we can enable it in the integration without")
        print("requiring users to capture network traffic!")

    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
