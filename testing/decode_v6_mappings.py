#!/usr/bin/env python3
"""Helper script to decode and display v6 API device mappings.

This script helps users understand:
1. How to decode base64 auth keys for Tile BLE devices
2. How Life360 device IDs map to Tile BLE device IDs
3. The complete device hierarchy for debugging

Usage:
    python3 decode_v6_mappings.py <path_to_v6_response.json>

Or pipe JSON directly:
    ./test_v6_configured.py | python3 decode_v6_mappings.py
"""

import sys
import json
import base64


def decode_auth_key(auth_key_b64):
    """Decode base64 auth key to hex string."""
    try:
        auth_key_bytes = base64.b64decode(auth_key_b64)
        return auth_key_bytes.hex()
    except Exception as e:
        return f"ERROR: {e}"


def analyze_v6_response(data):
    """Analyze and display v6/devices response structure."""

    print("\n" + "="*80)
    print("LIFE360 v6/devices API RESPONSE ANALYSIS")
    print("="*80)

    items = data.get("data", {}).get("items", [])

    if not items:
        print("\n❌ No devices found in response")
        return

    print(f"\n✓ Found {len(items)} device(s)\n")

    for i, item in enumerate(items, 1):
        print("─" * 80)
        print(f"DEVICE #{i}")
        print("─" * 80)

        # Basic info
        life360_id = item.get("id", "UNKNOWN")
        name = item.get("name", "UNKNOWN")
        provider = item.get("provider", "UNKNOWN")
        device_type = item.get("type", "UNKNOWN")
        category = item.get("category", "UNKNOWN")

        print(f"\n📱 Basic Info:")
        print(f"   Name:          {name}")
        print(f"   Provider:      {provider}")
        print(f"   Type:          {device_type}")
        print(f"   Category:      {category}")
        print(f"   Life360 ID:    {life360_id}")

        # Type-specific data (Tile/Jiobit BLE info)
        type_data = item.get("typeData", {})
        if type_data:
            print(f"\n🔷 BLE Device Info:")

            tile_ble_id = type_data.get("deviceId", "UNKNOWN")
            hardware_model = type_data.get("hardwareModel", "UNKNOWN")
            product_code = type_data.get("productCode", "UNKNOWN")
            firmware = type_data.get("firmwareVersion", "UNKNOWN")
            auth_key_b64 = type_data.get("authKey", "")

            print(f"   Tile BLE ID:   {tile_ble_id}")
            print(f"   Hardware:      {hardware_model}")
            print(f"   Product Code:  {product_code}")
            print(f"   Firmware:      {firmware}")

            if auth_key_b64:
                print(f"\n🔑 Authentication Key:")
                print(f"   Base64:        {auth_key_b64}")
                auth_key_hex = decode_auth_key(auth_key_b64)
                print(f"   Hex:           {auth_key_hex}")
                print(f"   Length:        {len(base64.b64decode(auth_key_b64)) if auth_key_hex != 'ERROR' else 'N/A'} bytes")

            # ICCID for cellular devices (Jiobit)
            iccid = type_data.get("iccid")
            if iccid:
                print(f"\n📡 Cellular Info:")
                print(f"   ICCID:         {iccid}")

        # Device mapping summary
        print(f"\n🔗 Device ID Mapping:")
        print(f"   Life360 ID  →  Tile BLE ID")
        print(f"   {life360_id}  →  {type_data.get('deviceId', 'N/A')}")

        # Avatar/Icon
        avatar = item.get("avatar")
        if avatar:
            print(f"\n🖼️  Avatar URL:")
            print(f"   {avatar}")

        print()

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\nTotal Devices: {len(items)}")
    print(f"\nProviders:")
    providers = {}
    for item in items:
        provider = item.get("provider", "unknown")
        providers[provider] = providers.get(provider, 0) + 1
    for provider, count in providers.items():
        print(f"  - {provider}: {count}")

    print("\n" + "=" * 80)
    print("HOW TO USE THIS DATA IN HOME ASSISTANT")
    print("=" * 80)
    print("""
The Life360 integration automatically:

1. ✓ Fetches this v6 API data via subprocess (bypasses Cloudflare)
2. ✓ Decodes base64 auth keys for BLE authentication
3. ✓ Maps Life360 device IDs → Tile BLE device IDs
4. ✓ Caches device names, avatars, categories
5. ✓ Uses auth keys for BLE ringing functionality

Device Name Mapping:
  - Life360 returns: "dr3d6e40d9..." (Life360 ID)
  - Maps to Tile BLE: "6fb809808f7f1309" (Tile device ID)
  - Shows user-friendly name: "Upstairs TV"

BLE Authentication:
  - Auth key is base64-decoded to bytes
  - Used with Tile BLE protocol for "ring" command
  - Cached in coordinator for fast lookup

You don't need to manually decode anything - the integration handles it all!
This script is just for debugging and understanding the data structure.
""")


def main():
    """Main entry point."""

    if len(sys.argv) > 1:
        # Read from file
        filepath = sys.argv[1]
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            print(f"ERROR: File not found: {filepath}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Read from stdin
        try:
            input_data = sys.stdin.read()
            data = json.loads(input_data)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON from stdin: {e}", file=sys.stderr)
            print("\nUsage: python3 decode_v6_mappings.py <path_to_v6_response.json>", file=sys.stderr)
            print("   Or: ./test_v6_configured.py | python3 decode_v6_mappings.py", file=sys.stderr)
            sys.exit(1)

    analyze_v6_response(data)


if __name__ == "__main__":
    main()
