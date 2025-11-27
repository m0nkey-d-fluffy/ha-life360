"""Life360 integration."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from typing import cast

try:
    from life360 import NotFound  # noqa: F401
except ImportError as err:
    raise ImportError(
        "If /config/life360 exists, remove it, restart Home Assistant, and try again"
    ) from err

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_ENTITY_ID, ENTITY_MATCH_ALL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    SERVICE_BUZZ_JIOBIT,
    SERVICE_DIAGNOSE_TILE_BLE,
    SERVICE_DIAGNOSE_RING_ALL_BLE,
    SERVICE_GET_DEVICES,
    SERVICE_GET_EMERGENCY_CONTACTS,
    SERVICE_GET_INTEGRATIONS,
    SERVICE_RING_DEVICE,
    SERVICE_STOP_RING_DEVICE,
    SERVICE_SYNC_GEOFENCE_ZONES,
    SERVICE_SYNC_PLACES,
    SERVICE_TOGGLE_LIGHT,
    SERVICE_UPDATE_LOCATION,
    SIGNAL_MEMBERS_CHANGED,
    SIGNAL_UPDATE_LOCATION,
)
from .coordinator import (
    CirclesMembersDataUpdateCoordinator,
    DeviceDataUpdateCoordinator,
    L360ConfigEntry,
    L360Coordinators,
    MemberDataUpdateCoordinator,
)
from .helpers import Life360Store, MemberID

# Needed only if setup or async_setup exists.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)
_PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]

_UPDATE_LOCATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): vol.Any(
            vol.All(vol.Lower, ENTITY_MATCH_ALL), cv.entity_ids
        )
    }
)


async def async_setup(hass: HomeAssistant, _: ConfigType) -> bool:
    """Set up integration."""

    @callback
    def update_location(call: ServiceCall) -> None:
        """Request Member location update."""
        async_dispatcher_send(hass, SIGNAL_UPDATE_LOCATION, call.data[CONF_ENTITY_ID])

    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE_LOCATION, update_location, _UPDATE_LOCATION_SCHEMA
    )

    async def sync_places_to_zones(call: ServiceCall) -> dict:
        """Sync Life360 places to Home Assistant zones."""
        _LOGGER.debug("Service %s called", SERVICE_SYNC_PLACES)

        # Get all config entries for Life360
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return {"places": []}

        all_places = []
        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                _LOGGER.debug("Entry %s has no runtime_data, skipping", entry.entry_id)
                continue

            coordinator = entry.runtime_data.coordinator
            _LOGGER.debug("Fetching places from coordinator")
            places = await coordinator.get_all_places()
            _LOGGER.debug("Retrieved %d places from API", len(places))

            for place_id, place in places.items():
                place_data = {
                    "name": place.name,
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                    "radius": place.radius,
                    "place_id": place.place_id,
                }
                all_places.append(place_data)
                _LOGGER.debug(
                    "Life360 Place: %s at (%s, %s) radius %sm",
                    place.name,
                    place.latitude,
                    place.longitude,
                    place.radius,
                )

        # Fire an event with place data for automations to use
        hass.bus.async_fire(
            f"{DOMAIN}_places",
            {"places": all_places},
        )
        _LOGGER.info(
            "Service %s completed: Found %d Life360 places",
            SERVICE_SYNC_PLACES,
            len(all_places),
        )
        return {"places": all_places}

    hass.services.async_register(DOMAIN, SERVICE_SYNC_PLACES, sync_places_to_zones)

    async def sync_geofence_zones(call: ServiceCall) -> dict:
        """Sync Life360 geofence zones to Home Assistant zones."""
        _LOGGER.debug("Service %s called", SERVICE_SYNC_GEOFENCE_ZONES)

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return {"zones": []}

        all_geofences = []
        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                _LOGGER.debug("Entry %s has no runtime_data, skipping", entry.entry_id)
                continue

            coordinator = entry.runtime_data.coordinator
            _LOGGER.debug("Fetching geofence zones from coordinator")
            all_zones = await coordinator.get_all_geofence_zones()
            _LOGGER.debug("Retrieved geofences from %d circles", len(all_zones))

            for cid, zones in all_zones.items():
                circle_data = coordinator.data.circles.get(cid)
                circle_name = circle_data.name if circle_data else str(cid)
                _LOGGER.debug("Processing %d zones from circle %s", len(zones), circle_name)

                for zone in zones:
                    zone_data = {
                        "name": zone.name,
                        "latitude": zone.latitude,
                        "longitude": zone.longitude,
                        "radius": zone.radius,
                        "zone_id": zone.zone_id,
                        "zone_type": zone.zone_type,
                        "circle": circle_name,
                        "active": zone.active,
                    }
                    all_geofences.append(zone_data)
                    _LOGGER.debug(
                        "Life360 Geofence: %s at (%s, %s) radius %sm in %s",
                        zone.name,
                        zone.latitude,
                        zone.longitude,
                        zone.radius,
                        circle_name,
                    )

        # Fire an event with geofence data for automations to use
        hass.bus.async_fire(
            f"{DOMAIN}_geofences",
            {"zones": all_geofences},
        )
        _LOGGER.info(
            "Service %s completed: Found %d Life360 geofences",
            SERVICE_SYNC_GEOFENCE_ZONES,
            len(all_geofences),
        )
        return {"zones": all_geofences}

    hass.services.async_register(DOMAIN, SERVICE_SYNC_GEOFENCE_ZONES, sync_geofence_zones)

    async def get_emergency_contacts(call: ServiceCall) -> dict:
        """Get emergency contacts from all Life360 circles."""
        _LOGGER.debug("Service %s called", SERVICE_GET_EMERGENCY_CONTACTS)

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return {"contacts": []}

        all_contacts = []
        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                _LOGGER.debug("Entry %s has no runtime_data, skipping", entry.entry_id)
                continue

            coordinator = entry.runtime_data.coordinator
            _LOGGER.debug("Fetching emergency contacts from coordinator")
            contacts_by_circle = await coordinator.get_all_emergency_contacts()
            _LOGGER.debug("Retrieved contacts from %d circles", len(contacts_by_circle))

            for cid, contacts in contacts_by_circle.items():
                circle_data = coordinator.data.circles.get(cid)
                circle_name = circle_data.name if circle_data else str(cid)
                _LOGGER.debug("Circle %s has %d emergency contacts", circle_name, len(contacts))

                for contact in contacts:
                    all_contacts.append({
                        "circle": circle_name,
                        "name": contact.name,
                        "phone": contact.phone,
                        "relationship": contact.relationship,
                    })

        # Fire an event with the contacts data
        hass.bus.async_fire(
            f"{DOMAIN}_emergency_contacts",
            {"contacts": all_contacts},
        )
        _LOGGER.info("Service %s completed: Retrieved %d emergency contacts", SERVICE_GET_EMERGENCY_CONTACTS, len(all_contacts))
        return {"contacts": all_contacts}

    hass.services.async_register(
        DOMAIN, SERVICE_GET_EMERGENCY_CONTACTS, get_emergency_contacts
    )

    async def get_integrations(call: ServiceCall) -> dict:
        """Get connected integrations/apps."""
        _LOGGER.debug("Service %s called", SERVICE_GET_INTEGRATIONS)

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return {"integrations": []}

        all_integrations = []
        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                _LOGGER.debug("Entry %s has no runtime_data, skipping", entry.entry_id)
                continue

            coordinator = entry.runtime_data.coordinator
            _LOGGER.debug("Fetching connected integrations from coordinator")
            integrations = await coordinator.get_integrations()
            _LOGGER.debug("Retrieved %d integrations", len(integrations))

            for integration in integrations:
                _LOGGER.debug(
                    "Integration: %s (%s) - connected=%s",
                    integration.name,
                    integration.provider,
                    integration.connected,
                )
                all_integrations.append({
                    "id": integration.integration_id,
                    "name": integration.name,
                    "provider": integration.provider,
                    "connected": integration.connected,
                    "status": integration.status,
                })

        hass.bus.async_fire(
            f"{DOMAIN}_integrations",
            {"integrations": all_integrations},
        )
        _LOGGER.info("Service %s completed: Retrieved %d connected integrations", SERVICE_GET_INTEGRATIONS, len(all_integrations))
        return {"integrations": all_integrations}

    hass.services.async_register(DOMAIN, SERVICE_GET_INTEGRATIONS, get_integrations)

    async def get_devices(call: ServiceCall) -> dict:
        """Get all Tile/Jiobit devices with their IDs for easy reference."""
        _LOGGER.debug("Service %s called", SERVICE_GET_DEVICES)

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return {"devices": [], "circles": []}

        all_devices = []
        all_circles = {}

        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                _LOGGER.debug("Entry %s has no runtime_data, skipping", entry.entry_id)
                continue

            coordinator = entry.runtime_data.coordinator
            _LOGGER.debug("Fetching devices from coordinator")

            # Get circles
            circles_data = coordinator.data.circles
            for cid, circle_data in circles_data.items():
                circle_name = circle_data.name if hasattr(circle_data, 'name') else str(cid)
                if cid not in all_circles:
                    all_circles[str(cid)] = circle_name

            # Trigger metadata fetch to populate device caches
            for cid in circles_data.keys():
                from .helpers import CircleID
                await coordinator._fetch_device_metadata(CircleID(cid))

            # Get devices from the caches
            device_names = coordinator._device_name_cache
            device_categories = coordinator._device_category_cache
            tile_ble_ids = coordinator._tile_ble_id_cache
            tile_auth_keys = coordinator._tile_auth_cache

            _LOGGER.debug("Found %d devices in name cache", len(device_names))

            for device_id, device_name in device_names.items():
                category = device_categories.get(device_id, "unknown")
                has_ble = device_id in tile_ble_ids
                has_auth = device_id in tile_auth_keys
                ble_id = tile_ble_ids.get(device_id, "")

                device_info = {
                    "id": device_id,
                    "name": device_name,
                    "category": category,
                    "ble_capable": has_ble and has_auth,
                }

                if ble_id:
                    device_info["ble_id"] = ble_id

                all_devices.append(device_info)

        # Convert circles dict to list
        circles_list = [
            {"id": cid, "name": name}
            for cid, name in all_circles.items()
        ]

        result = {
            "devices": all_devices,
            "circles": circles_list,
        }

        # Fire an event with the device data
        hass.bus.async_fire(
            f"{DOMAIN}_devices",
            result,
        )

        _LOGGER.info(
            "Service %s completed: Retrieved %d devices and %d circles",
            SERVICE_GET_DEVICES,
            len(all_devices),
            len(circles_list),
        )
        return result

    hass.services.async_register(DOMAIN, SERVICE_GET_DEVICES, get_devices)

    async def diagnose_tile_ble(call: ServiceCall) -> dict:
        """Diagnostic service to verify Tile MAC address mappings.

        Scans for all Tile devices, connects to each one, and reads their
        actual device IDs from GATT characteristics to verify the MAC derivation formula.
        """
        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("🔍 Service %s called - starting Tile BLE diagnostics", SERVICE_DIAGNOSE_TILE_BLE)
        _LOGGER.warning("═══════════════════════════════════════════════════════════")

        try:
            from .tile_ble import discover_and_verify_tile_macs

            # Run the diagnostic scan with HA Bluetooth backend
            mac_to_id_map = await discover_and_verify_tile_macs(scan_timeout=15.0, hass=hass)

            result = {
                "tiles_found": len(mac_to_id_map),
                "mappings": [
                    {"mac_address": mac, "device_id": device_id}
                    for mac, device_id in mac_to_id_map.items()
                ],
            }

            # Fire an event with the results
            hass.bus.async_fire(
                f"{DOMAIN}_tile_ble_diagnostic",
                result,
            )

            _LOGGER.warning("═══════════════════════════════════════════════════════════")
            _LOGGER.warning("✅ Service %s completed: Found %d Tile(s)", SERVICE_DIAGNOSE_TILE_BLE, len(mac_to_id_map))
            _LOGGER.warning("═══════════════════════════════════════════════════════════")

            return result

        except Exception as err:
            _LOGGER.error("❌ Tile BLE diagnostic failed: %s", err, exc_info=True)
            return {"tiles_found": 0, "mappings": [], "error": str(err)}

    hass.services.async_register(DOMAIN, SERVICE_DIAGNOSE_TILE_BLE, diagnose_tile_ble)

    async def diagnose_ring_all_ble(call: ServiceCall) -> dict:
        """Brute-force diagnostic to ring ALL BLE devices and find Tiles.

        Attempts to connect to every BLE device and try Tile authentication/ring.
        """
        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("🔥 Service %s called - will try ALL BLE devices!", SERVICE_DIAGNOSE_RING_ALL_BLE)
        _LOGGER.warning("═══════════════════════════════════════════════════════════")

        try:
            from .tile_ble import diagnose_ring_all_ble_devices

            # Get Tile auth keys from all entries
            auth_keys = {}
            entries = hass.config_entries.async_entries(DOMAIN)
            _LOGGER.warning("🔍 Checking %d config entries for Tile auth keys", len(entries))

            for entry in entries:
                _LOGGER.warning("   Entry: %s", entry.entry_id)
                if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                    _LOGGER.warning("      ❌ No runtime_data")
                    continue

                coordinator = entry.runtime_data.coordinator
                _LOGGER.warning("      ✅ Has coordinator")

                # Check what attributes the coordinator has
                if hasattr(coordinator, "_tile_auth_cache"):
                    _LOGGER.warning("      ✅ Has _tile_auth_cache with %d items", len(coordinator._tile_auth_cache))
                    for tile_id, auth_key in coordinator._tile_auth_cache.items():
                        try:
                            # Auth key might already be bytes or could be a hex string
                            if isinstance(auth_key, bytes):
                                auth_keys[tile_id] = auth_key
                                _LOGGER.warning("         🔑 Found auth key for Tile: %s (already bytes, %d bytes)", tile_id, len(auth_key))
                            else:
                                auth_keys[tile_id] = bytes.fromhex(auth_key)
                                _LOGGER.warning("         🔑 Found auth key for Tile: %s (converted from hex)", tile_id)
                        except Exception as e:
                            _LOGGER.warning("         ❌ Failed to parse auth key for %s: %s", tile_id, e)
                else:
                    _LOGGER.warning("      ❌ No _tile_auth_cache attribute")
                    # List all attributes that start with _tile
                    tile_attrs = [attr for attr in dir(coordinator) if attr.startswith('_tile')]
                    _LOGGER.warning("      Available _tile* attributes: %s", tile_attrs)

            if not auth_keys:
                _LOGGER.error("❌ No Tile auth keys found - make sure Tiles are configured")
                return {"devices_tested": 0, "tiles_found": 0, "error": "No auth keys"}

            # Run the brute-force test
            results = await diagnose_ring_all_ble_devices(hass, auth_keys)

            tiles_found = sum(1 for r in results.values() if "SUCCESS" in r)

            result = {
                "devices_tested": len(results),
                "tiles_found": tiles_found,
                "results": [
                    {"mac": mac, "result": res}
                    for mac, res in results.items()
                ],
            }

            _LOGGER.warning("═══════════════════════════════════════════════════════════")
            _LOGGER.warning("✅ Service %s completed: Found %d Tile(s) out of %d devices",
                          SERVICE_DIAGNOSE_RING_ALL_BLE, tiles_found, len(results))
            _LOGGER.warning("═══════════════════════════════════════════════════════════")

            return result

        except Exception as err:
            _LOGGER.error("❌ Ring-all diagnostic failed: %s", err, exc_info=True)
            return {"devices_tested": 0, "tiles_found": 0, "error": str(err)}

    hass.services.async_register(DOMAIN, SERVICE_DIAGNOSE_RING_ALL_BLE, diagnose_ring_all_ble)

    async def diagnose_raw_scan(call: ServiceCall) -> dict:
        """Direct BLE scan bypassing HA's Bluetooth backend to see raw advertisement data."""
        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("🔬 Service diagnose_raw_ble_scan called - direct BLE scan")
        _LOGGER.warning("═══════════════════════════════════════════════════════════")

        try:
            from .tile_ble import diagnose_raw_ble_scan

            scan_timeout = call.data.get("scan_timeout", 30.0)
            results = await diagnose_raw_ble_scan(scan_timeout=scan_timeout)

            return results

        except Exception as err:
            _LOGGER.error("❌ Raw BLE scan failed: %s", err, exc_info=True)
            return {"total_devices": 0, "tiles_found": 0, "error": str(err)}

    hass.services.async_register(DOMAIN, "diagnose_raw_ble_scan", diagnose_raw_scan)

    async def diagnose_ring_by_mac(call: ServiceCall) -> dict:
        """Test ringing a specific Tile by MAC address."""
        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("🔔 Service diagnose_ring_tile_by_mac called")
        _LOGGER.warning("═══════════════════════════════════════════════════════════")

        try:
            from .tile_ble import diagnose_ring_tile_by_mac

            mac_address = call.data.get("mac_address")
            tile_id = call.data.get("tile_id", "unknown")

            if not mac_address:
                _LOGGER.error("❌ mac_address is required")
                return {"success": False, "error": "mac_address required"}

            # Get auth key for this tile_id from coordinator
            auth_key = None
            entries = hass.config_entries.async_entries(DOMAIN)
            for entry in entries:
                coordinator = entry.runtime_data.coordinator
                if hasattr(coordinator, "_tile_auth_cache"):
                    if tile_id in coordinator._tile_auth_cache:
                        auth_key = coordinator._tile_auth_cache[tile_id]
                        if not isinstance(auth_key, bytes):
                            auth_key = bytes.fromhex(auth_key)
                        break

            if not auth_key:
                _LOGGER.error("❌ No auth key found for Tile ID: %s", tile_id)
                return {"success": False, "error": f"No auth key for {tile_id}"}

            results = await diagnose_ring_tile_by_mac(mac_address, tile_id, auth_key)
            return results

        except Exception as err:
            _LOGGER.error("❌ Ring test failed: %s", err, exc_info=True)
            return {"success": False, "error": str(err)}

    hass.services.async_register(DOMAIN, "diagnose_ring_tile_by_mac", diagnose_ring_by_mac)

    def _get_device_info_from_entity(entity_id: str) -> tuple[str | None, str | None, str | None]:
        """Extract device_id, circle_id, and provider from entity_id.

        Returns:
            Tuple of (device_id, circle_id, provider) or (None, None, None) if not found
        """
        # Get entity state
        state = hass.states.get(entity_id)
        if not state:
            _LOGGER.error("Entity %s not found", entity_id)
            return None, None, None

        # Get device_id from attributes
        device_id = state.attributes.get("device_id")
        if not device_id:
            _LOGGER.error("Entity %s does not have a device_id attribute", entity_id)
            return None, None, None

        # Determine provider from device_type attribute
        device_type = state.attributes.get("device_type", "").lower()
        if "tile" in device_type:
            provider = "tile"
        elif "jiobit" in device_type or "pet" in device_type:
            provider = "jiobit"
        else:
            provider = "jiobit"  # default

        # Find circle_id from coordinators
        # Devices are shared across circles, so we just need ANY valid circle
        entries = hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                continue

            # Get the main coordinator which has circle data
            main_coord = entry.runtime_data.coordinator
            if main_coord and main_coord.data and main_coord.data.circles:
                # Return the first circle - devices work with any circle they're in
                for circle_id in main_coord.data.circles.keys():
                    _LOGGER.debug(
                        "Found circle %s for device %s (provider: %s)",
                        circle_id, device_id, provider
                    )
                    return device_id, str(circle_id), provider

        _LOGGER.error("Could not find any circles for device %s", device_id)
        return device_id, None, provider

    _BUZZ_JIOBIT_SCHEMA = vol.Schema({
        vol.Exclusive("entity_id", "device_selector"): cv.entity_id,
        vol.Exclusive("device_id", "device_selector"): cv.string,
        vol.Optional("circle_id"): cv.string,
    })

    async def buzz_jiobit(call: ServiceCall) -> None:
        """Send buzz command to a Jiobit device to help find pet."""
        # Support both entity_id and device_id + circle_id
        entity_id = call.data.get("entity_id")

        if entity_id:
            device_id, circle_id, _ = _get_device_info_from_entity(entity_id)
            if not device_id or not circle_id:
                _LOGGER.error("Failed to extract device info from entity %s", entity_id)
                return
        else:
            device_id = call.data.get("device_id")
            circle_id = call.data.get("circle_id")

            if not device_id or not circle_id:
                _LOGGER.error("Either entity_id or both device_id and circle_id must be provided")
                return

        _LOGGER.debug(
            "Service %s called: device_id=%s, circle_id=%s",
            SERVICE_BUZZ_JIOBIT,
            device_id,
            circle_id,
        )

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return

        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                _LOGGER.debug("Entry %s has no runtime_data, skipping", entry.entry_id)
                continue

            coordinator = entry.runtime_data.coordinator
            _LOGGER.debug("Sending buzz command to Jiobit device %s", device_id)
            from .helpers import CircleID
            success = await coordinator.send_jiobit_command(
                device_id, CircleID(circle_id), "buzz"
            )
            if success:
                _LOGGER.info("Service %s completed: Buzz command sent to Jiobit device %s", SERVICE_BUZZ_JIOBIT, device_id)
                return
            else:
                _LOGGER.debug("Buzz command failed for device %s via this coordinator", device_id)

        _LOGGER.warning("Service %s failed: Could not send buzz command to Jiobit device %s", SERVICE_BUZZ_JIOBIT, device_id)

    hass.services.async_register(
        DOMAIN, SERVICE_BUZZ_JIOBIT, buzz_jiobit, _BUZZ_JIOBIT_SCHEMA
    )

    # Ring device service (Tile or Jiobit)
    _RING_DEVICE_SCHEMA = vol.Schema({
        vol.Exclusive("entity_id", "device_selector"): cv.entity_id,
        vol.Exclusive("device_id", "device_selector"): cv.string,
        vol.Optional("circle_id"): cv.string,
        vol.Optional("provider"): vol.In(["jiobit", "tile"]),
        vol.Optional("duration", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
        vol.Optional("strength", default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
    })

    async def ring_device(call: ServiceCall) -> None:
        """Ring/buzz a device to help locate it."""
        # Support both entity_id and device_id + circle_id
        entity_id = call.data.get("entity_id")

        if entity_id:
            device_id, circle_id, provider_from_entity = _get_device_info_from_entity(entity_id)
            if not device_id or not circle_id:
                _LOGGER.error("Failed to extract device info from entity %s", entity_id)
                return
            # Use provider from entity unless explicitly overridden
            provider = call.data.get("provider", provider_from_entity or "jiobit")
        else:
            device_id = call.data.get("device_id")
            circle_id = call.data.get("circle_id")
            provider = call.data.get("provider", "jiobit")

            if not device_id or not circle_id:
                _LOGGER.error("Either entity_id or both device_id and circle_id must be provided")
                return

        duration = call.data.get("duration", 30)
        strength = call.data.get("strength", 2)

        _LOGGER.debug(
            "Service %s called: device_id=%s, circle_id=%s, provider=%s",
            SERVICE_RING_DEVICE,
            device_id,
            circle_id,
            provider,
        )

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return

        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                continue

            coordinator = entry.runtime_data.coordinator
            from .helpers import CircleID
            success = await coordinator.ring_device(
                device_id, CircleID(circle_id), provider, duration, strength
            )
            if success:
                _LOGGER.info(
                    "Service %s completed: Ring command sent to %s device %s",
                    SERVICE_RING_DEVICE, provider, device_id
                )
                return

        _LOGGER.warning(
            "Service %s failed: Could not ring %s device %s",
            SERVICE_RING_DEVICE, provider, device_id
        )

    hass.services.async_register(
        DOMAIN, SERVICE_RING_DEVICE, ring_device, _RING_DEVICE_SCHEMA
    )

    # Stop ring device service
    _STOP_RING_DEVICE_SCHEMA = vol.Schema({
        vol.Exclusive("entity_id", "device_selector"): cv.entity_id,
        vol.Exclusive("device_id", "device_selector"): cv.string,
        vol.Optional("circle_id"): cv.string,
        vol.Optional("provider"): vol.In(["jiobit", "tile"]),
    })

    async def stop_ring_device(call: ServiceCall) -> None:
        """Stop ringing/buzzing a device."""
        # Support both entity_id and device_id + circle_id
        entity_id = call.data.get("entity_id")

        if entity_id:
            device_id, circle_id, provider_from_entity = _get_device_info_from_entity(entity_id)
            if not device_id or not circle_id:
                _LOGGER.error("Failed to extract device info from entity %s", entity_id)
                return
            provider = call.data.get("provider", provider_from_entity or "jiobit")
        else:
            device_id = call.data.get("device_id")
            circle_id = call.data.get("circle_id")
            provider = call.data.get("provider", "jiobit")

            if not device_id or not circle_id:
                _LOGGER.error("Either entity_id or both device_id and circle_id must be provided")
                return

        _LOGGER.debug(
            "Service %s called: device_id=%s, circle_id=%s, provider=%s",
            SERVICE_STOP_RING_DEVICE,
            device_id,
            circle_id,
            provider,
        )

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return

        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                continue

            coordinator = entry.runtime_data.coordinator
            from .helpers import CircleID
            success = await coordinator.stop_ring_device(
                device_id, CircleID(circle_id), provider
            )
            if success:
                _LOGGER.info(
                    "Service %s completed: Stop ring command sent to %s device %s",
                    SERVICE_STOP_RING_DEVICE, provider, device_id
                )
                return

        _LOGGER.warning(
            "Service %s failed: Could not stop ringing %s device %s",
            SERVICE_STOP_RING_DEVICE, provider, device_id
        )

    hass.services.async_register(
        DOMAIN, SERVICE_STOP_RING_DEVICE, stop_ring_device, _STOP_RING_DEVICE_SCHEMA
    )

    # Toggle light service (Jiobit only)
    _TOGGLE_LIGHT_SCHEMA = vol.Schema({
        vol.Required("device_id"): cv.string,
        vol.Required("circle_id"): cv.string,
        vol.Optional("provider", default="jiobit"): cv.string,
        vol.Optional("enable", default=True): cv.boolean,
    })

    async def toggle_light(call: ServiceCall) -> None:
        """Toggle the light on a device."""
        device_id = call.data["device_id"]
        circle_id = call.data["circle_id"]
        provider = call.data.get("provider", "jiobit")
        enable = call.data.get("enable", True)

        _LOGGER.debug(
            "Service %s called: device_id=%s, circle_id=%s, enable=%s",
            SERVICE_TOGGLE_LIGHT,
            device_id,
            circle_id,
            enable,
        )

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.warning("No Life360 integration configured")
            return

        for entry in entries:
            if not hasattr(entry, "runtime_data") or not entry.runtime_data:
                continue

            coordinator = entry.runtime_data.coordinator
            from .helpers import CircleID
            success = await coordinator.toggle_device_light(
                device_id, CircleID(circle_id), provider, enable
            )
            if success:
                _LOGGER.info(
                    "Service %s completed: Light %s on %s device %s",
                    SERVICE_TOGGLE_LIGHT,
                    "enabled" if enable else "disabled",
                    provider,
                    device_id
                )
                return

        _LOGGER.warning(
            "Service %s failed: Could not toggle light on %s device %s",
            SERVICE_TOGGLE_LIGHT, provider, device_id
        )

    hass.services.async_register(
        DOMAIN, SERVICE_TOGGLE_LIGHT, toggle_light, _TOGGLE_LIGHT_SCHEMA
    )

    return True


async def async_migrate_entry(_: HomeAssistant, entry: L360ConfigEntry) -> bool:
    """Migrate config entry."""
    # Currently, no migration is supported.
    version = str(entry.version)
    minor_version = cast(int | None, getattr(entry, "minor_version", None))
    if minor_version:
        version = f"{version}.{minor_version}"
    _LOGGER.error(
        "Unsupported configuration entry found: %s, version: %s; please remove it",
        entry.title,
        version,
    )
    return False


async def async_setup_entry(hass: HomeAssistant, entry: L360ConfigEntry) -> bool:
    """Set up config entry."""
    store = Life360Store(hass)
    await store.load()

    coordinator = CirclesMembersDataUpdateCoordinator(hass, store)
    await coordinator.async_config_entry_first_refresh()

    # Fetch Tile BLE auth keys if Tile credentials are configured
    # This populates the auth cache used for BLE ringing
    await coordinator.fetch_tile_auth_keys()

    mem_coordinator: dict[MemberID, MemberDataUpdateCoordinator] = {}

    async def async_process_data(forward: bool = False) -> None:
        """Process Members."""
        mids = set(coordinator.data.mem_details)
        coros = [
            mem_coordinator.pop(mid).async_shutdown()
            for mid in set(mem_coordinator) - mids
        ]
        for mid in mids - set(mem_coordinator):
            entry_was = config_entries.current_entry.get()
            config_entries.current_entry.set(entry)
            mem_crd = MemberDataUpdateCoordinator(hass, coordinator, mid)
            config_entries.current_entry.set(entry_was)
            mem_coordinator[mid] = mem_crd
            coros.append(mem_crd.async_refresh())
        if coros:
            await asyncio.gather(*coros)
            if forward:
                async_dispatcher_send(hass, SIGNAL_MEMBERS_CHANGED)

    @callback
    def process_data() -> None:
        """Process Members."""
        create_process_task = partial(
            entry.async_create_background_task,
            hass,
            async_process_data(forward=True),
            "Process Members",
        )
        # eager_start parameter was added in 2024.3.
        try:
            create_process_task(eager_start=True)
        except TypeError:
            create_process_task()

    await async_process_data()
    entry.async_on_unload(coordinator.async_add_listener(process_data))

    # Create device coordinator for Tiles and pet GPS trackers
    device_coordinator = DeviceDataUpdateCoordinator(hass, coordinator)
    await device_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = L360Coordinators(
        coordinator, mem_coordinator, device_coordinator
    )

    # Set up components for our platforms.
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: L360ConfigEntry) -> bool:
    """Unload config entry."""
    # The coordinators will eventually be shut down when their listeners stop listening
    # or ultimately when the config entry's "on unload" list is processed (which happens
    # after this method returns.) And any background tasks created by the coordinators
    # will be canceled after the "on unload" processing. But by then, resources those
    # tasks were using may have disappeared. So, shut down the coordinators now to give
    # them a chance to shutdown the background tasks themselves.
    shutdown_coros = [
        entry.runtime_data.coordinator.async_shutdown(),
        *(
            mem_crd.async_shutdown()
            for mem_crd in entry.runtime_data.mem_coordinator.values()
        ),
    ]
    if entry.runtime_data.device_coordinator:
        shutdown_coros.append(entry.runtime_data.device_coordinator.async_shutdown())
    await asyncio.gather(*shutdown_coros)
    # Unload components for our platforms.
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: L360ConfigEntry) -> bool:
    """Remove config entry."""
    # Don't delete store when removing old version 1 config entry.
    if entry.version < 2:
        return True
    await Life360Store(hass).remove()
    return True
