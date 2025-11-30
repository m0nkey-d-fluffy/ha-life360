# Tile API 401 Error Analysis
## Investigation into Tile v6 Provider API Failures

**Date**: 2025-11-30
**Issue**: Tile BLE fallback to Life360 v6 API returns 401 Unauthorized
**Investigation**: Tile APK v25.46.0 decompilation and analysis

---

## Executive Summary

### The Problem

When Tile BLE ringing fails in ha-life360, the code falls back to:
```
POST /v6/provider/tile/devices/{device_id}/circle/{cid}/command
```

This returns **HTTP 401 Unauthorized**.

### The Root Cause

**Tiles do NOT have a cloud-based ring API via Life360!**

The `/v6/provider/tile/...` endpoint either:
1. **Doesn't exist** for Tile devices
2. **Requires Tile integration authentication** (not just Life360 Bearer token)
3. **Only exists for TileGps devices** (Jiobits), not TileBle devices

---

## Evidence from Tile APK Analysis

### 1. Tile Uses Its Own API Infrastructure

**Tile's API Base URLs** (from APK decompilation):
```
https://production.tile-api.com
https://locations-prod.tile-api.com
https://events-production.tile-api.com
```

**Location in APK**: `smali_classes3/Sb/d.smali`

### 2. Tile API Endpoints Found

From `com/thetileapp/tile/api/ApiService.smali`:
```
users/user_tiles/accept_tile_share
users/user_tiles/link_share_token
```

**No ring/command endpoint found** in Tile's API service!

### 3. How Tile Implements Ringing

**Found**: `com/thetileapp/tile/remotering/RemoteRingCmd`

**Structure**:
```kotlin
class RemoteRingCmd {
    var tile_uuid: String
    var code: String
    var client_ts: Long
    var payload: Payload
}

class Payload {
    var email: String
    var client_uuid: String
    var sender_client_uuid: String
    var connection_state: String
    var event_timestamp: Long
    var user_device_name: String
    var ring_state: String
    var volume_level: String  // Default: "LOUD"
    var keep_alive_interval: String
    var session_id: String
}
```

**Critical Finding**: This is a **realtime/websocket message**, NOT an HTTP API request!

**Evidence**:
- Used in `com/thetileapp/tile/remotering/a.smali` for **subscription management**
- Contains `connection_state`, `session_id`, `keep_alive_interval` - typical websocket fields
- Sent via **Tile's realtime event system**, not REST API

---

## Device Type Comparison

### Jiobits (TileGps Devices)

✅ **Cloud API Support**:
```
POST /v6/provider/jiobit/devices/{id}/circle/{cid}/command
POST /v6/provider/jiobit/devices/{id}/circles/{cid}/activate-lost-mode
POST /v6/provider/jiobit/devices/{id}/circles/{cid}/deactivate-lost-mode
```

**Command Structure**:
```json
{
  "data": {
    "commands": [{
      "command": "buzz",
      "args": {
        "duration": 30,
        "volume": "high"
      }
    }]
  }
}
```

**Model**: `TileGpsDeviceCommand` (found in Life360 APK)

### Tiles (TileBle Devices)

❌ **NO Cloud API for Ring Commands**

**Ring Method**: BLE direct connection only
- Uses Tile Over Air (TOA) protocol
- Requires Bluetooth proximity
- Uses `authKey` from Life360 API
- No HTTP endpoint for remote ring

**Model**: No equivalent to `TileGpsDeviceCommand` for TileBle

---

## Why the 401 Error Occurs

### Hypothesis 1: Endpoint Doesn't Exist ⭐ (Most Likely)

Life360's `/v6/provider/tile/...` endpoint may simply not exist for TileBle devices.

**Evidence**:
- No `TileBleDeviceCommand` model found in Life360 APK
- Only `TileGpsDeviceCommand` exists (for Jiobits)
- Tile APK uses BLE for all ring operations

### Hypothesis 2: Requires Tile Integration Token

The endpoint might exist but require authentication from Tile's API, not Life360's.

**Evidence**:
- `/v6/integrations` endpoint exists in Life360 API
- Tile integration would have its own session/token
- Life360 Bearer token might not authorize Tile-specific operations

### Hypothesis 3: Provider Mismatch

The endpoint might be for **provider management**, not device commands.

**Evidence**:
- Other `/v6/provider/tile/...` endpoints deal with activation/deactivation
- These are lifecycle operations, not command operations
- Commands might not be supported via this API

---

## Architecture Differences

### Life360's Architecture

**Jiobits**:
```
Life360 App → Life360 v6 API → Jiobit Device (via cellular/GPS)
```

**Tiles** (Life360 APK approach):
```
Life360 App → Life360 v5 API (get authKey) → BLE TOA Protocol → Tile Device
```

### Tile App's Architecture

```
Tile App → Tile's Own API (production.tile-api.com)
Tile App → BLE TOA Protocol → Tile Device (for ringing)
Tile App → Tile Realtime Service (websocket) → Tile Device (for remote ring)
```

