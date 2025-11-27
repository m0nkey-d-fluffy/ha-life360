# Life360 + Tile Device Mapping Guide

This guide explains how Life360 device IDs map to Tile BLE device IDs and how authentication works.

## Overview

Life360's v6 API provides crucial information that links Life360's cloud-based tracking with Tile's Bluetooth Low Energy (BLE) devices. This allows Home Assistant to:

1. **Display real device names** instead of generic "Tile 6fb80980"
2. **Ring Tile devices** via BLE using proper authentication
3. **Map between different ID systems** (Life360 ↔ Tile ↔ Home Assistant)

## Device ID Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│ Life360 v6 API Response                                     │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────────┐
        │  Life360 Device ID (UUID)           │
        │  "dr3d6e40d9-c400-4853-804b-..."    │
        │                                     │
        │  User-Facing Info:                  │
        │  - name: "Upstairs TV"              │
        │  - provider: "tile"                 │
        │  - category: "REMOTE"               │
        │  - avatar: "https://..."            │
        └─────────────────────────────────────┘
                          │
                          │ typeData
                          ▼
        ┌─────────────────────────────────────┐
        │  Tile BLE Device ID (Hex)           │
        │  "6fb809808f7f1309"                 │
        │                                     │
        │  BLE Info:                          │
        │  - authKey: "mC84othqSACFKn+..." ◄─── Base64 encoded
        │  - hardwareModel: "ATM24_STICKER1"  │
        │  - firmwareVersion: "60.04.11.0"    │
        └─────────────────────────────────────┘
                          │
                          │ Home Assistant
                          ▼
        ┌─────────────────────────────────────┐
        │  Home Assistant Entity              │
        │  device_tracker.life360_upstairs_tv │
        │                                     │
        │  Cached Data:                       │
        │  - _device_name_cache               │
        │  - _tile_auth_cache                 │
        │  - _tile_ble_id_cache               │
        └─────────────────────────────────────┘
```

## Example Device Mapping

From the v6 API response:

```json
{
  "id": "dr3d6e40d9-c400-4853-804b-449d1fd609ad",
  "name": "Upstairs TV",
  "provider": "tile",
  "category": "REMOTE",
  "typeData": {
    "deviceId": "6fb809808f7f1309",
    "authKey": "mC84othqSACFKn+ZhBOd3A==",
    "hardwareModel": "ATM24_STICKER1",
    "firmwareVersion": "60.04.11.0"
  }
}
```

**What the integration does:**

1. **Fetches data** via subprocess (curl_cffi → bypasses Cloudflare)
2. **Extracts mapping:**
   - Life360 ID: `dr3d6e40d9...` → Tile BLE ID: `6fb809808f7f1309`
   - Name: `"Upstairs TV"`
3. **Decodes auth key:**
   - Base64: `mC84othqSACFKn+ZhBOd3A==`
   - Decoded (hex): `982f38e2eb6a48008529bf9984139ddc`
   - Length: 16 bytes
4. **Caches everything:**
   ```python
   self._device_name_cache["dr3d6e40d9..."] = "Upstairs TV"
   self._tile_auth_cache["dr3d6e40d9..."] = b'\x98/8\xe2\xebj...'
   self._tile_auth_cache["6fb809808f7f1309"] = b'\x98/8\xe2\xebj...'  # Also by BLE ID
   self._tile_ble_id_cache["dr3d6e40d9..."] = "6fb809808f7f1309"
   ```

## Authentication Key Decoding

### Base64 → Bytes

The `authKey` in the v6 API response is base64-encoded:

```python
import base64

auth_key_b64 = "mC84othqSACFKn+ZhBOd3A=="
auth_key_bytes = base64.b64decode(auth_key_b64)

# Result: b'\x98/8\xe2\xebj\x00\x80R\x9f\xf9\x98\x10\x9d\xdc'
# Hex:    982f38e2eb6a48008529bf9984139ddc
# Length: 16 bytes
```

### Why This Matters

The decoded auth key is used for **BLE authentication** when ringing Tile devices:

```python
# Simplified example of BLE ring command
async def ring_tile(ble_id: str, auth_key: bytes):
    # 1. Connect to Tile via BLE
    # 2. Send authentication challenge with auth_key
    # 3. Send "ring" command
    # 4. Tile plays sound
