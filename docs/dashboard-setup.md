# Dashboard Setup Guide

This guide shows you how to add Tile and Jiobit devices to your Home Assistant dashboard with easy-to-use buttons for ringing and tracking.

## Quick Setup: Tile Ring Button

### Step 1: Find Your Tile Entity ID

1. Go to **Settings** → **Devices & Services** → **Life360**
2. Look for entities starting with `device_tracker.tile_`
3. Write down the entity ID (e.g., `device_tracker.tile_1fd609ad`)

**Your Tile entities:**
- `device_tracker.tile_1fd609ad`
- `device_tracker.tile_9406d9fe`
- `device_tracker.tile_00caf133`
- `device_tracker.tile_9c37e810`

### Step 2: Add a Grid Card to Your Dashboard

1. Edit your dashboard (three dots → **Edit Dashboard**)
2. Click **+ Add Card**
3. Search for **Grid Card**
4. Click **Show Code Editor** (bottom left)
5. Paste the configuration below
6. Click **Save**

### Complete Tile Card Example

This creates a card with:
- 🗺️ Status tile showing location
- 🔔 Ring button (30 seconds)
- 🔕 Stop button

```yaml
type: grid
cards:
  # Status Card - Shows location and battery
  - type: button
    entity: device_tracker.tile_1fd609ad
    name: Keys Tile
    icon: mdi:key
    show_name: true
    show_icon: true
    show_state: true
    tap_action:
      action: more-info

  # Ring Button - Press to make it beep
  - type: button
    name: Ring
    icon: mdi:bell-ring
    show_name: true
    show_icon: true
    tap_action:
      action: perform-action
      perform_action: life360.ring_device
      data:
        entity_id: device_tracker.tile_1fd609ad
        duration: 30
        strength: 2
    hold_action:
      action: none

  # Stop Button - Press to stop ringing
  - type: button
    name: Stop
    icon: mdi:bell-off
    show_name: true
    show_icon: true
    tap_action:
      action: perform-action
      perform_action: life360.stop_ring_device
      data:
        entity_id: device_tracker.tile_1fd609ad
    hold_action:
      action: none

columns: 3
square: false
```

**To customize:**
- Replace `device_tracker.tile_1fd609ad` with YOUR Tile entity ID
- Change `name: Keys Tile` to match what the Tile tracks (Wallet, Backpack, etc.)
- Change `icon: mdi:key` to match your item:
  - Keys: `mdi:key`
  - Wallet: `mdi:wallet`
  - Backpack: `mdi:backpack`
  - Pet: `mdi:paw`
  - Phone: `mdi:cellphone`

### Ring Settings Explained

| Setting | What It Does | Options |
|---------|-------------|---------|
| `duration` | How long the Tile rings | 1-300 seconds (default: 30) |
| `strength` | How loud the ring is | 1 (quiet), 2 (medium), 3 (loud) |

**Example variations:**
```yaml
# Quick 10-second beep
duration: 10
strength: 2

# Long loud ring for lost items
duration: 60
strength: 3

# Quiet reminder beep
duration: 5
strength: 1
```

## Advanced: Multiple Tiles in One Card

Want to control all your Tiles from one card? Here's how:

```yaml
type: grid
cards:
  # Keys Tile
  - type: button
    entity: device_tracker.tile_1fd609ad
    name: Keys
    icon: mdi:key
    tap_action:
      action: perform-action
      perform_action: life360.ring_device
      data:
        entity_id: device_tracker.tile_1fd609ad
        duration: 30
        strength: 2

  # Wallet Tile
  - type: button
    entity: device_tracker.tile_9406d9fe
    name: Wallet
    icon: mdi:wallet
    tap_action:
      action: perform-action
      perform_action: life360.ring_device
      data:
        entity_id: device_tracker.tile_9406d9fe
        duration: 30
        strength: 2

  # Backpack Tile
  - type: button
    entity: device_tracker.tile_00caf133
    name: Backpack
    icon: mdi:backpack
    tap_action:
      action: perform-action
      perform_action: life360.ring_device
      data:
        entity_id: device_tracker.tile_00caf133
        duration: 30
        strength: 2

  # Phone Tile
  - type: button
    entity: device_tracker.tile_9c37e810
    name: Phone
    icon: mdi:cellphone
    tap_action:
      action: perform-action
      perform_action: life360.ring_device
      data:
        entity_id: device_tracker.tile_9c37e810
        duration: 30
        strength: 2

columns: 2
square: true
```

