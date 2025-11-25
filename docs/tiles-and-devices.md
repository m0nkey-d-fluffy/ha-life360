# Tile & Device Tracker Support

This document explains how the Life360 integration supports Tile Bluetooth trackers and Jiobit pet GPS trackers.

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

The integration fetches device names from the Life360 `/v6/devices` metadata endpoint. However, this endpoint requires authentication using a valid Life360 device ID.

**With Device ID Configured:**
- Devices show their actual names from Life360 (e.g., "Keys", "Wallet", "Fluffy")
- Names automatically sync from the Life360 app

**Without Device ID Configured:**
- Devices show generic names based on their ID (e.g., "Tile 12345678")
- You can manually rename entities in Home Assistant's entity settings
- All other functionality works normally

See [Configuring Device ID](#configuring-device-id) below for instructions.

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

## Configuring Device ID

To enable proper device names, you need to provide your Life360 device ID:

### What is a Device ID?

A device ID is a unique identifier generated by the Life360 mobile app when installed on your Android or iOS device. It looks like:
- **Android**: `androideDb6Dr3GQuOfOkQqpaiV6t` (starts with "android", 29 characters total)
- **iOS**: Similar format starting with "ios"

### How to Obtain Your Device ID

Your device ID is embedded in the Life360 app's network traffic. To extract it:

1. **Using Network Monitoring Tools** (Advanced)
   - Use a network monitoring tool like mitmproxy, Charles Proxy, or HTTP Toolkit
   - Monitor traffic from the Life360 app on your phone
   - Look for the `x-device-id` header in any API request
   - Example: `x-device-id: androideDb6Dr3GQuOfOkQqpaiV6t`

2. **Using ADB for Android** (Advanced)
   - The device ID may be stored in the Life360 app's shared preferences
   - Requires rooted device or ADB access
   - Location varies by app version

### Configuring in Home Assistant

Once you have your device ID:

1. Go to **Settings** → **Devices & Services**
2. Find **Life360** and click **Configure**
3. In the **Device ID** field, paste your device ID
4. Click **Submit**
5. Reload the integration

> **Note**: The device ID is optional. If not configured, Tile/Jiobit devices will show generic names that you can manually rename in Home Assistant.

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

If your Tile/Jiobit devices show names like "Tile 12345678" instead of their actual names:

1. **Configure your device ID** - See [Configuring Device ID](#configuring-device-id) above
2. **Check logs** - Look for messages about device ID configuration:
   ```
   No device ID configured. Tile/Jiobit devices will show generic names.
   ```
3. **Verify device ID format** - Make sure the ID matches the format from your Life360 app
4. **Manual workaround** - You can rename entities in Home Assistant:
   - Go to Settings → Devices & Services → Life360
   - Click on the device entity
   - Click the settings icon
   - Change the "Name" field

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