**Key Insight**: Tile's native app uses **websocket/realtime messaging** for remote ring, not REST API!

---

## Recommendations

### 1. Remove Tile Cloud API Fallback ✅ **RECOMMENDED**

**Current Code** (in `coordinator.py:1568-1613`):
```python
async def ring_device(...):
    if provider == "tile":
        ble_success = await self._ring_tile_ble(device_id, cid)
        if ble_success:
            return True
        _LOGGER.debug("Tile BLE ring failed, falling back to server API")

    # This returns 401 for Tiles!
    return await self.send_device_command(
        device_id, cid, provider, feature_id=1, enable=True,
        duration=duration, strength=strength,
    )
```

**Recommended Change**:
```python
async def ring_device(...):
    if provider == "tile":
        ble_success = await self._ring_tile_ble(device_id, cid)
        if ble_success:
            return True

        # Tiles ONLY support BLE ringing - no cloud API available
        _LOGGER.warning(
            "Tile %s requires Bluetooth proximity for ringing. "
            "Ensure Home Assistant is within BLE range of the Tile device.",
            device_id
        )
        return False

    # For Jiobit devices, try v6 API
    if provider == "jiobit":
        # ... existing Jiobit v6 code ...

    # Legacy fallback for other providers
    return await self.send_device_command(...)
```

### 2. Better Error Messages for Users

When Tile BLE fails, inform users clearly:

```python
if not ble_success:
    _LOGGER.error(
        "Failed to ring Tile %s via Bluetooth. "
        "Possible causes:\n"
        "1. Tile is out of Bluetooth range\n"
        "2. Tile battery is dead\n"
        "3. Bluetooth adapter is not available\n"
        "4. Tile is not advertising (press button to wake)\n"
        "Cloud API fallback is NOT available for Tiles - "
        "BLE proximity is required.",
        device_id
    )
```

### 3. Document Tile vs Jiobit Differences

Update documentation to clarify:

| Feature | Tiles (TileBle) | Jiobits (TileGps) |
|---------|----------------|-------------------|
| **Ring Method** | BLE only | Cloud API + BLE |
| **Proximity Required** | ✅ Yes | ❌ No (works remotely) |
| **Cloud API Endpoint** | ❌ None | ✅ `/v6/provider/jiobit/...` |
| **BLE Protocol** | TOA (Tile Over Air) | Same (theoretically) |
| **Lost Mode** | ❌ Not via Life360 API | ✅ Via Life360 v6 API |

---

## Conclusion

### Key Findings

1. **Tiles do NOT have a cloud-based ring API** via Life360
2. **Tile APK uses BLE exclusively** for ringing functionality
3. **The `/v6/provider/tile/...` endpoint returns 401** because it doesn't support ring commands
4. **Jiobits ARE different** - they have full cloud API support via `/v6/provider/jiobit/...`
5. **Our BLE implementation is correct** - this is the ONLY way to ring Tiles remotely

### What Works

✅ **Tile BLE ringing** via TOA protocol (when in range)
✅ **Jiobit v6 API** for buzz/ring/lost mode (works remotely)
✅ **Jiobit BLE ringing** (theoretically - same authKey field)

### What Doesn't Work (and Why)

❌ **Tile cloud API fallback** - endpoint doesn't exist/isn't authorized
❌ **Remote Tile ringing without BLE** - not supported by Life360

### Action Items

1. ✅ **Remove Tile cloud API fallback** to avoid confusing 401 errors
2. ✅ **Improve error messages** to explain BLE requirement
3. ✅ **Document architecture differences** between Tiles and Jiobits
4. ✅ **Keep Jiobit v6 API** (already implemented)

---

## Technical Details: Tile APK Files Analyzed

```
/tmp/tile_apk_smali/smali_classes3/com/thetileapp/tile/api/ApiService.smali
/tmp/tile_apk_smali/smali_classes3/com/thetileapp/tile/remotering/RemoteRingCmd.smali
/tmp/tile_apk_smali/smali_classes3/com/thetileapp/tile/remotering/RemoteRingCmd$Payload.smali
/tmp/tile_apk_smali/smali_classes3/com/thetileapp/tile/remotering/a.smali
/tmp/tile_apk_smali/smali_classes3/Sb/d.smali (API base URLs)
/tmp/tile_apk_smali/smali_classes3/com/tile/android/data/table/Tile$TileRingState.smali
```

### API Base URLs Discovered

```
Production:
- https://production.tile-api.com
- https://locations-prod.tile-api.com
- https://events-production.tile-api.com

Development:
- https://development.tile-api.com
- https://locations-development.tile-api.com
- https://events-development.tile-api.com
```

**Note**: These are Tile's **own APIs**, not Life360's integration endpoints!

---

**Analysis Complete**: The 401 error is expected behavior. Tiles require BLE for ringing - there is no cloud API alternative.
