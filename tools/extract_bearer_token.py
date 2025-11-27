#!/usr/bin/env python3
"""Extract bearer token from captured flows or Home Assistant logs."""

import gzip
import json
import re
import sys

def extract_from_flows(flows_path: str):
    """Extract bearer token and device ID from mitmproxy flows."""
    print(f"Reading flows from: {flows_path}")

    with open(flows_path, 'rb') as f:
        data = f.read()

    text = data.decode('latin-1')

    # Find authorization headers
    auth_pattern = r'Authorization(?:;13:authorization,)?(?::|\s*,\s*)(?:55:)?Bearer ([A-Za-z0-9+/=]+)'
    auth_matches = re.findall(auth_pattern, text)

    # Find device IDs
    device_id_pattern = r'x-device-id(?:;11:x-device-id,)?(?::|\s*,\s*)(?:\d+:)?([a-zA-Z0-9]+)'
    device_id_matches = re.findall(device_id_pattern, text)

    print("\n" + "="*80)
    print("FOUND CREDENTIALS")
    print("="*80)

    if auth_matches:
        # Decode the bearer token (it's base64 encoded in the flows)
        bearer_tokens = list(set(auth_matches))
        print(f"\nBearer Tokens ({len(bearer_tokens)} unique):")
        for i, token in enumerate(bearer_tokens[:5], 1):  # Show first 5
            print(f"  {i}. {token}")

        print("\n✓ Use this in test_v6_api.py:")
        print(f'BEARER_TOKEN = "{bearer_tokens[0]}"')
    else:
        print("\n✗ No bearer tokens found")

    if device_id_matches:
        device_ids = list(set(device_id_matches))
        print(f"\nx-device-id values ({len(device_ids)} unique):")
        for i, did in enumerate(device_ids, 1):
            print(f"  {i}. {did}")

        print("\n✓ Use this in test_v6_api.py:")
        print(f'DEVICE_ID = "{device_ids[0]}"')
    else:
        print("\n✗ No x-device-id values found")


def extract_from_ha_logs(log_path: str):
    """Extract bearer token from Home Assistant logs."""
    print(f"Reading HA logs from: {log_path}")

    with open(log_path, 'r') as f:
        log_data = f.read()

    # Find authorization headers in debug logs
    auth_pattern = r'Authorization["\']?\s*:\s*["\']?Bearer\s+([A-Za-z0-9+/=]+)'
    auth_matches = re.findall(auth_pattern, log_data)

    # Find device IDs
    device_id_pattern = r'x-device-id["\']?\s*:\s*["\']?([a-zA-Z0-9]+)'
    device_id_matches = re.findall(device_id_pattern, log_data)

    print("\n" + "="*80)
    print("FOUND CREDENTIALS")
    print("="*80)

    if auth_matches:
        bearer_tokens = list(set(auth_matches))
        print(f"\nBearer Tokens ({len(bearer_tokens)} unique):")
        for i, token in enumerate(bearer_tokens[:5], 1):
            print(f"  {i}. {token}")

        print("\n✓ Use this in test_v6_api.py:")
        print(f'BEARER_TOKEN = "{bearer_tokens[0]}"')
    else:
        print("\n✗ No bearer tokens found")

    if device_id_matches:
        device_ids = list(set(device_id_matches))
        print(f"\nx-device-id values ({len(device_ids)} unique):")
        for i, did in enumerate(device_ids, 1):
            print(f"  {i}. {did}")

        print("\n✓ Use this in test_v6_api.py:")
        print(f'DEVICE_ID = "{device_ids[0]}"')
    else:
        print("\n✗ No x-device-id values found")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 extract_bearer_token.py /tmp/flows/flows.txt")
        print("  python3 extract_bearer_token.py /path/to/home-assistant.log")
        sys.exit(1)

    file_path = sys.argv[1]

    # Detect file type
    if 'flow' in file_path.lower():
        extract_from_flows(file_path)
    else:
        extract_from_ha_logs(file_path)
