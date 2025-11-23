# Life360 Integration Architecture

This document describes the internal architecture and data flows of the Life360 Home Assistant integration.

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Home Assistant                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │   Device    │  │   Binary    │  │   Sensor    │  │  Services  │ │
│  │  Trackers   │  │   Sensors   │  │  Platform   │  │            │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘ │
│         │                │                │                │        │
│         └────────────────┴────────┬───────┴────────────────┘        │
│                                   │                                  │
│                    ┌──────────────▼──────────────┐                  │
│                    │      Coordinators           │                  │
│                    │  ┌─────────────────────┐    │                  │
│                    │  │ CirclesMembersData  │    │                  │
│                    │  │   Coordinator       │    │                  │
│                    │  └─────────────────────┘    │                  │
│                    │  ┌─────────────────────┐    │                  │
│                    │  │ MemberData          │    │                  │
│                    │  │   Coordinators      │    │                  │
│                    │  └─────────────────────┘    │                  │
│                    │  ┌─────────────────────┐    │                  │
│                    │  │ DeviceData          │    │                  │
│                    │  │   Coordinator       │    │                  │
│                    │  └─────────────────────┘    │                  │
│                    └──────────────┬──────────────┘                  │
│                                   │                                  │
└───────────────────────────────────┼──────────────────────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   Life360 API       │
                         │   (REST/HTTPS)      │
                         └─────────────────────┘
