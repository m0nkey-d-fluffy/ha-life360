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

Entity names are derived from the device names in Life360.

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

## API Details

The integration fetches device data from:

```
GET /v5/circles/devices/locations?providers[]=tile&providers[]=jiobit
```

Response includes:
- Device ID and name
- Location coordinates
- Battery information
- Last update timestamp

See [API Endpoints](api_endpoints.md) for more details.

## Limitations

- **Tile accuracy** - Depends on crowdsourced phone proximity, not true GPS
- **Update frequency** - Limited by Life360 API rate limits (typically 5-second intervals)
- **Historical data** - Only current location is available, no tracking history
- **Tile network coverage** - Rural areas may have less frequent updates
