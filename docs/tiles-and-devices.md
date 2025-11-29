# Tile & Device Tracker Support

This document explains how the Life360 integration supports Tile Bluetooth trackers and Jiobit pet GPS trackers.

> **💡 Want to add ring buttons to your dashboard?** See the **[Dashboard Setup Guide](dashboard-setup.md)** for easy copy-paste configurations!

## Overview

Life360 allows users to link third-party tracking devices to their account. This integration automatically discovers and creates Home Assistant entities for:

- **Tile Bluetooth Trackers** - Small Bluetooth devices for tracking keys, wallets, bags, etc.
- **Jiobit GPS Trackers** - Cellular GPS devices designed for pets and children

## Supported Devices

### Tile Trackers

All Tile models are supported:
- Tile Mate
- Tile Pro
- Tile Slim
- Tile Sticker
- Tile Ultra (UWB)

**How Tile works with Life360:**
1. Tile uses Bluetooth Low Energy (BLE) to communicate with nearby phones
2. When any Life360 user's phone comes near your Tile, the location is reported
3. This creates a crowdsourced network for finding lost items
4. Location updates depend on how recently a phone has been near the Tile

**Direct BLE Ringing (NEW in 2025):**
- This integration can ring Tiles **directly via Bluetooth** when in range
- No cloud API delays - rings in <1 second
- Uses the complete Tile Over Air (TOA) protocol
- Requires your Home Assistant host to have Bluetooth hardware
- Automatically falls back to cloud API if Tile is out of BLE range
- See [Tile BLE Technical Documentation](tile-ble-technical.md) for protocol details

### Jiobit Pet/Child GPS

Jiobit devices are cellular GPS trackers that provide:
- Real-time GPS location
- Cellular connectivity (no phone needed nearby)
- Designed for pets, children, and elderly care

**How Jiobit works with Life360:**
1. Jiobit connects directly to cellular networks
2. GPS location is sent to Life360 servers
3. More accurate and frequent updates than Tile
4. Requires active Jiobit subscription

## Linking Devices to Life360

### Linking Tile Devices

1. Open the **Life360 mobile app**
2. Tap on your **profile** or go to **Settings**
3. Look for **Connected Apps** or **Integrations**
4. Select **Tile**
5. Log in with your Tile account credentials
6. Authorize Life360 to access your Tile data
7. Your Tiles will appear in Life360

### Linking Jiobit Devices

1. Set up your Jiobit device using the Jiobit app first
2. Open the **Life360 mobile app**
3. Go to **Settings** → **Add Device** or **Connected Devices**
4. Select **Jiobit** or **Pet Tracker**
5. Follow the prompts to link your Jiobit account

## Home Assistant Entities

### Entity Creation

After linking devices and reloading the integration, you'll see new entities:

```
device_tracker.tile_keys
device_tracker.tile_wallet
device_tracker.jiobit_fluffy
```

### Device Names

The integration attempts to fetch device names from the Life360 `/v6/devices` metadata endpoint. However, this endpoint has authentication limitations.

**Understanding Device Naming:**

The integration will **automatically try** to fetch device names, but this may fail due to authentication token expiration (Life360 tokens typically expire after 24-48 hours). When this happens:

- ✅ **All device functionality continues to work normally**
- ⚠️ Devices will show generic names (e.g., "Tile 12345678", "Jiobit abcdef12")
- 💡 **You can manually rename** any entity in Home Assistant - this is the recommended approach

**Manual Entity Renaming (Recommended):**

1. Go to **Settings** → **Devices & Services** → **Life360**
2. Click on each device entity
3. Click the **settings icon** (⚙️)
4. Change the **"Name"** field to whatever you want
5. Names persist across restarts

This is a common pattern in Home Assistant integrations and gives you full control over entity names.

### Entity Attributes

Each device tracker entity includes:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `latitude` | Latitude coordinate | `-33.8688` |
| `longitude` | Longitude coordinate | `151.2093` |
| `gps_accuracy` | Accuracy in meters | `50` |
| `battery_level` | Battery percentage | `85` |
| `battery_status` | Battery state | `"NORMAL"` or `"LOW"` |
| `device_type` | Type of tracker | `"Tile"` or `"Pet GPS"` |
| `device_id` | Unique identifier | `"2382b0e5fdba138f"` |
| `last_seen` | Last update time | `2024-01-15 14:30:00` |

