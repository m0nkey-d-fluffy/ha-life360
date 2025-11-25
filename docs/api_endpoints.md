# Life360 API Endpoints

This document lists the discovered Life360 API endpoints from mobile app traffic analysis.

## Base URL

```
https://api-cloudfront.life360.com
```

## Authentication

All authenticated endpoints require the `Authorization` header:

```
Authorization: Bearer <access_token>
```

See [Obtaining Tokens](obtaining-tokens.md) for how to get your access token.

## Endpoints

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v3/oauth2/token` | Obtain access token (login) |

### User Information

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v3/users/me` | Get current user profile |
| POST | `/v3/users/devices` | Register/update user's device (returns empty list `[]` for new registrations) |

### Circles

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v3/circles` | List all circles the user belongs to |
| GET | `/v4/circles/{circle_id}/members` | Get all members in a circle |
| GET | `/v3/circles/{circle_id}/member/{member_id}` | Get specific member details |
| POST | `/v3/circles/{circle_id}/members/{member_id}/request` | Request member location update |

### Places & Zones

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v3/circles/{circle_id}/allplaces` | Get all places defined in circle |
| GET | `/v4/circles/{circle_id}/zones/` | Get geofence zones |
| GET | `/v3/circles/{circle_id}/places/alerts` | Get place-based alerts |

### Device Tracking (Tiles & Pet GPS)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v5/circles/devices` | List all linked devices |
| GET | `/v5/circles/devices/locations?providers[]=tile` | Get Tile device locations |
| GET | `/v5/circles/devices/locations?providers[]=jiobit` | Get Jiobit/pet GPS locations |
| GET | `/v5/circles/devices/locations?providers[]=tile&providers[]=jiobit` | Get all device locations |
| GET | `/v5/circles/devices/issues` | Get device issues/errors |
| GET | `/v4/settings/tileDeviceSettings` | Get Tile integration settings |
| GET | `/v6/devices?activationStates=activated,pending,pending_disassociated` | Get device metadata including names (requires `x-device-id` header with valid device ID, returns 401 without) |

### Driver Behavior

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v3/driverbehavior/crashenabledstatus` | Check crash detection status |
| GET | `/v3/drivereport/circle/{circle_id}/user/{user_id}/stats?weekOffset=0` | Get driving statistics |
| GET | `/v3/circles/{circle_id}/users/{user_id}/driverbehavior/trips/{trip_id}` | Get specific trip details |

### Alerts & Notifications

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/circles/{circle_id}/users/{user_id}/scheduled/alerts` | Get scheduled alerts |
| GET | `/v3/circles/{circle_id}/emergencyContacts` | Get emergency contacts |
| GET | `/v3/circles/{circle_id}/members/{member_id}/role` | Get member's role in circle |

### Real-time Updates

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v3/circles/{circle_id}/smartRealTime/start` | Start real-time location updates |

### Integrations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v6/integrations` | List connected integrations |
| GET | `/v6/adornments/circle/{circle_id}` | Get circle adornments/badges |

### Jiobit Commands

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v6/provider/jiobit/devices/{device_id}/circle/{circle_id}/command` | Send command to Jiobit device |

### Analytics (Internal)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/ingest` | Analytics event ingestion |
| POST | `/` | Root endpoint (various uses) |

## Response Format

Most endpoints return JSON. Example member response:

```json
{
  "id": "member-uuid",
  "firstName": "John",
  "lastName": "Doe",
  "avatar": "https://...",
  "location": {
    "latitude": "-33.8688",
    "longitude": "151.2093",
    "accuracy": "50",
    "timestamp": "1705312200",
    "since": "1705310400",
    "isDriving": "0",
    "speed": "0",
    "address1": "123 Main St",
    "address2": "Sydney NSW",
    "name": "Home"
  },
  "features": {
    "shareLocation": "1"
  }
}
```

## Device Locations Response

Example response from `/v5/circles/devices/locations`:

```json
{
  "tile": [
    {
      "id": "2382b0e5fdba138f",
      "name": "Keys",
      "location": {
        "latitude": -33.8688,
        "longitude": 151.2093,
        "timestamp": 1705312200,
        "accuracy": 50
      },
      "battery": {
        "level": 85,
        "status": "NORMAL"
      }
    }
  ],
  "jiobit": [
    {
      "deviceId": "dr123456",
      "deviceName": "Fluffy",
      "lat": -33.8700,
      "lng": 151.2100,
      "lastUpdated": "2024-01-15T14:30:00Z",
      "battery": 72
    }
  ]
}
```

## Rate Limiting

Life360 enforces rate limits on API requests:

- Requests may return HTTP 429 (Too Many Requests)
- The `Retry-After` header indicates when to retry
- Typical limits: ~60 requests per minute per account
- Circle list requests are more heavily rate-limited

## Error Responses

| HTTP Code | Meaning |
|-----------|---------|
| 400 | Bad Request - Invalid parameters |
| 401 | Unauthorized - Invalid or expired token |
| 403 | Forbidden - Access denied or rate limited |
| 404 | Not Found - Resource doesn't exist |
| 429 | Too Many Requests - Rate limit exceeded |
| 500 | Server Error - Life360 server issue |

## Device ID Authentication

Some endpoints (particularly `/v6/devices`) require authentication via the `x-device-id` header:

```
x-device-id: androideDb6Dr3GQuOfOkQqpaiV6t
```

### About Device IDs

- Device IDs are generated by the Life360 mobile app when installed on Android or iOS devices
- Format: `android` or `ios` prefix followed by a 22-character alphanumeric string (mixed case)
- Total length: 29 characters
- Example: `androideDb6Dr3GQuOfOkQqpaiV6t`
- Device IDs are **application-generated**, not OS-generated
- Life360 validates device IDs against their internal database
- Only device IDs from actual Life360 app installations are accepted

### Device Registration

The `/v3/users/devices` POST endpoint accepts device registration requests but:
- Returns HTTP 200 with an empty list `[]` for new device registrations
- Does not return or create valid device IDs in the response
- Life360 only recognizes device IDs from actual mobile app installations

### Obtaining a Device ID

To get a valid device ID:
1. Install Life360 on an Android or iOS device
2. Use network monitoring tools (mitmproxy, Charles Proxy, etc.) to capture API traffic
3. Look for the `x-device-id` header in any API request
4. The device ID is also included in the `ce-source` header format: `/ANDROID/12/device-model/[device_id]`

## Notes

- All timestamps are Unix epoch (seconds since 1970-01-01)
- Coordinates are returned as strings in member responses
- Speed is in an internal unit (multiply by 2.25 for MPH)
- Accuracy is in feet for member locations, meters for devices
- The API is undocumented and may change without notice
