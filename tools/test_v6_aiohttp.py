#!/usr/bin/env python3
"""Test v6 API with aiohttp (Home Assistant's HTTP library).

This tests whether session establishment alone is sufficient, or if
curl_cffi's TLS fingerprinting is required to bypass Cloudflare.
"""

import asyncio
import aiohttp
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

# Configuration - UPDATE THESE
BEARER_TOKEN = "your_bearer_token_here"
DEVICE_ID = ""  # Can be randomized!
CIRCLE_ID = ""

# API endpoints
API_BASE = "https://api-cloudfront.life360.com"
V6_DEVICES_URL = f"{API_BASE}/v6/devices"


async def establish_session_aiohttp(session, bearer_token, device_id, circle_id):
    """Establish session using aiohttp (same logic as curl_cffi version)."""
    if not circle_id:
        print("⚠️  No CIRCLE_ID provided - skipping session establishment")
        return

    print("\n" + "="*80)
    print("ESTABLISHING SESSION with aiohttp")
    print("="*80)

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

    # Step 1: /v4/circles/{id}/members
    print("\n1. Calling /v4/circles/{id}/members...")
    try:
        members_url = f"{API_BASE}/v4/circles/{circle_id}/members"
        async with session.get(members_url, headers=get_headers("com.life360.circle.members.v1")) as resp:
            text = await resp.text()
            print(f"   Response: {resp.status} ({len(text)} bytes)")
            if resp.status == 200:
                print(f"   ✓ Session cookie received")
                print(f"   Cookies: {session.cookie_jar}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    await asyncio.sleep(0.5)

    # Step 2: /v5/circles/devices/locations
    print("\n2. Calling /v5/circles/devices/locations...")
    try:
        locations_url = f"{API_BASE}/v5/circles/devices/locations"
        async with session.get(
            locations_url,
            headers=get_headers("com.life360.cloud.platform.devices.locations.v1"),
            params={"circleId": circle_id}
        ) as resp:
            text = await resp.text()
            print(f"   Response: {resp.status} ({len(text)} bytes)")
            if resp.status == 200:
                print(f"   ✓ Additional cookies received")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    await asyncio.sleep(0.5)
    print("\n✓ Session established")
    print(f"  Total cookies: {len(session.cookie_jar)}")


async def test_with_aiohttp(bearer_token, device_id, circle_id, with_session_establishment=True):
    """Test v6 API with aiohttp."""

    print("\n" + "="*80)
    print(f"TEST: aiohttp {'WITH' if with_session_establishment else 'WITHOUT'} session establishment")
    print("="*80)

    # Generate CloudEvents headers
    ce_id = str(uuid.uuid4())
    ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    headers = {
        "Accept": "application/json",
        "Accept-Language": "en_AU",
        "User-Agent": "com.life360.android.safetymapd/KOKO/25.45.0 android/12",
        "Authorization": f"Bearer {bearer_token}",
        "ce-specversion": "1.0",
        "ce-type": "com.life360.device.devices.v1",
        "ce-id": ce_id,
        "ce-time": ce_time,
        "Accept-Encoding": "gzip",
    }

    if device_id:
        headers["x-device-id"] = device_id
        headers["ce-source"] = f"/ANDROID/12/samsung-SM-N920I/{device_id}"

    params = {"activationStates": "activated,pending,pending_disassociated"}

    print(f"\nRequest: GET {V6_DEVICES_URL}")
    print(f"x-device-id: {device_id if device_id else '(not sent)'}")
    print(f"TLS: Python default (no fingerprinting)")
    print(f"HTTP: aiohttp default")

    try:
        # Create session with cookie jar
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
            # Establish session first if requested
            if with_session_establishment and circle_id:
                await establish_session_aiohttp(session, bearer_token, device_id, circle_id)

            print("\n" + "="*80)
            print(f"CALLING v6/devices" + (" WITH ESTABLISHED SESSION" if with_session_establishment else " WITHOUT SESSION"))
            print("="*80)

            # Now call v6/devices
            async with session.get(V6_DEVICES_URL, headers=headers, params=params) as response:
                print(f"\n{'='*80}")
                print(f"Response Status: {response.status}")
                print(f"{'='*80}")

                text = await response.text()

                if response.status == 200:
                    print("\n✅ SUCCESS with aiohttp!")
                    data = json.loads(text)
                    items = data.get("data", {}).get("items", [])
                    print(f"\nFound {len(items)} devices:")
                    for item in items:
                        print(f"  - {item.get('name')} ({item.get('provider')})")
                    return True
                elif response.status == 403:
                    print("\n❌ 403 Forbidden - Cloudflare blocked aiohttp")
                    print(f"\nResponse: {text[:500]}")
                    return False
                else:
                    print(f"\n❌ Unexpected status: {response.status}")
                    print(f"\nResponse: {text[:500]}")
                    return False

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run comparison tests."""

    print("\n" + "#"*80)
    print("# AIOHTTP vs CURL_CFFI COMPARISON TEST")
    print("#"*80)
    print("\nThis test determines if Home Assistant can use v6 API directly,")
    print("or if we need a curl_cffi wrapper/proxy service.")

    if BEARER_TOKEN == "your_bearer_token_here":
        print("\n❌ ERROR: Please update BEARER_TOKEN in the script!")
        return

    print("\n" + "="*80)
    print("CONFIGURATION")
    print("="*80)
    print(f"Bearer token: {BEARER_TOKEN[:30]}...")
    print(f"Device ID: {DEVICE_ID if DEVICE_ID else '(none - will fail)'}")
    print(f"Circle ID: {CIRCLE_ID if CIRCLE_ID else '(none - no session establishment)'}")

    # Test 1: aiohttp WITHOUT session establishment
    print("\n\n" + "#"*80)
    print("# TEST 1: aiohttp WITHOUT session establishment")
    print("#"*80)
    result1 = await test_with_aiohttp(BEARER_TOKEN, DEVICE_ID, CIRCLE_ID, with_session_establishment=False)
    await asyncio.sleep(2)

    # Test 2: aiohttp WITH session establishment
    print("\n\n" + "#"*80)
    print("# TEST 2: aiohttp WITH session establishment")
    print("#"*80)
    result2 = await test_with_aiohttp(BEARER_TOKEN, DEVICE_ID, CIRCLE_ID, with_session_establishment=True)

    # Summary
    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"{'✅' if result1 else '❌'} Test 1: aiohttp WITHOUT session establishment")
    print(f"{'✅' if result2 else '❌'} Test 2: aiohttp WITH session establishment")

    print("\n" + "="*80)
    print("CONCLUSION")
    print("="*80)

    if result2:
        print("✅ Home Assistant integration can work with aiohttp!")
        print("   Session establishment is sufficient to bypass Cloudflare.")
        print("   We can integrate this directly into coordinator.py")
    elif result1:
        print("✅ Session establishment not needed - aiohttp works as-is!")
        print("   We can use v6 API directly in Home Assistant.")
    else:
        print("❌ aiohttp cannot bypass Cloudflare WAF")
        print("   curl_cffi's TLS fingerprinting is required.")
        print("   Options:")
        print("   1. Create a proxy service using curl_cffi")
        print("   2. Use test scripts to generate device mappings")
        print("   3. Wait for Cloudflare to relax restrictions")


if __name__ == "__main__":
    asyncio.run(main())