### Entity States

| State | Meaning |
|-------|---------|
| `not_home` | Device has a known location outside any zone |
| `home` | Device is in the Home zone |
| `<zone_name>` | Device is in a named zone |
| `unknown` | No location data available |

## Ringing Tiles

### Using the life360.ring_device Service

Ring your Tile devices to help locate them:

```yaml
service: life360.ring_device
data:
  entity_id: device_tracker.tile_keys
  duration: 30  # seconds (1-300)
  strength: 2   # 1=low, 2=medium, 3=high
```

**How it works:**
1. Integration scans for the Tile via Bluetooth
2. Connects to the Tile (if in BLE range)
3. Authenticates using the Tile Over Air (TOA) protocol
4. Sends ring command with specified volume and duration
5. Falls back to cloud API if BLE connection fails

**Requirements:**
- Home Assistant host must have Bluetooth hardware
- Tile must be within BLE range (~30 meters)
- Tile must have battery power

### Creating a Ring Button

Add a button to your dashboard to ring a Tile:

```yaml
type: button
name: Ring Keys
icon: mdi:key-ring
tap_action:
  action: call-service
  service: life360.ring_device
  service_data:
    entity_id: device_tracker.tile_keys
    duration: 30
    strength: 3
```

## Automation Examples

### Alert When Tile Battery is Low

```yaml
automation:
  - alias: "Tile Low Battery Alert"
    trigger:
      - platform: numeric_state
        entity_id: device_tracker.tile_keys
        attribute: battery_level
        below: 20
    action:
      - service: notify.mobile_app
        data:
          title: "Tile Battery Low"
          message: "Your keys Tile battery is at {{ state_attr('device_tracker.tile_keys', 'battery_level') }}%"
```

### Ring Tile When Leaving Home Without It

```yaml
automation:
  - alias: "Ring Keys if Forgotten"
    trigger:
      - platform: state
        entity_id: person.you
        from: "home"
    condition:
      - condition: state
        entity_id: device_tracker.tile_keys
        state: "home"
    action:
      - service: life360.ring_device
        data:
          entity_id: device_tracker.tile_keys
          duration: 10
          strength: 3
      - service: notify.mobile_app
        data:
          title: "Don't Forget Your Keys!"
          message: "Your keys are still at home"
```

### Track Pet Leaving Home

```yaml
automation:
  - alias: "Pet Left Home"
    trigger:
      - platform: state
        entity_id: device_tracker.jiobit_fluffy
        from: "home"
        to: "not_home"
    action:
      - service: notify.mobile_app
        data:
          title: "🐱 Pet Alert"
          message: "Fluffy has left home!"
```

### Log Tile Last Seen Location

```yaml
automation:
  - alias: "Log Tile Location"
    trigger:
      - platform: state
        entity_id: device_tracker.tile_wallet
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.latitude is defined }}"
    action:
      - service: logbook.log
        data:
          name: "Wallet Tile"
          message: >
            Last seen at {{ state_attr('device_tracker.tile_wallet', 'latitude') }},
            {{ state_attr('device_tracker.tile_wallet', 'longitude') }}
```

## Device ID Configuration (Optional)

> **⚠️ Important Note**: Due to Life360's token expiration (24-48 hours), device ID authentication is **not recommended** for most users. The integration will attempt to fetch names automatically, but when authentication fails, it gracefully falls back to generic names.
>
> **Recommended approach**: Use manual entity renaming in Home Assistant as described above.

### Why Device ID Has Limitations

The Life360 `/v6/devices` endpoint requires:
1. A valid device ID from a Life360 mobile app installation
2. A Bearer token from the **same device/session**

**The Problem:**
- Home Assistant uses username/password login to get tokens
- These tokens come from a different "device" than the device ID
- Life360 rejects mismatched device/token combinations
- Even with correct credentials, tokens expire after 24-48 hours
- This makes the feature impractical for long-running integrations

