#!/usr/bin/env python3
"""Helper script to fetch Life360 v6/devices data using curl_cffi.

This script is called by the Life360 integration coordinator to bypass
Cloudflare WAF blocking. It uses curl_cffi for TLS fingerprinting and
session establishment to mimic mobile app behavior.

Usage:
    fetch_v6_devices.py <bearer_token> <device_id> <circle_id>

Arguments:
    bearer_token: Life360 bearer token
    device_id: Device ID for x-device-id header (can be randomized)
    circle_id: Circle ID for session establishment

Output:
    On success: JSON to stdout with device data
    On error: Error message to stderr, exit code 1

Exit codes:
    0: Success
    1: Error (missing dependency, network error, API error, etc.)
"""

import sys
import json
import uuid
import asyncio
from datetime import datetime, timezone

# Check for curl_cffi dependency
try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    print("ERROR: curl_cffi not installed. Install with: pip3 install curl_cffi", file=sys.stderr)
    sys.exit(1)


API_BASE = "https://api-cloudfront.life360.com"


async def establish_session(session, bearer_token, device_id, circle_id):
    """Establish session by calling preliminary API endpoints."""

    def get_headers(ce_type):
        """Generate CloudEvents headers for API requests."""
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

    # Step 1: Call /v4/circles/{id}/members to get session cookies
    try:
        members_url = f"{API_BASE}/v4/circles/{circle_id}/members"
        await session.get(members_url, headers=get_headers("com.life360.circle.members.v1"))
    except Exception:
        pass  # Non-critical, continue anyway

    await asyncio.sleep(0.5)

    # Step 2: Call /v5/circles/devices/locations (optional, may 403 but still helps)
    try:
        locations_url = f"{API_BASE}/v5/circles/devices/locations"
        await session.get(
            locations_url,
            headers=get_headers("com.life360.cloud.platform.devices.locations.v1"),
            params={"circleId": circle_id}
        )
    except Exception:
        pass  # Non-critical, continue anyway

    await asyncio.sleep(0.5)


async def fetch_devices(bearer_token, device_id, circle_id):
    """Fetch v6/devices data using curl_cffi with TLS fingerprinting."""

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
        "x-device-id": device_id,
        "ce-source": f"/ANDROID/12/samsung-SM-N920I/{device_id}",
    }

    params = {"activationStates": "activated,pending,pending_disassociated"}

    try:
        # Use curl_cffi with Android Chrome impersonation
        async with AsyncSession(impersonate="chrome110") as session:
            # Establish session first (mimics mobile app)
            if circle_id:
                await establish_session(session, bearer_token, device_id, circle_id)

            # Now call v6/devices
            v6_url = f"{API_BASE}/v6/devices"
            response = await session.get(v6_url, headers=headers, params=params)

            if response.status_code == 200:
                data = json.loads(response.text)
                return data
            else:
                print(f"ERROR: API returned {response.status_code}: {response.text[:200]}", file=sys.stderr)
                return None

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def main():
    """Main entry point for script."""

    # Validate arguments
    if len(sys.argv) != 4:
        print("Usage: fetch_v6_devices.py <bearer_token> <device_id> <circle_id>", file=sys.stderr)
        sys.exit(1)

    bearer_token = sys.argv[1]
    device_id = sys.argv[2]
    circle_id = sys.argv[3]

    # Validate inputs
    if not bearer_token or bearer_token == "None":
        print("ERROR: Bearer token is required", file=sys.stderr)
        sys.exit(1)

    if not device_id or device_id == "None":
        print("ERROR: Device ID is required", file=sys.stderr)
        sys.exit(1)

    # Fetch data
    try:
        data = asyncio.run(fetch_devices(bearer_token, device_id, circle_id))

        if data:
            # Output JSON to stdout
            print(json.dumps(data))
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"ERROR: Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