```

## Bidirectional Caching

The integration caches data **both ways** for fast lookup:

### By Life360 Device ID

```python
# Lookup by Life360 ID (from location updates)
life360_id = "dr3d6e40d9-c400-4853-804b-449d1fd609ad"

name = self._device_name_cache[life360_id]  # "Upstairs TV"
auth_key = self._tile_auth_cache[life360_id]  # b'\x98/8\xe2...'
ble_id = self._tile_ble_id_cache[life360_id]  # "6fb809808f7f1309"
```

### By Tile BLE Device ID

```python
# Lookup by Tile BLE ID (from BLE scans)
ble_id = "6fb809808f7f1309"

auth_key = self._tile_auth_cache[ble_id]  # b'\x98/8\xe2...'
# Note: We cache auth_key by BOTH IDs for flexibility
```

## How to View Your Device Mappings

### Option 1: Use the helper script

```bash
cd testing

# Run test and pipe to decoder
./test_v6_configured.py | python3 decode_v6_mappings.py

# Or decode saved JSON
python3 decode_v6_mappings.py v6_response.json
```

### Option 2: Check Home Assistant logs

When the integration starts, it logs device mappings:

```
[custom_components.life360.coordinator] ✓ Cached metadata for 5 devices: {
  'dr3d6e40d9...': 'Upstairs TV',
  'dr7acfe58f...': 'Wallet',
  'dr9f3289f2...': 'Keys',
  ...
}
```

### Option 3: Manual decoding

```python
import base64

# From v6 API typeData.authKey
auth_key_b64 = "mC84othqSACFKn+ZhBOd3A=="

# Decode to bytes
auth_key = base64.b64decode(auth_key_b64)

# View as hex
print(auth_key.hex())  # 982f38e2eb6a48008529bf9984139ddc
```

## Complete Data Flow

```
1. User installs integration
   └─> Integration starts

2. Coordinator generates random device ID
   └─> Example: "androidK3mP9xQw2Vn4Ry8Lz7Jc5T"

3. Subprocess calls v6 API with curl_cffi
   └─> TLS fingerprinting bypasses Cloudflare
   └─> Session establishment (preliminary API calls)
   └─> GET /v6/devices?activationStates=activated,...

4. Parse JSON response
   └─> Extract Life360 device IDs
   └─> Extract Tile BLE device IDs
   └─> Decode base64 auth keys
   └─> Get device names, categories, avatars

5. Cache everything bidirectionally
   └─> _device_name_cache: ID → Name
   └─> _tile_auth_cache: ID → Auth Key (both Life360 & BLE IDs)
   └─> _tile_ble_id_cache: Life360 ID → BLE ID
   └─> _device_avatar_cache: ID → Avatar URL
   └─> _device_category_cache: ID → Category

6. Create entities
   └─> device_tracker.life360_upstairs_tv
   └─> Uses cached name: "Upstairs TV"
   └─> Has ring service with cached auth

7. User clicks "Ring"
   └─> Service looks up auth_key from cache
   └─> Calls Tile BLE API with auth_key
   └─> Tile device rings
```

## Troubleshooting

### Devices show generic names

**Check:**
1. Is curl_cffi installed? `pip3 show curl_cffi`
2. Check HA logs for: `✓ Successfully fetched v6/devices via subprocess`
3. Check HA logs for: `✓ Cached metadata for X devices`

**If you see:**
- "curl_cffi not installed" → Run `pip3 install curl_cffi`
- "Cloudflare blocking detected" → subprocess failed, check network

### Ring doesn't work

**Check:**
1. Auth key was decoded: Check logs for "✓ Cached Tile BLE auth data"
2. BLE connection works: Device must be in BLE range
3. Auth key is valid: Keys may expire (re-fetch v6 data)

## Summary

**The integration handles everything automatically:**

✅ Fetches v6 API data (bypassing Cloudflare)
✅ Maps Life360 IDs ↔ Tile BLE IDs
✅ Decodes base64 auth keys
✅ Caches bidirectionally for fast lookup
✅ Uses correct names for entities
✅ Authenticates BLE ring commands

**Users don't need to manually decode or map anything!**

This guide is for understanding the internals and debugging.
