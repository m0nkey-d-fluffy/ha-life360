# Dashboard Card Examples
## Home Assistant Lovelace Cards for Life360 Integration

**Integration**: ha-life360
**Version**: 0.7.0+
**Features**: Tile/Jiobit devices, GPS tracking, battery monitoring, BLE ringing

---

## Table of Contents

1. [Tile Device Cards](#tile-device-cards)
2. [Jiobit Device Cards](#jiobit-device-cards)
3. [GPS Map Cards](#gps-map-cards)
4. [Battery Monitoring](#battery-monitoring)
5. [Combined Dashboard Views](#combined-dashboard-views)
6. [Automation Examples](#automation-examples)

---

## Tile Device Cards

### 1. Simple Tile Card with Ring Button

Basic card showing Tile location with a button to ring it via BLE.

```yaml
type: entities
title: Tile Keys
entities:
  - entity: device_tracker.tile_keys
    secondary_info: last-changed
  - type: attribute
    entity: device_tracker.tile_keys
    attribute: battery
    name: Battery
    icon: mdi:battery
  - type: button
    name: Ring Tile
    icon: mdi:bell-ring
    action_row: true
    tap_action:
      action: call-service
      service: life360.ring_device
      service_data:
        entity_id: device_tracker.tile_keys
        duration: 30
        strength: 3
```

### 2. Tile Card with Multiple Actions

Card with ring, stop ring, and location update buttons.

```yaml
type: custom:mushroom-entity-card
entity: device_tracker.tile_keys
name: Tile - Wallet
icon: mdi:wallet
tap_action:
  action: more-info
hold_action:
  action: call-service
  service: life360.ring_device
  service_data:
    entity_id: device_tracker.tile_keys
    duration: 15
    strength: 2
```

**Additional buttons card:**
```yaml
type: horizontal-stack
cards:
  - type: button
    name: Ring
    icon: mdi:bell-ring
    tap_action:
      action: call-service
      service: life360.ring_device
      service_data:
        entity_id: device_tracker.tile_keys
        duration: 30
        strength: 3

  - type: button
    name: Stop
    icon: mdi:bell-off
    tap_action:
      action: call-service
      service: life360.stop_ring_device
      service_data:
        entity_id: device_tracker.tile_keys

  - type: button
    name: Locate
    icon: mdi:map-marker
    tap_action:
      action: call-service
      service: life360.update_location
      target:
        entity_id: device_tracker.tile_keys
```

### 3. Tile Status Card with Conditional Ring Button

Shows different states and only displays ring button when BLE is available.

```yaml
type: conditional
conditions:
  - entity: binary_sensor.home_assistant_bluetooth
    state: "on"
card:
  type: vertical-stack
  cards:
    - type: glance
      title: My Tiles
      entities:
        - entity: device_tracker.tile_keys
          name: Keys
        - entity: device_tracker.tile_wallet
          name: Wallet
        - entity: device_tracker.tile_backpack
          name: Backpack

    - type: horizontal-stack
      cards:
        - type: button
          name: Ring Keys
          icon: mdi:key
          tap_action:
            action: call-service
            service: life360.ring_device
            service_data:
              entity_id: device_tracker.tile_keys

        - type: button
          name: Ring Wallet
          icon: mdi:wallet
          tap_action:
            action: call-service
            service: life360.ring_device
            service_data:
              entity_id: device_tracker.tile_wallet

        - type: button
          name: Ring Backpack
          icon: mdi:bag-personal
          tap_action:
            action: call-service
            service: life360.ring_device
            service_data:
              entity_id: device_tracker.tile_backpack
```

### 4. Picture Elements Card - Interactive Tile

Visual card with tap zones for different actions.

```yaml
type: picture-elements
image: /local/images/tile-pro.png
elements:
  - type: state-badge
    entity: device_tracker.tile_keys
    style:
      top: 20%
      left: 20%

  - type: state-label
    entity: device_tracker.tile_keys
    attribute: battery
    prefix: "Battery: "
    suffix: "%"
    style:
      top: 40%
      left: 50%
      color: white
      font-size: 18px

  - type: icon
    icon: mdi:bell-ring
    tap_action:
      action: call-service
      service: life360.ring_device
      service_data:
        entity_id: device_tracker.tile_keys
        duration: 30
        strength: 3
    style:
      top: 70%
      left: 30%
      color: orange
      "--mdc-icon-size": 48px

  - type: icon
    icon: mdi:map-marker
    tap_action:
      action: call-service
      service: life360.update_location
      target:
        entity_id: device_tracker.tile_keys
    style:
      top: 70%
      left: 70%
      color: blue
      "--mdc-icon-size": 48px
```

---

## Jiobit Device Cards

### 1. Jiobit Pet Tracker Card

Card for tracking pets with buzz and lost mode features.

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-entity-card
    entity: device_tracker.jiobit_fluffy
    name: Fluffy's Tracker
    icon: mdi:dog
    primary_info: name
    secondary_info: last-changed
    icon_color: orange

  - type: grid
    square: false
    columns: 2
    cards:
      - type: button
        name: Buzz
        icon: mdi:vibrate
        tap_action:
          action: call-service
          service: life360.buzz_jiobit
          service_data:
            entity_id: device_tracker.jiobit_fluffy

      - type: button
        name: Lost Mode
        icon: mdi:alert-circle
        tap_action:
          action: call-service
          service: life360.jiobit_lost_mode
          service_data:
            entity_id: device_tracker.jiobit_fluffy
            activate: true
        hold_action:
          action: call-service
          service: life360.jiobit_lost_mode
          service_data:
            entity_id: device_tracker.jiobit_fluffy
            activate: false

  - type: attribute
    entity: device_tracker.jiobit_fluffy
    attribute: battery
    name: Battery Level
    icon: mdi:battery

  - type: attribute
    entity: device_tracker.jiobit_fluffy
    attribute: gps_accuracy
    name: GPS Accuracy
    icon: mdi:crosshairs-gps
    unit: meters
```

### 2. Jiobit with All Controls

Complete control panel for Jiobit devices.

```yaml
type: entities
title: Jiobit - Max (Dog)
state_color: true
entities:
  - entity: device_tracker.jiobit_max
    secondary_info: last-changed

  - type: section
    label: Device Info

  - type: attribute
    entity: device_tracker.jiobit_max
    attribute: battery
    name: Battery
    icon: mdi:battery
    suffix: "%"

  - type: attribute
    entity: device_tracker.jiobit_max
    attribute: altitude
    name: Altitude
    icon: mdi:elevation-rise

  - type: attribute
    entity: device_tracker.jiobit_max
    attribute: gps_accuracy
    name: Accuracy
    icon: mdi:crosshairs-gps

  - type: section
    label: Actions

  - type: button
    name: Buzz Jiobit
    icon: mdi:vibrate
    action_row: true
    tap_action:
      action: call-service
      service: life360.buzz_jiobit
      service_data:
        entity_id: device_tracker.jiobit_max

  - type: button
    name: Ring (30s, High)
    icon: mdi:bell-ring
    action_row: true
    tap_action:
      action: call-service
      service: life360.ring_device
      service_data:
        entity_id: device_tracker.jiobit_max
        duration: 30
        strength: 3

  - type: button
    name: Toggle Light
    icon: mdi:lightbulb
    action_row: true
    tap_action:
      action: call-service
      service: life360.toggle_light
      service_data:
        device_id: dr123456
        circle_id: abc123-def456
        enable: true

  - type: section
    label: Lost Mode

  - type: button
    name: Activate Lost Mode
    icon: mdi:alert-circle-outline
    action_row: true
    tap_action:
      action: call-service
      service: life360.jiobit_lost_mode
      service_data:
        entity_id: device_tracker.jiobit_max
        activate: true

  - type: button
    name: Deactivate Lost Mode
    icon: mdi:check-circle-outline
    action_row: true
    tap_action:
      action: call-service
      service: life360.jiobit_lost_mode
      service_data:
        entity_id: device_tracker.jiobit_max
        activate: false
```

### 3. Pet Location Dashboard

Multi-pet tracking dashboard.

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: |
      # 🐾 Pet Tracking Dashboard
      Last updated: {{ now().strftime('%I:%M %p') }}

  - type: grid
    columns: 2
    square: false
    cards:
      - type: custom:mushroom-entity-card
        entity: device_tracker.jiobit_fluffy
        name: Fluffy
        icon: mdi:dog
        icon_color: orange
        tap_action:
          action: call-service
          service: life360.buzz_jiobit
          service_data:
            entity_id: device_tracker.jiobit_fluffy

      - type: custom:mushroom-entity-card
        entity: device_tracker.jiobit_whiskers
        name: Whiskers
        icon: mdi:cat
        icon_color: blue
        tap_action:
          action: call-service
          service: life360.buzz_jiobit
          service_data:
            entity_id: device_tracker.jiobit_whiskers

  - type: map
    entities:
      - device_tracker.jiobit_fluffy
      - device_tracker.jiobit_whiskers
    hours_to_show: 2
    aspect_ratio: 16:9
```

---

## GPS Map Cards

### 1. Basic Map with All Life360 Members

Simple map showing all tracked entities.

```yaml
type: map
title: Family & Devices
entities:
  - device_tracker.life360_john
  - device_tracker.life360_jane
  - device_tracker.tile_keys
  - device_tracker.jiobit_fluffy
hours_to_show: 24
aspect_ratio: 16:9
default_zoom: 13
```

### 2. Advanced Map with Route History

Shows movement history with custom styling.

```yaml
type: custom:better-map-card
entities:
  - entity: device_tracker.life360_john
    label: John
    color: blue
  - entity: device_tracker.tile_keys
    label: Keys
    color: orange
  - entity: device_tracker.jiobit_fluffy
    label: Fluffy
    color: green
hours_to_show: 6
show_path: true
path_color: rgba(0, 123, 255, 0.5)
```

### 3. Google Maps Style Card

Using custom card for Google Maps style.

```yaml
type: custom:google-maps-card
entities:
  - entity: device_tracker.life360_john
    icon: mdi:account
    color: blue
  - entity: device_tracker.life360_jane
    icon: mdi:account
    color: pink
  - entity: device_tracker.tile_wallet
    icon: mdi:wallet
    color: brown
  - entity: device_tracker.jiobit_max
    icon: mdi:dog
    color: orange
options:
  zoom: 15
  center:
    lat: 37.7749
    lng: -122.4194
  map_type: roadmap
  styles:
    - elementType: geometry
      stylers:
        - color: "#242f3e"
```

### 4. Map with Proximity Zones

Shows device locations relative to home zones.

```yaml
type: vertical-stack
cards:
  - type: glance
    title: Distance from Home
    entities:
      - entity: sensor.life360_john_distance
        name: John
      - entity: sensor.tile_keys_distance
        name: Keys
      - entity: sensor.jiobit_fluffy_distance
        name: Fluffy

  - type: map
    entities:
      - device_tracker.life360_john
      - device_tracker.tile_keys
      - device_tracker.jiobit_fluffy
      - zone.home
      - zone.work
    hours_to_show: 1
    aspect_ratio: 16:9
```

---

## Battery Monitoring

### 1. Battery Level Gauge Cards

Visual battery indicators for all devices.

```yaml
type: horizontal-stack
cards:
  - type: gauge
    entity: sensor.tile_keys_battery
    name: Keys Tile
    min: 0
    max: 100
    severity:
      green: 40
      yellow: 20
      red: 0
    needle: true

  - type: gauge
    entity: sensor.tile_wallet_battery
    name: Wallet Tile
    min: 0
    max: 100
    severity:
      green: 40
      yellow: 20
      red: 0
    needle: true

  - type: gauge
    entity: sensor.jiobit_fluffy_battery
    name: Fluffy's Jiobit
    min: 0
    max: 100
    severity:
      green: 30
      yellow: 15
      red: 0
    needle: true
```

### 2. Battery Status List

Simple list view of all device batteries.

```yaml
type: entities
title: Device Battery Status
state_color: true
entities:
  - type: attribute
    entity: device_tracker.tile_keys
    attribute: battery
    name: Tile - Keys
    icon: mdi:key
    suffix: "%"

  - type: attribute
    entity: device_tracker.tile_wallet
    attribute: battery
    name: Tile - Wallet
    icon: mdi:wallet
    suffix: "%"

  - type: attribute
    entity: device_tracker.tile_backpack
    attribute: battery
    name: Tile - Backpack
    icon: mdi:bag-personal
    suffix: "%"

  - type: attribute
    entity: device_tracker.jiobit_fluffy
    attribute: battery
    name: Jiobit - Fluffy
    icon: mdi:dog
    suffix: "%"

  - type: attribute
    entity: device_tracker.life360_john_phone
    attribute: battery
    name: John's Phone
    icon: mdi:cellphone
    suffix: "%"
```

### 3. Low Battery Alert Card

Conditional card showing only devices with low battery.

```yaml
type: conditional
conditions:
  - entity: binary_sensor.any_device_low_battery
    state: "on"
card:
  type: markdown
  content: |
    ## ⚠️ Low Battery Alert

    {% set low_battery = namespace(devices=[]) %}
    {% for entity in states.device_tracker %}
      {% if entity.attributes.battery is defined and entity.attributes.battery | int < 20 %}
        {% set low_battery.devices = low_battery.devices + [entity.name ~ ': ' ~ entity.attributes.battery ~ '%'] %}
      {% endif %}
    {% endfor %}

    {% if low_battery.devices | length > 0 %}
    The following devices need charging:
    {% for device in low_battery.devices %}
    - {{ device }}
    {% endfor %}
    {% else %}
    All devices have sufficient battery! ✅
    {% endif %}
```

### 4. Battery History Graph

Track battery levels over time.

```yaml
type: custom:mini-graph-card
name: Battery Levels - 7 Days
icon: mdi:battery
hours_to_show: 168
points_per_hour: 1
entities:
  - entity: sensor.tile_keys_battery
    name: Keys
    color: orange
  - entity: sensor.tile_wallet_battery
    name: Wallet
    color: brown
  - entity: sensor.jiobit_fluffy_battery
    name: Fluffy
    color: green
show:
  labels: true
  points: false
  legend: true
```

---

## Combined Dashboard Views

### 1. Complete Family Dashboard

All-in-one dashboard for family tracking.

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: |
      # 👨‍👩‍👧‍👦 Family Dashboard
      {{ now().strftime('%A, %B %d at %I:%M %p') }}

  - type: glance
    title: Family Members
    show_state: false
    entities:
      - entity: device_tracker.life360_john
        name: John
        icon: mdi:account
      - entity: device_tracker.life360_jane
        name: Jane
        icon: mdi:account
      - entity: device_tracker.life360_emily
        name: Emily
        icon: mdi:account

  - type: map
    entities:
      - device_tracker.life360_john
      - device_tracker.life360_jane
      - device_tracker.life360_emily
    hours_to_show: 4
    aspect_ratio: 16:9

  - type: entities
    title: Tracked Devices
    entities:
      - device_tracker.tile_keys
      - device_tracker.tile_wallet
      - device_tracker.jiobit_fluffy

  - type: horizontal-stack
    cards:
      - type: button
        name: Update All
        icon: mdi:refresh
        tap_action:
          action: call-service
          service: life360.update_location
          target:
            entity_id: all

      - type: button
        name: Ring Keys
        icon: mdi:key
        tap_action:
          action: call-service
          service: life360.ring_device
          service_data:
            entity_id: device_tracker.tile_keys
```

### 2. Pet Care Dashboard

Complete pet monitoring and control.

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-title-card
    title: Pet Care Dashboard
    subtitle: Jiobit Tracking & Control

  - type: custom:mushroom-chips-card
    chips:
      - type: entity
        entity: device_tracker.jiobit_fluffy
        icon: mdi:dog
        icon_color: orange

      - type: template
        icon: mdi:battery
        content: "{{ state_attr('device_tracker.jiobit_fluffy', 'battery') }}%"
        icon_color: >
          {% set battery = state_attr('device_tracker.jiobit_fluffy', 'battery') | int %}
          {% if battery > 40 %}green
          {% elif battery > 20 %}orange
          {% else %}red{% endif %}

      - type: template
        icon: mdi:map-marker-distance
        content: "{{ states('sensor.jiobit_fluffy_distance') }}"
        icon_color: blue

  - type: map
    entities:
      - device_tracker.jiobit_fluffy
      - zone.home
      - zone.dog_park
    hours_to_show: 2
    aspect_ratio: 16:9

  - type: grid
    columns: 3
    square: false
    cards:
      - type: button
        name: Buzz
        icon: mdi:vibrate
        tap_action:
          action: call-service
          service: life360.buzz_jiobit
          service_data:
            entity_id: device_tracker.jiobit_fluffy

      - type: button
        name: Light
        icon: mdi:lightbulb
        tap_action:
          action: call-service
          service: life360.toggle_light
          service_data:
            device_id: dr123456
            circle_id: abc123-def456
            enable: true

      - type: button
        name: Lost Mode
        icon: mdi:alert-circle
        tap_action:
          action: call-service
          service: life360.jiobit_lost_mode
          service_data:
            entity_id: device_tracker.jiobit_fluffy
            activate: true

  - type: entities
    title: Activity Log
    entities:
      - type: attribute
        entity: device_tracker.jiobit_fluffy
        attribute: last_seen
        name: Last Seen
        icon: mdi:clock

      - type: attribute
        entity: device_tracker.jiobit_fluffy
        attribute: gps_accuracy
        name: GPS Accuracy
        icon: mdi:crosshairs-gps

      - type: attribute
        entity: device_tracker.jiobit_fluffy
        attribute: altitude
        name: Altitude
        icon: mdi:elevation-rise
```

### 3. Smart Home Finder Panel

Quick access to find anything in your home.

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-title-card
    title: Find My Stuff
    subtitle: Ring any device instantly

  - type: custom:layout-card
    layout_type: grid
    layout_options:
      grid-template-columns: 1fr 1fr
      grid-gap: 8px
    cards:
      - type: custom:mushroom-template-card
        primary: Keys
        secondary: "Battery: {{ state_attr('device_tracker.tile_keys', 'battery') }}%"
        icon: mdi:key
        icon_color: orange
        tap_action:
          action: call-service
          service: life360.ring_device
          service_data:
            entity_id: device_tracker.tile_keys
            duration: 30
            strength: 3

      - type: custom:mushroom-template-card
        primary: Wallet
        secondary: "Battery: {{ state_attr('device_tracker.tile_wallet', 'battery') }}%"
        icon: mdi:wallet
        icon_color: brown
        tap_action:
          action: call-service
          service: life360.ring_device
          service_data:
            entity_id: device_tracker.tile_wallet
            duration: 30
            strength: 3

      - type: custom:mushroom-template-card
        primary: Backpack
        secondary: "Battery: {{ state_attr('device_tracker.tile_backpack', 'battery') }}%"
        icon: mdi:bag-personal
        icon_color: blue
        tap_action:
          action: call-service
          service: life360.ring_device
          service_data:
            entity_id: device_tracker.tile_backpack
            duration: 30
            strength: 3

      - type: custom:mushroom-template-card
        primary: Remote
        secondary: "Battery: {{ state_attr('device_tracker.tile_remote', 'battery') }}%"
        icon: mdi:remote
        icon_color: purple
        tap_action:
          action: call-service
          service: life360.ring_device
          service_data:
            entity_id: device_tracker.tile_remote
            duration: 30
            strength: 3

  - type: markdown
    content: |
      **💡 Tip:** Tap any item to ring it via Bluetooth.
      Make sure you're within ~30 meters of the Tile!
```

---

## Automation Examples

### 1. Auto-Ring Keys When Leaving

Ring Tile when leaving home to ensure you have it.

```yaml
automation:
  - alias: "Ring Keys When Leaving Home"
    trigger:
      - platform: state
        entity_id: device_tracker.life360_john
        from: "home"
        to: "not_home"
    condition:
      - condition: state
        entity_id: device_tracker.tile_keys
        state: "home"
    action:
      - service: life360.ring_device
        data:
          entity_id: device_tracker.tile_keys
          duration: 15
          strength: 3
      - service: notify.mobile_app_johns_phone
        data:
          message: "Don't forget your keys! 🔑"
```

### 2. Low Battery Notifications

Alert when any device battery is low.

```yaml
automation:
  - alias: "Low Battery Alert - Tiles"
    trigger:
      - platform: numeric_state
        entity_id:
          - device_tracker.tile_keys
          - device_tracker.tile_wallet
          - device_tracker.tile_backpack
        attribute: battery
        below: 15
    action:
      - service: notify.mobile_app
        data:
          title: "🪫 Low Battery Alert"
          message: >
            {{ trigger.to_state.name }} battery is at
            {{ trigger.to_state.attributes.battery }}%
```

### 3. Pet Geofence Alert

Alert when pet leaves safe zone.

```yaml
automation:
  - alias: "Pet Left Safe Zone"
    trigger:
      - platform: zone
        entity_id: device_tracker.jiobit_fluffy
        zone: zone.home
        event: leave
    action:
      - service: life360.buzz_jiobit
        data:
          entity_id: device_tracker.jiobit_fluffy

      - service: notify.mobile_app
        data:
          title: "🐕 Fluffy Alert!"
          message: "Fluffy has left the safe zone!"
          data:
            actions:
              - action: ACTIVATE_LOST_MODE
                title: "Activate Lost Mode"
              - action: LOCATE_PET
                title: "Show Location"
```

### 4. Scheduled Pet Check-In

Daily reminder to check pet tracker battery.

```yaml
automation:
  - alias: "Daily Pet Tracker Check"
    trigger:
      - platform: time
        at: "20:00:00"
    action:
      - service: notify.mobile_app
        data:
          title: "🐾 Daily Pet Check"
          message: >
            Fluffy's tracker battery:
            {{ state_attr('device_tracker.jiobit_fluffy', 'battery') }}%
            Last seen: {{ relative_time(states.device_tracker.jiobit_fluffy.last_updated) }} ago
```

### 5. Auto Lost Mode on Distance

Automatically activate lost mode if pet gets too far.

```yaml
automation:
  - alias: "Auto Lost Mode - Pet Far From Home"
    trigger:
      - platform: numeric_state
        entity_id: sensor.jiobit_fluffy_distance
        above: 500  # meters
        for:
          minutes: 10
    action:
      - service: life360.jiobit_lost_mode
        data:
          entity_id: device_tracker.jiobit_fluffy
          activate: true

      - service: notify.mobile_app
        data:
          title: "🚨 Pet Lost Mode Activated"
          message: "Fluffy is {{ states('sensor.jiobit_fluffy_distance') }} from home!"
          data:
            actions:
              - action: DEACTIVATE_LOST_MODE
                title: "Deactivate Lost Mode"
```

---

## Service Call Examples

### Direct Service Calls via Developer Tools

#### Ring a Tile
```yaml
service: life360.ring_device
data:
  entity_id: device_tracker.tile_keys
  duration: 30
  strength: 3
```

#### Buzz a Jiobit
```yaml
service: life360.buzz_jiobit
data:
  entity_id: device_tracker.jiobit_fluffy
```

#### Activate Lost Mode
```yaml
service: life360.jiobit_lost_mode
data:
  entity_id: device_tracker.jiobit_fluffy
  activate: true
```

#### Stop Ringing
```yaml
service: life360.stop_ring_device
data:
  entity_id: device_tracker.tile_keys
```

#### Update Location
```yaml
service: life360.update_location
target:
  entity_id: device_tracker.life360_john
```

---

## Custom Card Requirements

Some examples above use custom cards. Install via HACS:

- **mushroom-cards**: `custom:mushroom-*`
- **mini-graph-card**: `custom:mini-graph-card`
- **layout-card**: `custom:layout-card`
- **google-maps-card**: `custom:google-maps-card` (if using Google Maps)

Install via HACS → Frontend → Search for card name.

---

## Tips & Best Practices

### For Tiles
- ✅ **BLE Range**: Tiles require Bluetooth proximity (~30m)
- ✅ **Battery Life**: Ring sparingly to conserve battery
- ✅ **Placement**: Keep HA within BLE range of commonly used areas
- ⚠️ **No Cloud**: Tiles cannot be rung remotely - BLE only

### For Jiobits
- ✅ **Remote Ring**: Jiobits work anywhere with cellular coverage
- ✅ **Lost Mode**: Use for enhanced tracking when missing
- ✅ **Battery Alerts**: Set up automations for low battery (<20%)
- ✅ **Geofencing**: Create zones for automatic alerts

### For Maps
- 🗺️ **History**: Use `hours_to_show` to see movement patterns
- 🗺️ **Zones**: Create zones for home, work, school, etc.
- 🗺️ **Clustering**: Group nearby devices for cleaner maps
- 🗺️ **Styling**: Customize colors per person/device

### For Automations
- 🤖 **Conditions**: Add conditions to prevent false alerts
- 🤖 **Delays**: Use delays for multi-step sequences
- 🤖 **Notifications**: Include actionable buttons for quick response
- 🤖 **Templates**: Use templates for dynamic messages

---

## Troubleshooting

### Tile Not Ringing
1. Check HA is within BLE range (~30m)
2. Verify Bluetooth adapter is enabled
3. Try pressing Tile button to wake it
4. Check logs for BLE errors

### Jiobit Commands Failing
1. Verify device has cellular signal
2. Check battery level (>10%)
3. Ensure device_id and circle_id are correct
4. Try legacy API if v6 fails (automatic fallback)

### Map Not Updating
1. Call `life360.update_location` service
2. Check GPS accuracy in device attributes
3. Verify entity is not disabled
4. Check Life360 app shows recent location

---

**Created for ha-life360 v0.7.0+**
**Last Updated**: 2025-11-30

For more information, see:
- [README.md](../README.md)
- [Tiles & Devices Documentation](./tiles-and-devices.md)
- [API Analysis](./tile-api-401-analysis.md)
