# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - Unreleased

### Added

#### Device Tracking
- **Tile Bluetooth tracker support** - Track Tile devices (Mate, Pro, Slim, Sticker) linked to Life360
- **Jiobit pet GPS tracker support** - Track pet/child GPS devices integrated with Life360
- **Device Issues sensor** - Monitor connectivity, battery, and signal issues for Tile/Jiobit devices
- **Jiobit Buzz service** (`life360.buzz_jiobit`) - Send buzz command to locate pets

#### Driving & Trips
- **Driving Statistics sensors** - Weekly metrics per member:
  - Distance driven
  - Number of trips
  - Maximum speed
  - Hard braking events
  - Rapid acceleration events
  - Phone usage while driving
  - Overall driving score
- **Crash Detection sensor** - Monitor if crash detection is enabled
- **Trip History sensors** - View recent trips with start/end times, addresses, distance, and driving behavior

#### Places & Zones
- **Places discovery service** (`life360.sync_places`) - Retrieve Life360 Places and fire events for zone creation
- **Geofence discovery service** (`life360.sync_geofence_zones`) - Retrieve geofence zones and fire events
- **Place Alert binary sensors** - Show place-based alert configurations (arrival/departure)

#### Alerts & Contacts
- **Scheduled Alerts sensors** - View scheduled check-in alerts per member
- **Emergency Contacts service** (`life360.get_emergency_contacts`) - Retrieve emergency contacts from circles

#### Account & Integrations
- **User Profile sensor** - Display Life360 account information
- **Connected Integrations service** (`life360.get_integrations`) - List linked apps/services (Tile, Jiobit, etc.)

### Changed
- Enhanced debug logging with verbosity level support
- Improved error messages for troubleshooting

### Documentation
- Added troubleshooting guide with debug logging instructions
- Added API endpoints documentation (`docs/api_endpoints.md`)
- Added token obtaining guide (`docs/obtaining-tokens.md`)
- Added Tile and devices setup guide (`docs/tiles-and-devices.md`)

## [0.6.0] - 2024-XX-XX

### Changed
- Support Home Assistant 2024.8.3 or newer

## [0.5.4] - 2024-XX-XX

### Fixed
- Use UTC datetimes internally & fix new HA 2024.11 error

## [0.5.3] - 2024-XX-XX

### Fixed
- Delay & more retries for error 403 while fetching Member data

## [0.5.2] - 2024-XX-XX

### Fixed
- Check for possible existence of old workaround in /config/life360

## [0.5.1] - 2024-XX-XX

### Fixed
- Handle error 404 when Member removed from Circles