## Quick Copy Templates

### Single Ring Button (Minimal)

```yaml
type: button
icon: mdi:bell-ring
name: Ring Keys
tap_action:
  action: perform-action
  perform_action: life360.ring_device
  data:
    entity_id: device_tracker.tile_1fd609ad
    duration: 30
    strength: 2
```

### Ring + Stop Buttons (Horizontal)

```yaml
type: horizontal-stack
cards:
  - type: button
    icon: mdi:bell-ring
    name: Ring
    tap_action:
      action: perform-action
      perform_action: life360.ring_device
      data:
        entity_id: device_tracker.tile_1fd609ad
        duration: 30
        strength: 2

  - type: button
    icon: mdi:bell-off
    name: Stop
    tap_action:
      action: perform-action
      perform_action: life360.stop_ring_device
      data:
        entity_id: device_tracker.tile_1fd609ad
```

## Jiobit Pet Tracker Setup

Same process, but use your Jiobit entity:

```yaml
type: grid
cards:
  # Pet Status
  - type: button
    entity: device_tracker.jiobit_fluffy
    name: Fluffy
    icon: mdi:paw
    show_name: true
    show_icon: true
    show_state: true
    tap_action:
      action: more-info

  # Buzz Pet Tracker
  - type: button
    name: Buzz
    icon: mdi:bell-ring
    tap_action:
      action: perform-action
      perform_action: life360.buzz_jiobit
      data:
        entity_id: device_tracker.jiobit_fluffy

columns: 2
```

## Troubleshooting Dashboard Issues

### "Service not found" Error

**Problem:** You get an error when clicking the ring button.

**Solution:**
1. Make sure the Life360 integration is loaded
2. Reload the integration: **Settings** → **Devices & Services** → **Life360** → **⋮** → **Reload**
3. Restart Home Assistant if needed

### "Entity not found" Error

**Problem:** The button shows "Entity not available".

**Solution:**
1. Check the entity ID is correct (go to **Developer Tools** → **States**)
2. Make sure you're using the Tile entity (`device_tracker.tile_XXXXXX`), NOT the person tracker
3. Reload the Life360 integration

### Button Doesn't Do Anything

**Problem:** Clicking the ring button doesn't make the Tile beep.

**Solution:**
1. Check Home Assistant logs: **Settings** → **System** → **Logs**
2. Look for errors mentioning "Tile" or "BLE"
3. Make sure:
   - The Tile is nearby (Bluetooth range ~30-50 feet)
   - The Tile has battery power
   - The Tile is linked to your Life360 account
4. Try the `life360.get_devices` service to verify the Tile is detected

### How to Check Logs

1. Go to **Settings** → **System** → **Logs**
2. Use the search box at the top
3. Search for: `life360`
4. Look for emoji indicators:
   - 🔍 = Scanning for Tile
   - 🔌 = Connecting
   - 🔐 = Authenticating
   - 🔔 = Ringing
   - ✅ = Success
   - ❌ = Error

## Using Developer Tools to Test

Before creating dashboard cards, test your services:

1. Go to **Developer Tools** → **Services**
2. Select service: `life360.ring_device`
3. Fill in:
   ```yaml
   entity_id: device_tracker.tile_1fd609ad
   duration: 30
   strength: 2
   ```
4. Click **Call Service**
5. Check if your Tile rings

## Icon Reference

Common icons you can use:

| Item | Icon Code |
|------|-----------|
| Keys | `mdi:key` |
| Wallet | `mdi:wallet` |
| Backpack | `mdi:backpack` |
| Bag | `mdi:bag-personal` |
| Purse | `mdi:purse` |
| Phone | `mdi:cellphone` |
| Tablet | `mdi:tablet` |
| Laptop | `mdi:laptop` |
| Pet | `mdi:paw` |
| Dog | `mdi:dog` |
| Cat | `mdi:cat` |
| Remote | `mdi:remote` |
| Car Keys | `mdi:car-key` |
| Bike | `mdi:bike` |
| Luggage | `mdi:bag-suitcase` |

Browse more at: [Material Design Icons](https://pictogrammers.com/library/mdi/)

## Next Steps

- **Rename your entities** for easier identification: [Entity Renaming Guide](tiles-and-devices.md#manual-entity-renaming-recommended)
- **Set up automations** to ring tiles automatically: [Automation Examples](tiles-and-devices.md#automation-examples)
- **Create zones** for location tracking: See Home Assistant zones documentation
