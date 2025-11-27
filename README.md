# <img src="https://brands.home-assistant.io/life360/icon.png" alt="Life360" width="50" height="50"/> Life360

A [Home Assistant](https://www.home-assistant.io/) integration for Life360.
Creates Device Tracker (`device_tracker`) entities to show where Life360 Members are located.

**New in v0.7.0:**

**Device Tracking:**
- Support for **Tile Bluetooth trackers** and **Jiobit pet GPS trackers**
- **Device Issues sensor** - Monitor Tile/Jiobit device problems
- **Jiobit Buzz service** - Send buzz command to find your pet

**Driving & Trips:**
- **Driving Statistics sensors** - Track weekly distance, trips, max speed, hard brakes, and more
- **Crash Detection sensor** - Monitor crash detection status
- **Trip History sensors** - View recent trips with detailed statistics

**Places & Zones:**
- **Places discovery service** - Retrieve Life360 Places data for creating Home Assistant zones
- **Geofence discovery service** - Retrieve Life360 geofence data for creating Home Assistant zones
- **Place Alert sensors** - Binary sensors showing place alert configurations

**Alerts & Contacts:**
- **Scheduled Alerts sensors** - View scheduled check-in alerts per member
- **Emergency Contacts service** - Retrieve emergency contacts from your circles

**Account & Integrations:**
- **User Profile sensor** - View Life360 account information
- **Connected Integrations service** - View linked apps and services

## Current Changes / Improvements

As of HA 2024.2 the built-in Life360 integration was removed due to the integration effectively being broken and seemingly unrepairable.
It appeared Life360 and/or Cloudflare were actively blocking third party usage of their API.
However, since that time, a better understanding of the (undocumented & unsupported) API has been developed.
This custom integration is now able to use the API again.
It's, of course, yet to be seen if it will continue to work.

### Note on Updating Circles & Members Lists

The current implementation differs from previous versions in the way it retrieves the list of Circles visible to the registered accounts
as well as the list of Members in each of those Circles.
This is due to the fact that the server seems to severly limit when the list of Circles can be retrieved.
It is not uncommon for the server to respond to a request for Circles with an HTTP error 429, too many requests,
or an HTTP error 403, forbidden (aka a login error.)
When this happens the request must be retried after a delay of as much as ten minutes.
It may even need to be retried multiple times before it succeeds.

Therefore, when the integration is loaded (e.g., when the integration is first added, when it is reloaded, or when HA starts)
a WARNING message may be issued stating that the list of Circles & Members could not be retrieved and needs to be retried.
Once the lists of Circles & Members is retrieved successfully, there will be another WARNING message saying so.

Device tracker entities cannot be created until the lists of Circles & Members is known.

Once this process has completed the first time, the lists will be saved in storage (i.e., config/.storage/life360).
When the integration is reloaded or HA is restarted, this stored list will be used so that the tracker entities
can be created and updated normally.
At the same time, the integration will try to update the lists again from the server, so WARNING messages may be seen again.

Due to the above, new Circles or Members will only be seen (and corresponding tracker entities created) when the integration is loaded.
Therefore, if the registered accounts are added to any new Circles, or any Members are added to the known Circles,
the integration will not be aware of those changes until it is loaded.
This will happen at the next restart, or you can force it to happen by reloading the integration.
I.e., go to Settings -> Devices & services -> Life360,
click on the three dots next to "CONFIGURE" and select Reload.
Please be patient since it could take a while due the above reasons before any new tracker entities are created.

## Installation
### Remove `/config/life360` if Present

If you have a folder named `life360` in your configuration folder (typically `/config`), remove it.
This was a workaround from previous versions of the Life360 integration when it wasn't working.
It contained earlier or experimental versions of the life360 pypi.org package.

### Procedure

The integration software must first be installed as a custom component.
You can use HACS to manage the installation and provide update notifications.
Or you can manually install the software.

<details>
<summary>With HACS</summary>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

1. Add this repo as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/).
   It should then appear as a new integration. Click on it. If necessary, search for "life360".

   ```text
   https://github.com/pnbruckner/ha-life360
   ```
   Or use this button:
   
   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pnbruckner&repository=ha-life360&category=integration)