```

## Component Structure

```
custom_components/life360/
├── __init__.py          # Integration setup, services registration
├── coordinator.py       # Data update coordinators
├── helpers.py           # Data classes, API wrapper, storage
├── const.py             # Constants and configuration
├── config_flow.py       # Configuration UI flow
├── device_tracker.py    # Member & device location entities
├── binary_sensor.py     # Account online & place alert entities
├── sensor.py            # Driving stats, trips, alerts entities
├── services.yaml        # Service definitions
└── translations/        # UI translations
```

## Initialization Flow

```
┌────────────────────────────────────────────────────────────────────┐
│                    Integration Startup                              │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│ 1. async_setup() - Register global services                        │
│    • update_location                                                │
│    • sync_places                                                    │
│    • sync_geofence_zones                                           │
│    • get_emergency_contacts                                         │
│    • get_integrations                                               │
│    • buzz_jiobit                                                    │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│ 2. async_setup_entry() - Initialize config entry                   │
│    • Load stored data (Life360Store)                               │
│    • Create CirclesMembersDataUpdateCoordinator                    │
│    • First refresh to get circles & members                        │
│    • Create MemberDataUpdateCoordinator for each member            │
│    • Create DeviceDataUpdateCoordinator for Tiles/Jiobit           │
│    • Store coordinators in entry.runtime_data                      │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│ 3. Platform Setup (async_forward_entry_setups)                     │
│    • device_tracker.async_setup_entry()                            │
│    • binary_sensor.async_setup_entry()                             │
│    • sensor.async_setup_entry()                                    │
└────────────────────────────────────────────────────────────────────┘
```

## Coordinator Architecture

### CirclesMembersDataUpdateCoordinator

The main coordinator that manages Life360 API connections and retrieves circle/member lists.

```
┌─────────────────────────────────────────────────────────────────┐
│          CirclesMembersDataUpdateCoordinator                     │
├─────────────────────────────────────────────────────────────────┤
│ Data: CirclesMembersData                                         │
│   ├── circles: dict[CircleID, CircleData]                       │
│   └── mem_details: dict[MemberID, MemberDetails]                │
├─────────────────────────────────────────────────────────────────┤
│ Responsibilities:                                                │
│   • Manage API sessions per account                             │
│   • Retrieve circles and members list                           │
│   • Handle rate limiting and login errors                       │
│   • Persist data to storage                                     │
│   • Provide API methods for other coordinators/services         │
├─────────────────────────────────────────────────────────────────┤
│ API Methods:                                                     │
│   • get_circle_devices()      • get_driving_stats()             │
│   • get_all_devices()         • get_crash_detection_status()    │
│   • get_circle_places()       • get_emergency_contacts()        │
│   • get_all_places()          • get_trip_history()              │
│   • get_geofence_zones()      • get_scheduled_alerts()          │
│   • get_place_alerts()        • get_user_profile()              │
│   • get_member_role()         • get_integrations()              │
│   • get_device_issues()       • send_jiobit_command()           │
└─────────────────────────────────────────────────────────────────┘
```

### MemberDataUpdateCoordinator

Per-member coordinator for location updates.

```
┌─────────────────────────────────────────────────────────────────┐
│            MemberDataUpdateCoordinator                           │
├─────────────────────────────────────────────────────────────────┤
│ Data: MemberData                                                 │
│   ├── details: MemberDetails (name, avatar)                     │
│   ├── loc: LocationData (lat, lon, accuracy, speed, etc.)       │
│   └── loc_missing: NoLocReason                                  │
├─────────────────────────────────────────────────────────────────┤
│ Update Interval: 5 seconds                                       │
│ Responsibilities:                                                │
│   • Poll member location from API                               │
│   • Handle multiple circles (take best data)                    │
│   • Track location missing reasons                              │
└─────────────────────────────────────────────────────────────────┘
```

### DeviceDataUpdateCoordinator

Coordinator for Tile and Jiobit device tracking.

```
┌─────────────────────────────────────────────────────────────────┐
│            DeviceDataUpdateCoordinator                           │
├─────────────────────────────────────────────────────────────────┤
│ Data: dict[DeviceID, DeviceData]                                │
│   └── DeviceData: device_id, name, type, location, battery      │
├─────────────────────────────────────────────────────────────────┤
│ Update Interval: 5 seconds                                       │
│ Responsibilities:                                                │
│   • Poll Tile/Jiobit locations via /v5/circles/devices/locations│
│   • Parse provider-specific response formats                    │
│   • Signal when devices are added/removed                       │
└─────────────────────────────────────────────────────────────────┘
```

### Sensor-specific Coordinators

Additional coordinators for less frequently updated data:

```
┌─────────────────────────────────────────────────────────────────┐
│ DrivingStatsCoordinator      │ Update: 15 minutes               │
│ CrashDetectionCoordinator    │ Update: 1 hour                   │
│ TripHistoryCoordinator       │ Update: 30 minutes               │
│ ScheduledAlertsCoordinator   │ Update: 1 hour                   │
│ DeviceIssuesCoordinator      │ Update: 1 hour                   │
│ UserProfileCoordinator       │ Update: 24 hours                 │
│ PlaceAlertsCoordinator       │ Update: 1 hour                   │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow: Member Location Update

```
┌──────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│  Timer   │────▶│   Member     │────▶│  Life360    │────▶│  API     │
│ (5 sec)  │     │ Coordinator  │     │  API Call   │     │ Response │
└──────────┘     └──────────────┘     └─────────────┘     └────┬─────┘
                                                               │
     ┌─────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Parse      │────▶│   Update     │────▶│   Entity     │
│  Response    │     │ MemberData   │     │ State Update │
└──────────────┘     └──────────────┘     └──────────────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │ Home Assistant│
                                          │  State Machine│
                                          └──────────────┘
```

## Data Flow: Service Call

Example: `life360.sync_places`

```
┌──────────────┐
│ User calls   │
│ service      │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│ sync_places_to_zones(call: ServiceCall)                      │
│   1. Get config entries                                       │
│   2. For each entry with runtime_data:                       │
│      a. Get coordinator from entry.runtime_data              │
│      b. Call coordinator.get_all_places()                    │
│      c. Collect place data                                   │
│   3. Fire 'life360_places' event                             │
│   4. Return places dict                                       │
└──────────────────────────────────────────────────────────────┘
       │
       ├────────────────────────┐
       │                        │
       ▼                        ▼
┌──────────────┐         ┌──────────────┐
│ Event Bus    │         │ Service      │
│ life360_     │         │ Response     │
│ places       │         │ Data         │
└──────────────┘         └──────────────┘
```

