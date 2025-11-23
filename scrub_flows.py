#!/usr/bin/env python3
"""
Scrub sensitive data from mitmproxy flows and extract Life360 API endpoints.

Usage:
  1. Capture traffic with mitmproxy: mitmproxy -w flows.raw
  2. Export to text: mitmproxy -r flows.raw -w flows.txt
  3. Add your specific IDs to SCRUB_PATTERNS below
  4. Run: python scrub_flows.py
"""

import re
from pathlib import Path

# Patterns to scrub - ADD YOUR OWN VALUES HERE
SCRUB_PATTERNS = [
    # Bearer tokens (generic pattern catches most)
    (r'Bearer [A-Za-z0-9+/=_-]{20,}', 'Bearer <REDACTED_TOKEN>'),
    # Any base64-encoded UUIDs (pattern: base64 of UUID format)
    (r'[A-Za-z0-9+/]{40,}={0,2}', '<REDACTED_B64>'),
    # Device IDs
    (r'android[A-Za-z0-9]{20,}', 'android<REDACTED_DEVICE_ID>'),
    # UUIDs in API paths
    (r'(/v\d+/[^;]+?)/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', r'\1/<UUID>'),
    # Cloudflare cookies
    (r'__cf_bm=[^;]+', '__cf_bm=<REDACTED>'),
    (r'_cfuvid=[^;]+', '_cfuvid=<REDACTED>'),
    # IP addresses (internal)
    (r'192\.168\.\d+\.\d+', '<INTERNAL_IP>'),
    (r'10\.\d+\.\d+\.\d+', '<INTERNAL_IP>'),
    # Phone numbers (adjust pattern for your country)
    (r'\+\d{10,}', '<REDACTED_PHONE>'),
    # Email addresses
    (r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '<REDACTED_EMAIL>'),
    # Tile IDs (16-char hex)
    (r'"tile_id"[:\s]+"[a-f0-9]{16}"', '"tile_id":"<TILE_ID>"'),
    # Session IDs
    (r'"session_id":\d{13}', '"session_id":<SESSION_ID>'),

    # === ADD YOUR SPECIFIC VALUES BELOW ===
    # Uncomment and modify these examples:
    # (r'your-user-uuid-here', '<USER_ID>'),
    # (r'your-circle-uuid-here', '<CIRCLE_ID>'),
    # (r'your-specific-tile-id', '<TILE_ID>'),
    # (r'your-bearer-token', '<TOKEN>'),
]

# Patterns to extract endpoints
ENDPOINT_PATTERN = r'4:path;(\d+):([^,;]+)'
HOST_PATTERN = r'9:authority;(\d+):([^,;]+)'


def extract_endpoints(content: str) -> list:
    """Extract all API endpoints from the flows."""
    endpoints = set()

    path_matches = re.findall(ENDPOINT_PATTERN, content)

    for length, path in path_matches:
        if 'life360' in content[content.find(path)-100:content.find(path)+100].lower():
            path = path.strip()
            if path.startswith('/'):
                endpoints.add(path)

    return sorted(endpoints)


def scrub_content(content: str) -> str:
    """Scrub sensitive data from content."""
    scrubbed = content

    for pattern, replacement in SCRUB_PATTERNS:
        scrubbed = re.sub(pattern, replacement, scrubbed)

    return scrubbed


def extract_life360_requests(content: str) -> list:
    """Extract Life360 API requests with details."""
    requests = []

    request_pattern = r'7:request;(\d+):4:path;(\d+):([^,]+).*?6:method;(\d+):([^,]+).*?9:authority;(\d+):([^,]+)'

    for match in re.finditer(request_pattern, content, re.DOTALL):
        path = match.group(3)
        method = match.group(5)
        host = match.group(7)

        if 'life360' in host.lower():
            requests.append({
                'method': method,
                'host': host,
                'path': path
            })

    return requests


def main():
    flows_file = Path('flows.txt')

    if not flows_file.exists():
        print("flows.txt not found!")
        print("Export mitmproxy flows to flows.txt first.")
        return

    print("Reading flows.txt...")
    content = flows_file.read_text(errors='ignore')
    print(f"File size: {len(content)} bytes")

    # Extract endpoints
    print("\n=== DISCOVERED LIFE360 API ENDPOINTS ===")
    endpoints = extract_endpoints(content)

    unique_paths = set()
    for ep in endpoints:
        normalized = re.sub(
            r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
            '{id}',
            ep
        )
        unique_paths.add(normalized)

    for path in sorted(unique_paths):
        print(f"  {path}")

    # Extract detailed requests
    print("\n=== LIFE360 API REQUESTS ===")
    requests = extract_life360_requests(content)

    seen = set()
    for req in requests:
        key = f"{req['method']} {req['path']}"
        if key not in seen:
            seen.add(key)
            normalized_path = re.sub(
                r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
                '{id}',
                req['path']
            )
            print(f"  {req['method']:6} {normalized_path}")

    # Scrub and save
    print("\n=== SCRUBBING SENSITIVE DATA ===")
    scrubbed = scrub_content(content)

    # Save scrubbed version
    scrubbed_file = Path('flows_sanitized.txt')
    scrubbed_file.write_text(scrubbed)
    print(f"Saved sanitized flows to: {scrubbed_file}")

    # Save endpoints documentation
    endpoints_doc = """# Life360 API Endpoints (Discovered)

## Endpoints from Mobile App Traffic Capture

| Method | Endpoint | Description |
|--------|----------|-------------|
"""

    for req in sorted(set(
        f"{r['method']}|{re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '{id}', r['path'])}"
        for r in requests
    )):
        parts = req.split('|')
        if len(parts) == 2:
            method, path = parts
            endpoints_doc += f"| {method} | `{path}` | |\n"

    doc_file = Path('docs/api_endpoints.md')
    doc_file.parent.mkdir(exist_ok=True)
    doc_file.write_text(endpoints_doc)
    print(f"Saved endpoint documentation to: {doc_file}")


if __name__ == '__main__':
    main()