### Advanced: Configuring Device ID (Not Recommended)

If you still want to try device ID authentication:

#### What is a Device ID?

A device ID is a unique identifier generated by the Life360 mobile app:
- **Android**: `androidXxYyZz1234AbCdEf5678Gh` (starts with "android")
- **iOS**: Similar format starting with "ios"

#### How to Obtain Your Device ID

Use network monitoring tools to extract the device ID from Life360 app traffic:

1. Install **mitmproxy**, **Charles Proxy**, or **HTTP Toolkit**
2. Configure your phone to use the proxy
3. Monitor traffic from the Life360 app
4. Look for the `x-device-id` header in any API request
5. Copy the device ID value

#### Configuring in Home Assistant

1. Go to **Settings** → **Devices & Services**
2. Find **Life360** and click **Configure**
3. In the **Device ID** field, paste your device ID
4. Click **Submit**
5. Reload the integration

**Expected Behavior:**
- The integration will attempt to use the device ID
- If authentication fails (401/403), you'll see a clear message in logs
- The integration will continue working with generic device names
- No functionality is lost - only names may be generic

## Troubleshooting

### Devices Not Appearing

1. **Check Life360 app** - Make sure devices appear in the Life360 mobile app
2. **Reload integration** - Go to Settings → Devices & services → Life360 → Reload
3. **Check logs** - Enable debug logging to see API responses:
   ```yaml
   logger:
     logs:
       custom_components.life360: debug
   ```

### Stale Location Data

Tile locations may be outdated if:
- No Life360 users have been near the Tile recently
- The Tile is out of Bluetooth range of all phones

Jiobit locations may be delayed if:
- The device is in a low-signal area
- Battery saver mode is enabled

### Device Shows "Unknown" State

This means no location data is available. Possible causes:
- Device was just linked and hasn't reported yet
- Device is offline or out of range
- API rate limiting (wait a few minutes)

### Devices Show Generic Names

If your Tile/Jiobit devices show names like "Tile 12345678" instead of their actual names, **this is expected behavior** due to Life360's token expiration.

**What's Happening:**
- The integration tried to fetch device names from Life360
- Authentication failed (401/403) due to token expiration
- The integration fell back to generic names
- All functionality still works - only the display name is affected

**Solution (Recommended):**
Simply rename the entities in Home Assistant:
1. Go to **Settings** → **Devices & Services** → **Life360**
2. Click on the device entity
3. Click the **settings icon** (⚙️)
4. Change the **"Name"** field to whatever you want

This is **by design** - manual renaming gives you full control and avoids authentication issues.

**Check Logs (Optional):**
If you're curious about what happened, check your Home Assistant logs. You'll see messages like:
```
⚠️ Device ID authentication failed (HTTP 401)
📝 DEVICE NAMING LIMITATION
💡 Your Tile/Jiobit devices will appear with generic names...
```

**Advanced Option:**
If you want to try device ID configuration despite the limitations, see [Device ID Configuration](#device-id-configuration-optional) above. However, this is not recommended for most users.

## API Details

The integration fetches device data from two endpoints:

### Device Locations

```
GET /v5/circles/devices/locations?providers[]=tile&providers[]=jiobit
```

Returns:
- Device ID
- Location coordinates
- Battery information
- Last update timestamp

### Device Metadata (Names)

```
GET /v6/devices?activationStates=activated,pending,pending_disassociated
```

Requires authentication with the `x-device-id` header containing a valid Life360 device ID.

Returns:
- Device names and display information
- Device types and categories
- Avatar/icon URLs

Without a valid device ID, this endpoint returns HTTP 401 (Unauthorized), causing the integration to fall back to generic device names.

See [API Endpoints](api_endpoints.md) for more details.

## Limitations

- **Tile accuracy** - Depends on crowdsourced phone proximity, not true GPS
- **Update frequency** - Limited by Life360 API rate limits (typically 5-second intervals)
- **Historical data** - Only current location is available, no tracking history
- **Tile network coverage** - Rural areas may have less frequent updates