1. Download the integration using the appropriate button.

</details>

<details>
<summary>Manual Installation</summary>

Place a copy of the files from [`custom_components/life360`](custom_components/life360)
in `<config>/custom_components/life360`,
where `<config>` is your Home Assistant configuration directory.

>__NOTE__: When downloading, make sure to use the `Raw` button from each file's page.

</details>

### Post Installation

After it has been downloaded you will need to restart Home Assistant.

## Configuration
### Add Integration Entry

After installation a Life360 integration entry must be added to Home Assistant.
This only needs to be done once.

Use this My Button:

[![add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=life360)

Alternatively, go to Settings -> Devices & services and click the **`+ ADD INTEGRATION`** button.
Find or search for "Life360", click on it, then follow the prompts.

### Configuration Options

#### Device ID (Optional)

**For Tile/Jiobit Device Names** ✨ **Automatic!**

The integration now **automatically displays proper device names** for Tile Bluetooth trackers and Jiobit pet GPS devices!

- **Automatic device names**: Shows actual names from Life360 (e.g., "Keys", "Wallet", "Fluffy")
- **Zero configuration**: Device ID is auto-generated, no manual setup needed
- **Works immediately**: Install via HACS and device names appear automatically

**How it works:**
1. The integration auto-generates a random Android device ID (e.g., `androidK3mP9xQw2Vn4Ry8Lz7Jc5T`)
2. Uses curl_cffi to bypass Cloudflare WAF and fetch device names from Life360's v6 API
3. Automatically maps Life360 device IDs to Tile BLE IDs and decodes authentication keys
4. Caches all device metadata for instant access

**No manual configuration required!** Just install via HACS and restart Home Assistant.

See [Tile & Device Tracker Support](docs/tiles-and-devices.md) for technical details and troubleshooting.

#### GPS Accuracy Radius Limit

Each location update has a GPS accuracy value (see the entity's corresponding attribute.)
You can think of each update as a circle whose center is defined by latitude & longitude,
and whose radius is defined by the accuracy value,
where the actual location of the device is somewhere within that circle.
The _higher_ the accuracy value, the _larger_ the circle where the device may be,
therefore the _less_ accurate the location fix.

This configuration option can be used to reject location updates that are _less_ accurate
(i.e., have _larger_ accuracy values) than the entered value (in meters.)
Or it can be left blank, in which case updates will not be rejected due to their accuracy.
If used, a value of 100m is recommended as a reasonable starting point.
Adjust up or down depending on your desire for more updates, or fewer but more accurate updates.

#### Driving Speed Threshold

The Life360 server indicates when it considers the device is moving at driving speeds.
However, this value does not always seem to be as expected.
This value can be overridden by providing a speed, at which or above, the entity's `driving` attribute should be true.

#### Show Driving as State

If enabled, and the device is determined to be at or above driving speed,
the state of the entity will be set to "Driving", assuming it is not within a Home Assistant Zone.

#### DEBUG Message Verbosity

If the user's profile has "advanced mode" enabled, then this configuration option will appear.
It can be used to adjust how much debug information should be written to the system log,
assuming debug has been enabled for the Life360 integration.

### Life360 Accounts

At least one Life360 account must be entered, although more may be entered if desired.
The integration will look for Life360 Members in all the Circles that can be seen by the entered account(s).

#### Account Authorization Methods

There are currently two methods supported for authorizing the Life360 integration to rerieve data associated with a Life360 account.

##### Username & Password

This method can be used with any Life360 account that has not had a phone number "verified."
Once a phone number has been verified, the Life360 server will no longer allow this authorization method.

Enter the Life360 account's email address & password.

##### Access Type & Token

This method is effectively a work around for accounts that have had a phone number "verified."
In theory, there is a way to "login" to the Life360 server using a phone number and a code sent via SMS.
However, I have not been able to get that to work.

Go to https://life360.com/login.
Open the browser's Developer Tools sidebar & go to the Network tab.
Make sure recording is enabled.
Log into Life360.
When the process has been completed look for the "token" packet.
(If there is one labeled "preflight", uses the OPTIONS method, or has no preview/response data,
ignore it and look for another "token" packet which uses the POST method and has data.)
Under the Preview or Response tab, look for `token_type` & `access_token`.
Copy those values into the corresponding boxes (access type & access token) on the HA account page.
(Note that the `token_type` is almost certainly "Bearer".)
You can put whatever you want in the "Account identifier" box.

## Versions

This custom integration supports HomeAssistant versions 2024.8.3 or newer.

## Tile & Pet GPS Tracker Support

This integration now supports **Tile Bluetooth trackers** and **Jiobit pet GPS trackers** that are linked to your Life360 account.

### Supported Devices
- **Tile trackers** - All Tile models (Mate, Pro, Slim, Sticker, etc.)
- **Jiobit pet GPS** - Pet and child GPS trackers integrated with Life360

### How It Works
When you link a Tile or Jiobit device to your Life360 account through the Life360 mobile app, this integration will automatically discover and create `device_tracker` entities for each device.

### Device Tracker Attributes
Each device tracker entity includes:
- `latitude` / `longitude` - Last known location
- `gps_accuracy` - Location accuracy in meters (when available)
- `battery_level` - Battery percentage (when available)
- `battery_status` - Battery status (e.g., "LOW", "NORMAL")
- `device_type` - "Tile" or "Pet GPS"
- `device_id` - Unique device identifier
- `last_seen` - When the device was last seen

### Linking Devices
To add Tile or Jiobit devices:
1. Open the **Life360 mobile app**
2. Go to **Settings** → **Connected Apps** or **Devices**
3. Link your Tile account or add your Jiobit device
4. Reload the Life360 integration in Home Assistant

> **Note:** Device locations are updated based on the Life360 server data, which depends on how recently the device has been seen by the Tile network or the Jiobit cellular connection.

## Driving Statistics & Crash Detection

This integration creates sensor entities for driving behavior data:

### Driving Statistics Sensors (per member)
- **Weekly Distance** - Total miles driven this week
- **Weekly Trips** - Number of trips this week
- **Max Speed** - Highest speed recorded this week
- **Hard Brakes** - Number of hard braking events
- **Rapid Accelerations** - Number of rapid acceleration events
- **Phone Usage While Driving** - Minutes of phone usage while driving
- **Driving Score** - Overall driving safety score (0-100)

### Crash Detection Sensor
- **Life360 Crash Detection** - Shows if crash detection is enabled/disabled

> **Note:** Driving statistics are updated every 15 minutes. Crash detection status is checked hourly.

## Services

### `life360.update_location`

Can be used to request a location update for one or more Members.
Once this service is called, the Member's location will typically be updated every five seconds for about one minute.
The service takes one parameters, `entity_id`, which can be a single entity ID, a list of entity ID's, or the word "all" (which means all Life360 trackers.)
The use of the `target` parameter should also work.

### `life360.sync_places`

Retrieves all Life360 Places and fires a `life360_places` event with the data. Use this to discover your Life360 places and their coordinates for creating Home Assistant zones.

The service returns data including:
- Place name, latitude, longitude, and radius
- Place ID for reference

Example automation to log places:
```yaml
automation:
  - alias: "Log Life360 Places"
    trigger:
      - platform: event
        event_type: life360_places
    action:
      - service: notify.persistent_notification
        data:
          title: "Life360 Places"
          message: "{{ trigger.event.data.places | length }} places found"
```

### `life360.sync_geofence_zones`

Retrieves all Life360 geofence zones and fires a `life360_geofences` event with the data. Use this to discover geofence zones configured for arrival/departure alerts.

The service returns data including:
- Zone name, latitude, longitude, and radius
- Zone type and active status
- Associated circle name

Example automation to log geofences:
```yaml
automation:
  - alias: "Log Life360 Geofences"
    trigger:
      - platform: event
        event_type: life360_geofences
    action:
      - service: notify.persistent_notification
        data:
          title: "Life360 Geofences"
          message: "{{ trigger.event.data.zones | length }} geofences found"
```

### `life360.get_emergency_contacts`

Retrieves emergency contacts from all Life360 circles. The contacts are returned as service response data and also fired as a `life360_emergency_contacts` event.

```yaml
automation:
  - alias: "Check Emergency Contacts Weekly"
    trigger:
      - platform: time
        at: "00:00:00"
        weekday: "sun"
    action:
      - service: life360.get_emergency_contacts
```

The event data includes:
```yaml
contacts:
  - circle: "Family Circle"
    name: "Emergency Contact Name"
    phone: "+1234567890"
    relationship: "Parent"
```

## Trip History

This integration provides trip history sensors for each member. These sensors show:
- **Recent Trips** - Number of recent trips
- Detailed attributes including:
  - Start/end times
  - Start/end addresses
  - Distance traveled
  - Duration
  - Max speed
  - Hard brakes and rapid accelerations

> **Note:** Trip history is updated every 30 minutes to reduce API calls.

## Place Alerts

Binary sensors are created for each place alert configured in your Life360 circles. These show:
- Alert enabled/disabled status
- Place and member details
- Alert type (arrival, departure, or both)

These can be used in automations to monitor when Life360 alert configurations change.

## Scheduled Alerts

Sensors showing scheduled check-in alerts configured for each member. Attributes include:
- Alert time and days
- Enabled status
- Last check-in timestamp

## Device Issues

A sensor that monitors issues with your Tile and Jiobit devices:
- Device connectivity problems
- Low battery warnings
- Signal issues

## User Profile

Shows your Life360 account information:
- Name and email
- Account creation date
- Avatar URL

### `life360.get_integrations`

Retrieves all connected integrations/apps from your Life360 account. Returns information about linked services like Tile, Jiobit, etc.

### `life360.buzz_jiobit`

Sends a buzz command to a Jiobit pet GPS tracker to help locate your pet. The device will emit a sound.

```yaml
service: life360.buzz_jiobit
data:
  device_id: "dr123456"
  circle_id: "abc123-def456"
```

> **Note:** The device_id and circle_id can be found in the device tracker entity attributes.

## Troubleshooting

### Enable Debug Logging

To troubleshoot issues, enable debug logging for the Life360 integration by adding this to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.life360: debug
```

After adding this, restart Home Assistant and check the logs in **Settings > System > Logs**.

### Verbosity Levels

The integration has a **DEBUG message verbosity** setting (found in the integration options if you have "advanced mode" enabled in your user profile). The levels are:

| Level | Description |
|-------|-------------|
| 0 | Redacted request errors only |
| 1 | Above plus redacted response headers |
| 2 | Above plus redacted response data (recommended for troubleshooting) |
| 3 | Above but only sensitive data is redacted |
| 4 | No redactions (includes sensitive data - use with caution) |

### Common Issues

**"No Life360 integration configured"**
- Ensure you have added and configured the Life360 integration in Settings > Devices & services

**Device trackers not appearing**
- Check the logs for rate limiting (HTTP 429) or authentication errors (HTTP 401/403)
- The integration may take several minutes to retrieve the initial circle/member list due to Life360 server restrictions

**Tile/Jiobit devices not showing**
- Ensure the devices are linked to your Life360 account in the Life360 mobile app
- Check debug logs for device location API responses

**Services not working**
- Enable debug logging and check for error messages when calling the service
- Verify the integration has finished its initial setup (check for WARNING messages in logs)