## Entity Hierarchy

```
Life360 Integration
│
├── Per Account
│   └── binary_sensor.life360_online_{account_id}
│
├── Per Member
│   ├── device_tracker.life360_{member_name}
│   ├── sensor.{member_name}_weekly_distance
│   ├── sensor.{member_name}_weekly_trips
│   ├── sensor.{member_name}_max_speed_this_week
│   ├── sensor.{member_name}_hard_brakes_this_week
│   ├── sensor.{member_name}_rapid_accelerations_this_week
│   ├── sensor.{member_name}_phone_usage_while_driving
│   ├── sensor.{member_name}_driving_score
│   ├── sensor.{member_name}_recent_trips
│   └── sensor.{member_name}_scheduled_alerts
│
├── Per Device (Tile/Jiobit)
│   └── device_tracker.life360_{device_name}
│
├── Per Place Alert
│   └── binary_sensor.life360_alert_{member}_at_{place}
│
└── Global
    ├── sensor.life360_crash_detection
    ├── sensor.life360_device_issues
    └── sensor.life360_user_profile
```

## API Endpoints Used

| Endpoint | Coordinator/Method | Update Frequency |
|----------|-------------------|------------------|
| `/v3/circles` | CirclesMembersData | On load/reload |
| `/v4/circles/{cid}/members` | CirclesMembersData | On load/reload |
| `/v3/circles/{cid}/member/{mid}` | MemberData | 5 seconds |
| `/v5/circles/devices/locations` | DeviceData | 5 seconds |
| `/v3/drivereport/.../stats` | DrivingStats | 15 minutes |
| `/v3/driverbehavior/crashenabledstatus` | CrashDetection | 1 hour |
| `/v3/drivereport/.../trips` | TripHistory | 30 minutes |
| `/v1/.../scheduled/alerts` | ScheduledAlerts | 1 hour |
| `/v5/circles/devices/issues` | DeviceIssues | 1 hour |
| `/v3/users/me` | UserProfile | 24 hours |
| `/v3/circles/{cid}/allplaces` | Service call | On demand |
| `/v4/circles/{cid}/zones/` | Service call | On demand |
| `/v3/circles/{cid}/emergencyContacts` | Service call | On demand |
| `/v6/integrations` | Service call | On demand |
| `/v6/provider/jiobit/.../command` | Service call | On demand |

## Error Handling

```
┌─────────────────────────────────────────────────────────────────┐
│                      Error Handling Flow                         │
└─────────────────────────────────────────────────────────────────┘

API Request
    │
    ├── HTTP 200 ──────────────────────▶ Parse & return data
    │
    ├── HTTP 401/403 (LoginError) ─────▶ Retry with backoff
    │   │                                 (up to 30 retries)
    │   └── Too many retries ──────────▶ Disable account
    │                                     Create repair issue
    │
    ├── HTTP 429 (RateLimited) ────────▶ Wait Retry-After + 10s
    │                                     Then retry
    │
    ├── HTTP 404 (NotFound) ───────────▶ Return NOT_FOUND error
    │                                     (member may be removed)
    │
    └── Other errors ──────────────────▶ Log warning
                                          Return NO_DATA
```

## Storage

Data is persisted to `.storage/life360`:

```json
{
  "version": 1,
  "data": {
    "circles": {
      "circle_id": {
        "name": "Family",
        "aids": ["account1@email.com"],
        "mids": ["member_uuid_1", "member_uuid_2"]
      }
    },
    "mem_details": {
      "member_uuid_1": {
        "name": "John Doe",
        "entity_picture": "https://..."
      }
    }
  }
}
```

This allows the integration to create entities immediately on restart without waiting for API responses.
