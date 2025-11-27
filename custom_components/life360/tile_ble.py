"""Tile BLE communication module for ringing Tile devices.

Based on reverse-engineered protocol from https://github.com/lesleyxyz/node-tile
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.exc import BleakError
    from bleak_retry_connector import establish_connection, BleakClientWithServiceCache
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    BleakClient = None
    BleakScanner = None
    BLEDevice = None
    BleakError = Exception
    establish_connection = None
    BleakClientWithServiceCache = None

_LOGGER = logging.getLogger(__name__)

# Tile BLE UUIDs
TILE_SERVICE_UUID = "0000feed-0000-1000-8000-00805f9b34fb"
MEP_COMMAND_CHAR_UUID = "9d410018-35d6-f4dd-ba60-e7bd8dc491c0"
MEP_RESPONSE_CHAR_UUID = "9d410019-35d6-f4dd-ba60-e7bd8dc491c0"
TILE_ID_CHAR_UUID = "9d410007-35d6-f4dd-ba60-e7bd8dc491c0"


class TileVolume(IntEnum):
    """Tile ring volume levels."""
    LOW = 1
    MED = 2
    HIGH = 3


class ToaCommand(IntEnum):
    """TOA (Tile Over Air) command types."""
    AUTH = 1
    CHANNEL = 2
    TDI = 3  # Tile Device Info
    SONG = 5  # Ring/Song command
    TDG = 6  # Diagnostic
    TIME = 7
    PPM = 8
    READY = 9
    TDT = 10  # Double-tap
    TCU = 11
    TFC = 12
    TKA = 13
    TRM = 14
    ADVINT = 15


class SongType(IntEnum):
    """Song/ring command types."""
    STOP = 0
    FIND = 1
    RING = 2


@dataclass
class TileAuthData:
    """Authentication data for a Tile device."""
    tile_id: str
    auth_key: bytes  # 16-byte authentication key

    @classmethod
    def from_hex(cls, tile_id: str, auth_key_hex: str) -> "TileAuthData":
        """Create from hex string auth key."""
        return cls(tile_id=tile_id, auth_key=bytes.fromhex(auth_key_hex))


class TileBleClient:
    """BLE client for communicating with Tile devices."""

    def __init__(
        self,
        tile_id: str,
        auth_key: bytes,
        timeout: float = 10.0,
    ) -> None:
        """Initialize Tile BLE client.

        Args:
            tile_id: The Tile device ID (MAC address or UUID)
            auth_key: 16-byte authentication key for this Tile
            timeout: Connection timeout in seconds
        """
        if not BLEAK_AVAILABLE:
            raise RuntimeError("bleak library not available")

        self.tile_id = tile_id
        self.auth_key = auth_key
        self.timeout = timeout
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._response_event = asyncio.Event()
        self._response_data: bytes = b""
        self._authenticated = False
        self._rand_a: bytes = b""
        self._channel_key: bytes = b""

    def _tile_id_to_mac(self, tile_id: str) -> str:
        """Convert Tile ID to expected BLE MAC address.

        Tiles use a random static BLE address derived from their device ID.
        The MAC address is the first 6 bytes of the Tile ID with the first byte
        modified to create a valid BLE random static address (bits 7-6 set to 11).

        Args:
            tile_id: Tile device ID (e.g., "03a757b8479cbdfc")

        Returns:
            Expected MAC address (e.g., "C3:A7:57:B8:47:9C")
        """
        # Remove any separators and convert to lowercase
        tile_id_clean = tile_id.lower().replace(":", "").replace("-", "")

        # Take first 6 bytes (12 hex chars)
        if len(tile_id_clean) < 12:
            _LOGGER.warning("Tile ID too short for MAC derivation: %s", tile_id)
            return ""

        tile_bytes = bytes.fromhex(tile_id_clean[:12])

        # For BLE random static address, bits 7-6 of first byte must be 11
        # So we set bits 7-6: (byte & 0x3F) | 0xC0
        mac_bytes = bytearray(tile_bytes)
        mac_bytes[0] = (mac_bytes[0] & 0x3F) | 0xC0

        # Format as MAC address
        mac = ":".join(f"{b:02X}" for b in mac_bytes)
        return mac

    async def scan_for_tile(self, scan_timeout: float = 10.0) -> BLEDevice | None:
        """Scan for the Tile device by ID.

        Args:
            scan_timeout: Scan timeout in seconds

        Returns:
            BLEDevice if found, None otherwise
        """
        _LOGGER.info("🔍 Scanning for Tile device: %s (timeout: %ds)", self.tile_id, scan_timeout)

        # Calculate expected MAC address from Tile ID
        expected_mac = self._tile_id_to_mac(self.tile_id)
        _LOGGER.info("💡 Derived expected MAC address from Tile ID: %s", expected_mac)

        # Normalize for comparison
        tile_id_lower = self.tile_id.lower().replace(":", "").replace("-", "")
        expected_mac_lower = expected_mac.lower().replace(":", "").replace("-", "")

        _LOGGER.debug("Normalized Tile ID for matching: %s", tile_id_lower)
        _LOGGER.debug("Normalized expected MAC for matching: %s", expected_mac_lower)

        found_device = None
        devices_seen = []

        def detection_callback(device: BLEDevice, advertisement_data):
            """Callback for each detected BLE device."""
            nonlocal found_device

            devices_seen.append(device.address)

            # Log ALL devices in diagnostic mode
            _LOGGER.warning(
                "🔧 BLE device detected: name=%s, address=%s, rssi=%s, service_uuids=%s",
                device.name or "N/A",
                device.address,
                advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else 'N/A',
                list(advertisement_data.service_uuids) if advertisement_data.service_uuids else "None"
            )

            # Normalize this device's MAC for comparison
            addr_normalized = device.address.lower().replace(":", "").replace("-", "")

            # PRIMARY: Check if device advertises Tile service UUID
            if advertisement_data.service_uuids and TILE_SERVICE_UUID in advertisement_data.service_uuids:
                # Check if this is OUR target Tile (exact MAC match)
                if addr_normalized == expected_mac_lower:
                    _LOGGER.warning("✅✅✅ FOUND TARGET TILE BY EXACT MAC MATCH at %s!", device.address)
                    _LOGGER.warning("   Service UUID: %s", TILE_SERVICE_UUID)
                    _LOGGER.warning("   MAC: %s", device.address)
                    _LOGGER.warning("   Expected MAC: %s", expected_mac)
                    _LOGGER.warning("   RSSI: %s", advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else 'N/A')
                    found_device = device
                    return  # Stop scanning - we found our target!
                else:
                    # Found A Tile, but not OUR Tile - log it but keep scanning
                    _LOGGER.warning("✅ FOUND TILE BY SERVICE UUID at %s (but not our target)", device.address)
                    _LOGGER.warning("   Service UUID: %s", TILE_SERVICE_UUID)
                    _LOGGER.warning("   MAC: %s (expected: %s)", device.address, expected_mac)
                    _LOGGER.warning("   RSSI: %s", advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else 'N/A')

                    # If we haven't found any device yet, use this as a fallback
                    if found_device is None:
                        _LOGGER.warning("   → Using as fallback candidate")
                        found_device = device
                    return

            # FALLBACK: Check by derived MAC address (less reliable due to MAC randomization)
            if addr_normalized == expected_mac_lower:
                _LOGGER.info("✅ MATCH: Found Tile by derived MAC address!")
                _LOGGER.info("   Tile ID: %s", self.tile_id)
                _LOGGER.info("   Expected MAC: %s", expected_mac)
                _LOGGER.info("   Actual MAC: %s", device.address)
                _LOGGER.info("   RSSI: %s", advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else 'N/A')
                found_device = device
                return

            # Fallback: Check if first 6 bytes of tile_id are in the MAC address
            if len(tile_id_lower) >= 12 and tile_id_lower[:12] in addr_normalized:
                _LOGGER.info("✅ Found matching Tile by partial ID in address: %s", device.address)
                if found_device is None:  # Only use if we haven't found anything better
                    found_device = device
                return

            # Fallback: Check by name if it contains tile ID
            if device.name and tile_id_lower[:8] in device.name.lower():
                _LOGGER.info("✅ Found matching Tile by name: %s at %s", device.name, device.address)
                if found_device is None:  # Only use if we haven't found anything better
                    found_device = device

        try:
            # DIAGNOSTIC: Persistent scan - stop as soon as target found
            _LOGGER.warning("🔧 DIAGNOSTIC MODE: Persistent scan (up to %.0f seconds)", scan_timeout)
            _LOGGER.warning("🔧 Will connect immediately when Tile is detected")

            scanner = BleakScanner(
                detection_callback=detection_callback,
                # service_uuids=[TILE_SERVICE_UUID],  # Temporarily disabled for diagnostics
            )

            await scanner.start()

            # Persistent scan: check every 0.5 seconds if we found the device
            start_time = asyncio.get_event_loop().time()
            check_interval = 0.5  # Check twice per second

            while True:
                elapsed = asyncio.get_event_loop().time() - start_time

                # If we found the target device, stop immediately!
                if found_device:
                    _LOGGER.warning("✅ Target found at %.1fs - stopping scan early!", elapsed)
                    break

                # If we've exceeded the timeout, give up
                if elapsed >= scan_timeout:
                    _LOGGER.warning("⏱️ Scan timeout reached after %.0fs", scan_timeout)
                    break

                # Wait a bit before checking again
                await asyncio.sleep(check_interval)

            await scanner.stop()

            _LOGGER.warning("🔧 DIAGNOSTIC: Scan complete - detected %d BLE devices total", len(devices_seen))
            if devices_seen:
                _LOGGER.warning("🔧 Devices found: %s", ", ".join(devices_seen))

            if found_device:
                _LOGGER.info("✅ Successfully located Tile device!")
                self._device = found_device
                return found_device

            _LOGGER.warning("❌ Tile %s not found in BLE range after %.0fs", self.tile_id, scan_timeout)
            _LOGGER.warning("   Expected MAC: %s", expected_mac)
            _LOGGER.warning("   If the Tile is nearby, it may be out of range or sleeping")
            if not devices_seen:
                _LOGGER.error("🔧 DIAGNOSTIC: No devices found at all - BLE adapter may not be working!")
            return None

        except Exception as err:
            _LOGGER.error("❌ BLE scan failed: %s", err, exc_info=True)
            return None

    async def connect(self, device: BLEDevice | None = None) -> bool:
        """Connect to the Tile device.

        Args:
            device: BLEDevice to connect to, or None to scan first

        Returns:
            True if connected successfully
        """
        if device:
            self._device = device
        elif not self._device:
            self._device = await self.scan_for_tile()

        if not self._device:
            _LOGGER.error("❌ No Tile device to connect to")
            return False

        try:
            _LOGGER.info("🔌 Connecting to Tile at %s using bleak-retry-connector...", self._device.address)

            # Use bleak-retry-connector for reliable connections
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self._device,
                self._device.address,
                disconnected_callback=self._handle_disconnect,
                timeout=30.0,
            )

            if not self._client.is_connected:
                raise BleakError("Connection established but client reports not connected")

            _LOGGER.info("✅ Connected to Tile successfully!")
            _LOGGER.debug("📝 Subscribing to Tile response notifications...")

            # Subscribe to responses
            await self._client.start_notify(
                MEP_RESPONSE_CHAR_UUID,
                self._handle_response,
            )

            _LOGGER.info("✅ Notifications enabled - ready to ring!")
            return True

        except asyncio.TimeoutError:
            _LOGGER.error("❌ Connection to Tile timed out after 30 seconds")
            self._client = None
            return False

        except BleakError as err:
            _LOGGER.error("❌ Failed to connect to Tile: %s", err, exc_info=True)
            self._client = None
            return False

    def _handle_disconnect(self, client: BleakClient) -> None:
        """Handle disconnection from Tile."""
        _LOGGER.warning("⚠️ Tile disconnected")
        self._authenticated = False

    async def disconnect(self) -> None:
        """Disconnect from the Tile device."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except BleakError:
                pass
        self._client = None
        self._authenticated = False

    def _handle_response(self, sender: Any, data: bytes) -> None:
        """Handle response from Tile."""
        _LOGGER.debug("Tile response: %s", data.hex())
        self._response_data = data
        self._response_event.set()

    async def _send_command(self, data: bytes) -> bytes:
        """Send command and wait for response.

        Args:
            data: Command bytes to send

        Returns:
            Response bytes
        """
        if not self._client or not self._client.is_connected:
            raise RuntimeError("Not connected to Tile")

        self._response_event.clear()
        self._response_data = b""

        _LOGGER.debug("Sending to Tile: %s", data.hex())
        await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, data)

        # Wait for response
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for Tile response")
            return b""

        return self._response_data

    async def authenticate(self) -> bool:
        """Perform authentication handshake with Tile.

        Returns:
            True if authentication succeeded
        """
        if not self._client or not self._client.is_connected:
            _LOGGER.error("❌ Not connected to Tile - cannot authenticate")
            return False

        try:
            _LOGGER.info("🔐 Starting Tile authentication handshake...")

            # Generate random value for authentication
            self._rand_a = os.urandom(8)
            _LOGGER.debug("Generated randA: %s", self._rand_a.hex())

            # Step 1: Send AUTH command with randA
            # Format: [TOA_AUTH, randA (8 bytes)]
            auth_cmd = bytes([ToaCommand.AUTH]) + self._rand_a
            _LOGGER.debug("Step 1: Sending AUTH command with randA")
            response = await self._send_command(auth_cmd)

            if len(response) < 17:
                _LOGGER.error("❌ Invalid auth response length: %d (expected >= 17)", len(response))
                return False

            # Response format: [cmd, randT (8 bytes), sresT (8 bytes)]
            rand_t = response[1:9]
            sres_t = response[9:17]
            _LOGGER.debug("Received randT: %s, sresT: %s", rand_t.hex(), sres_t.hex())

            # Step 2: Verify Tile's response
            _LOGGER.debug("Step 2: Verifying Tile's signature")
            expected_sres_t = self._compute_sres(rand_t, self._rand_a, self.auth_key)
            if sres_t != expected_sres_t:
                _LOGGER.error("❌ Tile authentication failed - invalid sresT (signature mismatch)")
                _LOGGER.debug("Expected: %s, Got: %s", expected_sres_t.hex(), sres_t.hex())
                return False

            _LOGGER.debug("✓ Tile signature verified")

            # Step 3: Send our sresA to prove we have the auth key
            _LOGGER.debug("Step 3: Sending our signature (sresA)")
            sres_a = self._compute_sres(self._rand_a, rand_t, self.auth_key)
            ack_cmd = bytes([ToaCommand.AUTH]) + sres_a
            response = await self._send_command(ack_cmd)

            # Check for success
            if len(response) > 0 and response[0] == ToaCommand.AUTH:
                self._authenticated = True
                # Derive channel key for subsequent commands
                self._channel_key = self._derive_channel_key(
                    self._rand_a, rand_t, self.auth_key
                )
                _LOGGER.info("✅ Tile authentication successful!")
                return True

            _LOGGER.error("❌ Tile authentication failed - unexpected response: %s", response.hex() if response else "empty")
            return False

        except Exception as err:
            _LOGGER.error("❌ Authentication error: %s", err, exc_info=True)
            return False

    def _compute_sres(
        self, rand1: bytes, rand2: bytes, key: bytes
    ) -> bytes:
        """Compute SRES (Signed Response) value.

        Args:
            rand1: First random value (8 bytes)
            rand2: Second random value (8 bytes)
            key: Authentication key (16 bytes)

        Returns:
            8-byte SRES value
        """
        # HMAC-SHA256, truncated to 8 bytes
        msg = rand1 + rand2
        h = hmac.new(key, msg, hashlib.sha256)
        return h.digest()[:8]

    def _derive_channel_key(
        self, rand_a: bytes, rand_t: bytes, auth_key: bytes
    ) -> bytes:
        """Derive channel encryption key from authentication values.

        Args:
            rand_a: Our random value
            rand_t: Tile's random value
            auth_key: Authentication key

        Returns:
            16-byte channel key
        """
        msg = rand_a + rand_t + b"channel"
        h = hmac.new(auth_key, msg, hashlib.sha256)
        return h.digest()[:16]

    def _build_ring_command(
        self,
        volume: TileVolume = TileVolume.MED,
        duration_seconds: int = 30,
    ) -> bytes:
        """Build the ring/find command.

        Args:
            volume: Ring volume level
            duration_seconds: How long to ring

        Returns:
            Command bytes
        """
        # TOA Song command format:
        # [SONG, transaction_type, volume_type, volume_level, duration?]
        cmd = bytes([
            ToaCommand.SONG,
            SongType.RING,  # Ring transaction type
            1,  # Volume type indicator
            volume.value,  # Volume level (1=LOW, 2=MED, 3=HIGH)
            duration_seconds,  # Duration in seconds
        ])
        return cmd

    def _build_stop_command(self) -> bytes:
        """Build the stop ring command.

        Returns:
            Command bytes
        """
        cmd = bytes([
            ToaCommand.SONG,
            SongType.STOP,  # Stop
        ])
        return cmd

    async def ring(
        self,
        volume: TileVolume = TileVolume.MED,
        duration_seconds: int = 30,
    ) -> bool:
        """Ring the Tile device.

        Args:
            volume: Ring volume level
            duration_seconds: How long to ring (if supported)

        Returns:
            True if ring command was sent successfully
        """
        if not self._client or not self._client.is_connected:
            _LOGGER.error("❌ Not connected to Tile - cannot ring")
            return False

        if not self._authenticated:
            _LOGGER.debug("Not authenticated yet, performing authentication...")
            if not await self.authenticate():
                _LOGGER.error("❌ Authentication required before ringing, but failed")
                return False

        try:
            _LOGGER.info("🔔 Sending ring command (volume=%s, duration=%ds)...", volume.name, duration_seconds)
            cmd = self._build_ring_command(volume, duration_seconds)
            _LOGGER.debug("Ring command bytes: %s", cmd.hex())
            response = await self._send_command(cmd)

            # Check response
            if len(response) > 0:
                _LOGGER.info("✅ Tile ring command sent successfully! Response: %s", response.hex())
                return True

            _LOGGER.warning("⚠️  No response to ring command (this may be normal)")
            # Some Tiles may not respond but still ring, so return True
            return True

        except Exception as err:
            _LOGGER.error("❌ Error sending ring command: %s", err, exc_info=True)
            return False

    async def stop_ring(self) -> bool:
        """Stop ringing the Tile device.

        Returns:
            True if stop command was sent successfully
        """
        if not self._client or not self._client.is_connected:
            _LOGGER.error("Not connected to Tile")
            return False

        try:
            cmd = self._build_stop_command()
            await self._send_command(cmd)
            _LOGGER.info("Tile stop ring command sent")
            return True

        except Exception as err:
            _LOGGER.error("Error sending stop command: %s", err)
            return False


async def ring_tile_ble(
    tile_id: str,
    auth_key: bytes | str,
    volume: TileVolume = TileVolume.MED,
    duration_seconds: int = 30,
    scan_timeout: float = 10.0,
) -> bool:
    """High-level function to ring a Tile via BLE.

    Args:
        tile_id: Tile device ID or MAC address
        auth_key: 16-byte auth key or hex string
        volume: Ring volume level
        duration_seconds: Ring duration
        scan_timeout: BLE scan timeout

    Returns:
        True if successfully rang the Tile
    """
    if not BLEAK_AVAILABLE:
        _LOGGER.error("❌ bleak library not available for BLE communication")
        return False

    _LOGGER.info("═══════════════════════════════════════════════════════════")
    _LOGGER.info("Starting Tile BLE ring operation for device: %s", tile_id)
    _LOGGER.info("═══════════════════════════════════════════════════════════")

    # Convert hex string to bytes if needed
    if isinstance(auth_key, str):
        try:
            auth_key = bytes.fromhex(auth_key)
            _LOGGER.debug("Auth key decoded: %d bytes", len(auth_key))
        except ValueError as err:
            _LOGGER.error("❌ Invalid auth key hex string: %s", err)
            return False

    if len(auth_key) != 16:
        _LOGGER.error("❌ Invalid auth key length: %d (expected 16)", len(auth_key))
        return False

    client = TileBleClient(tile_id, auth_key)

    try:
        # Scan for the Tile
        device = await client.scan_for_tile(scan_timeout)
        if not device:
            _LOGGER.warning("❌ Tile %s not found in BLE range", tile_id)
            _LOGGER.info("💡 Tip: Make sure the Tile is nearby and has battery power")
            return False

        # Connect
        if not await client.connect(device):
            _LOGGER.error("❌ Failed to connect to Tile")
            return False

        # Ring it
        success = await client.ring(volume, duration_seconds)
        if success:
            _LOGGER.info("═══════════════════════════════════════════════════════════")
            _LOGGER.info("✅ Tile BLE ring operation completed successfully!")
            _LOGGER.info("═══════════════════════════════════════════════════════════")
        else:
            _LOGGER.error("❌ Tile ring operation failed")
        return success

    except Exception as err:
        _LOGGER.error("❌ Unexpected error during Tile BLE operation: %s", err, exc_info=True)
        return False

    finally:
        await client.disconnect()


async def stop_ring_tile_ble(
    tile_id: str,
    auth_key: bytes | str,
    scan_timeout: float = 10.0,
) -> bool:
    """High-level function to stop ringing a Tile via BLE.

    Args:
        tile_id: Tile device ID or MAC address
        auth_key: 16-byte auth key or hex string
        scan_timeout: BLE scan timeout

    Returns:
        True if successfully stopped ringing
    """
    if not BLEAK_AVAILABLE:
        _LOGGER.error("bleak library not available for BLE communication")
        return False

    if isinstance(auth_key, str):
        auth_key = bytes.fromhex(auth_key)

    client = TileBleClient(tile_id, auth_key)

    try:
        device = await client.scan_for_tile(scan_timeout)
        if not device:
            _LOGGER.warning("Tile %s not found in BLE range", tile_id)
            return False

        if not await client.connect(device):
            return False

        # Need to authenticate first
        if not await client.authenticate():
            return False

        return await client.stop_ring()

    finally:
        await client.disconnect()


async def discover_and_verify_tile_macs(
    scan_timeout: float = 15.0,
    hass = None,
) -> dict[str, str]:
    """Scan for all Tile devices and read their actual device IDs to verify MAC mappings.

    This is a diagnostic function to verify that our MAC derivation formula is correct.
    It connects to each Tile found and reads the device ID from GATT characteristics.

    Args:
        scan_timeout: BLE scan timeout in seconds
        hass: Home Assistant instance (optional, for using HA Bluetooth backend)

    Returns:
        Dictionary mapping MAC addresses to actual Tile IDs read from devices
    """
    if not BLEAK_AVAILABLE:
        _LOGGER.error("❌ bleak library not available for BLE communication")
        return {}

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("🔍 DIAGNOSTIC: Discovering ALL Tiles and reading device IDs")
    _LOGGER.warning("═══════════════════════════════════════════════════════════")

    discovered_tiles = []
    mac_to_id_map = {}

    # Use Home Assistant's Bluetooth backend if available
    if hass is not None:
        try:
            from homeassistant.components import bluetooth

            _LOGGER.warning("🔍 Using Home Assistant Bluetooth backend to find Tiles...")

            # Get all devices from HA's Bluetooth backend
            service_info_list = bluetooth.async_discovered_service_info(hass)

            _LOGGER.warning("📡 HA Bluetooth backend sees %d devices total", len(service_info_list))

            # DEBUG: Log ALL devices to see what we're checking
            _LOGGER.warning("🔧 DEBUG: Listing ALL %d devices from HA Bluetooth:", len(service_info_list))
            for idx, service_info in enumerate(service_info_list, 1):
                _LOGGER.warning("   %d. %s (%s) - Service UUIDs: %s",
                              idx,
                              service_info.name or "Unknown",
                              service_info.address,
                              service_info.service_uuids if service_info.service_uuids else "None")

            _LOGGER.warning("🔍 Looking for Tile service UUID: %s", TILE_SERVICE_UUID)

            # Filter for Tiles
            for service_info in service_info_list:
                if TILE_SERVICE_UUID in service_info.service_uuids:
                    _LOGGER.warning("✅ Found Tile: %s at %s (RSSI: %s)",
                                  service_info.name or "Unknown",
                                  service_info.address,
                                  service_info.rssi)
                    # Convert ServiceInfo to BLEDevice
                    discovered_tiles.append(service_info.device)

            _LOGGER.warning("🔍 Found %d Tile(s) from HA Bluetooth", len(discovered_tiles))

        except Exception as err:
            _LOGGER.error("❌ Failed to use HA Bluetooth backend: %s", err)
            _LOGGER.warning("⚠️ Falling back to direct BleakScanner...")
            hass = None  # Fall back to direct scanning

    # Fallback: Direct BleakScanner if HA not available
    if hass is None:
        def detection_callback(device: BLEDevice, advertisement_data):
            """Callback for each detected BLE device."""
            # Check if device advertises Tile service
            service_uuids = advertisement_data.service_uuids if hasattr(advertisement_data, 'service_uuids') else []

            if TILE_SERVICE_UUID in service_uuids:
                _LOGGER.warning("✅ Found Tile: %s at %s (RSSI: %s)",
                              device.name or "Unknown",
                              device.address,
                              advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else 'N/A')
                discovered_tiles.append(device)

        try:
            # Scan for Tiles
            _LOGGER.warning("🔍 Scanning for Tile devices (filtering by service UUID)...")
            scanner = BleakScanner(
                detection_callback=detection_callback,
                service_uuids=[TILE_SERVICE_UUID],
            )

            await scanner.start()
            await asyncio.sleep(scan_timeout)
            await scanner.stop()

            _LOGGER.warning("🔍 Scan complete - found %d Tile(s)", len(discovered_tiles))
        except Exception as scan_err:
            _LOGGER.error("❌ Scan failed: %s", scan_err)
            discovered_tiles = []

    try:

        if not discovered_tiles:
            _LOGGER.warning("⚠️  No Tiles found in range - make sure they're nearby and awake")
            _LOGGER.warning("💡 Try pressing the button on each Tile to wake it up")
            return {}

        # Now connect to each discovered Tile and read its device ID
        for device in discovered_tiles:
            _LOGGER.warning("─────────────────────────────────────────────────────────")
            _LOGGER.warning("📱 Connecting to Tile at %s...", device.address)

            client = None
            try:
                # Use bleak-retry-connector for reliable connections with HA Bluetooth
                if hass is not None:
                    _LOGGER.warning("🔌 Using bleak-retry-connector with HA Bluetooth backend...")
                    client = await establish_connection(
                        BleakClientWithServiceCache,
                        device,
                        device.name or device.address,
                        disconnected_callback=lambda _: None,
                        max_attempts=3,
                    )
                else:
                    _LOGGER.warning("🔌 Using direct BleakClient connection...")
                    client = BleakClient(device, timeout=30.0)
                    await asyncio.wait_for(client.connect(), timeout=30.0)

                if not client.is_connected:
                    _LOGGER.error("❌ Failed to connect to %s", device.address)
                    continue

                _LOGGER.warning("✅ Connected! Reading device ID from GATT characteristic...")

                # Try to read the Tile ID characteristic
                try:
                    tile_id_bytes = await client.read_gatt_char(TILE_ID_CHAR_UUID)
                    tile_id_hex = tile_id_bytes.hex()

                    _LOGGER.warning("✅ SUCCESS! Read device ID from Tile:")
                    _LOGGER.warning("   MAC Address: %s", device.address)
                    _LOGGER.warning("   Device ID:   %s", tile_id_hex)

                    # Store the mapping
                    mac_to_id_map[device.address] = tile_id_hex

                    # Verify against our derivation formula
                    derived_mac = TileBleClient(tile_id_hex, b"0"*16)._tile_id_to_mac(tile_id_hex)
                    _LOGGER.warning("   Derived MAC: %s", derived_mac)

                    if derived_mac.upper() == device.address.upper():
                        _LOGGER.warning("   ✅ MATCH! Our derivation formula is CORRECT!")
                    else:
                        _LOGGER.warning("   ❌ MISMATCH! Our derivation formula is WRONG!")
                        _LOGGER.warning("   Expected: %s", derived_mac)
                        _LOGGER.warning("   Got:      %s", device.address)

                except Exception as char_err:
                    _LOGGER.error("❌ Failed to read device ID characteristic: %s", char_err)
                    _LOGGER.warning("💡 Trying to list all characteristics...")

                    # List all services and characteristics as fallback
                    try:
                        for service in client.services:
                            _LOGGER.warning("   Service: %s", service.uuid)
                            for char in service.characteristics:
                                _LOGGER.warning("      Char: %s (properties: %s)",
                                              char.uuid, char.properties)
                                # Try to read if readable
                                if "read" in char.properties:
                                    try:
                                        value = await client.read_gatt_char(char.uuid)
                                        _LOGGER.warning("         Value: %s (hex: %s)",
                                                      value, value.hex())
                                    except Exception:
                                        pass
                    except Exception as list_err:
                        _LOGGER.error("❌ Failed to list characteristics: %s", list_err)

            except asyncio.TimeoutError:
                _LOGGER.error("❌ Connection timeout for %s", device.address)
            except Exception as err:
                _LOGGER.error("❌ Error connecting to %s: %s", device.address, err, exc_info=True)
            finally:
                if client and client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("📊 FINAL RESULTS:")
        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        if mac_to_id_map:
            for mac, tile_id in mac_to_id_map.items():
                _LOGGER.warning("MAC: %s → Tile ID: %s", mac, tile_id)
        else:
            _LOGGER.warning("⚠️  No device IDs were successfully read")
        _LOGGER.warning("═══════════════════════════════════════════════════════════")

        return mac_to_id_map

    except Exception as err:
        _LOGGER.error("❌ Diagnostic scan failed: %s", err, exc_info=True)
        return {}


async def diagnose_ring_all_ble_devices(
    hass,
    auth_keys: dict[str, bytes],
) -> dict[str, str]:
    """Try to ring ALL BLE devices to find which ones are Tiles.

    This is a brute-force diagnostic that attempts to connect and ring every
    BLE device seen by Home Assistant to discover which MACs are actually Tiles.

    Args:
        hass: Home Assistant instance
        auth_keys: Dict mapping Tile IDs to auth keys from API

    Returns:
        Dictionary mapping MAC addresses to results (success/failure)
    """
    if not BLEAK_AVAILABLE:
        _LOGGER.error("❌ bleak library not available for BLE communication")
        return {}

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("🔥 DIAGNOSTIC: Trying to ring EVERY BLE device!")
    _LOGGER.warning("⚠️  WARNING: This will attempt connections to ALL nearby devices")
    _LOGGER.warning("═══════════════════════════════════════════════════════════")

    from homeassistant.components import bluetooth

    # Get all devices from HA's Bluetooth backend - make a list copy to avoid modification during iteration
    service_info_list = list(bluetooth.async_discovered_service_info(hass))

    _LOGGER.warning("📡 Found %d BLE devices total", len(service_info_list))

    results = {}

    if not auth_keys:
        _LOGGER.error("❌ No auth keys provided - cannot authenticate with Tiles")
        return {}

    _LOGGER.warning("🔑 Have %d auth keys to try", len(auth_keys))

    # Try each device once
    for idx, service_info in enumerate(service_info_list, 1):
        device = service_info.device
        _LOGGER.warning("─────────────────────────────────────────────────────────")
        _LOGGER.warning("📱 %d/%d: Testing %s (%s)",
                      idx, len(service_info_list),
                      service_info.name or "Unknown",
                      device.address)

        client = None
        try:
            # Try to connect
            _LOGGER.warning("   🔌 Connecting...")
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                device.name or device.address,
                disconnected_callback=lambda _: None,
                max_attempts=1,  # Only 1 attempt to keep it fast
                timeout=10.0,
            )

            if not client.is_connected:
                _LOGGER.warning("   ❌ Connection failed")
                results[device.address] = "connection_failed"
                continue

            _LOGGER.warning("   ✅ Connected!")

            # Try to subscribe to Tile response characteristic
            try:
                response_data = None

                def response_handler(sender, data):
                    nonlocal response_data
                    response_data = data

                await client.start_notify(MEP_RESPONSE_CHAR_UUID, response_handler)
                _LOGGER.warning("   ✅ Subscribed to Tile response characteristic")

                # Try authentication with EACH auth key until one works
                auth_success = False
                working_tile_id = None
                working_auth_key = None

                for tile_id, auth_key in auth_keys.items():
                    _LOGGER.warning("   🔐 Trying auth key for Tile: %s", tile_id)
                    response_data = None  # Reset for each attempt

                    rand_a = os.urandom(8)
                    auth_cmd = bytes([ToaCommand.AUTH]) + rand_a
                    await client.write_gatt_char(MEP_COMMAND_CHAR_UUID, auth_cmd)

                    # Wait for response
                    await asyncio.sleep(1.0)

                    if response_data and len(response_data) >= 17:
                        _LOGGER.warning("   ✅ Got Tile auth response! THIS IS A TILE!")
                        _LOGGER.warning("   🎉 FOUND TILE AT: %s", device.address)

                        # Try to ring it
                        rand_t = response_data[1:9]
                        sres_t = response_data[9:17]

                        # Compute our response
                        msg = rand_a + rand_t
                        h = hmac.new(auth_key, msg, hashlib.sha256)
                        sres_a = h.digest()[:8]

                        # Send our sres
                        ack_cmd = bytes([ToaCommand.AUTH]) + sres_a
                        await client.write_gatt_char(MEP_COMMAND_CHAR_UUID, ack_cmd)
                        await asyncio.sleep(0.5)

                        # Send ring command
                        _LOGGER.warning("   🔔 Sending ring command...")
                        ring_cmd = bytes([ToaCommand.SONG, SongType.RING, 1, 3, 10])
                        await client.write_gatt_char(MEP_COMMAND_CHAR_UUID, ring_cmd)

                        _LOGGER.warning("   ✅ Ring command sent! Listen for the Tile!")
                        results[device.address] = f"SUCCESS_TILE_{tile_id}"
                        auth_success = True
                        break  # Found the right auth key, stop trying others

                if not auth_success:
                    _LOGGER.warning("   ❌ No Tile response - not a Tile or wrong auth keys")
                    results[device.address] = "not_a_tile"

            except Exception as char_err:
                _LOGGER.warning("   ❌ Not a Tile (no characteristic): %s", str(char_err)[:50])
                results[device.address] = "not_a_tile"

        except asyncio.TimeoutError:
            _LOGGER.warning("   ⏱️  Timeout")
            results[device.address] = "timeout"
        except Exception as err:
            _LOGGER.warning("   ❌ Error: %s", str(err)[:50])
            results[device.address] = f"error: {str(err)[:30]}"
        finally:
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("📊 RING ALL RESULTS:")
    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    for mac, result in results.items():
        if "SUCCESS" in result:
            _LOGGER.warning("🎉 %s → %s", mac, result)
        else:
            _LOGGER.warning("   %s → %s", mac, result)
    _LOGGER.warning("═══════════════════════════════════════════════════════════")

    return results


async def diagnose_ring_tile_by_mac(
    mac_address: str,
    tile_id: str,
    auth_key: bytes,
    scan_timeout: float = 120.0,
) -> dict[str, Any]:
    """Test ringing a specific Tile by MAC address.

    This diagnostic connects directly to a known MAC and attempts to ring it.
    Uses persistent scanning that stops as soon as the Tile is found.

    Args:
        mac_address: The MAC address to connect to
        tile_id: The Tile device ID (for logging)
        auth_key: The authentication key
        scan_timeout: Max seconds to scan for Tile (default 120s = 2 min)

    Returns:
        Dictionary with test results
    """
    if not BLEAK_AVAILABLE:
        _LOGGER.error("❌ bleak library not available for BLE communication")
        return {}

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("🔔 DIAGNOSTIC: Test ring Tile via BLE")
    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("   Target MAC: %s", mac_address)
    _LOGGER.warning("   Tile ID: %s", tile_id)
    _LOGGER.warning("   Auth key length: %d bytes", len(auth_key))
    _LOGGER.warning("   Scan timeout: %.0f seconds (will stop early if found)", scan_timeout)

    try:
        # Create Tile BLE client with the Tile ID (not MAC address!)
        client = TileBleClient(tile_id, auth_key, timeout=scan_timeout)

        _LOGGER.warning("🔌 Connecting to Tile...")
        _LOGGER.warning("   Scanning for device...")
        connected = await client.connect()

        if not connected:
            _LOGGER.error("❌ Failed to connect")
            # Check if device was found during scan
            device_found = client._device is not None
            return {
                "success": False,
                "error": "Connection failed",
                "device_found_in_scan": device_found,
                "scanned_for_mac": client._tile_id_to_mac(tile_id),
            }

        _LOGGER.warning("✅ Connected!")

        # Authenticate
        _LOGGER.warning("🔐 Authenticating...")
        auth_success = await client.authenticate()

        if not auth_success:
            _LOGGER.error("❌ Authentication failed")
            await client.disconnect()
            return {"success": False, "error": "Authentication failed"}

        _LOGGER.warning("✅ Authenticated!")

        # Ring the Tile
        _LOGGER.warning("🔔 Sending ring command...")
        ring_success = await client.ring(volume=TileVolume.HIGH, duration_seconds=10)

        if ring_success:
            _LOGGER.warning("🎉 SUCCESS! Tile should be ringing for 10 seconds!")
            _LOGGER.warning("🔊 Listen for the Tile ringing...")
        else:
            _LOGGER.error("❌ Ring command failed")

        await client.disconnect()
        _LOGGER.warning("✅ Disconnected")

        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        return {
            "success": ring_success,
            "mac_address": mac_address,
            "tile_id": tile_id,
            "connected": True,
            "authenticated": True,
            "rang": ring_success,
        }

    except Exception as err:
        _LOGGER.error("❌ Test failed: %s", err, exc_info=True)
        return {
            "success": False,
            "error": str(err),
            "mac_address": mac_address,
            "tile_id": tile_id,
        }


async def diagnose_list_tiles(coordinator) -> dict[str, Any]:
    """List all cached Tile devices with their IDs, MACs, and auth keys.

    Args:
        coordinator: The Life360 coordinator instance

    Returns:
        Dictionary with all Tile device information
    """
    result = {
        "tiles": [],
        "count": 0,
    }

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("📋 DIAGNOSTIC: List all cached Tile devices")
    _LOGGER.warning("═══════════════════════════════════════════════════════════")

    # Get all Tile devices from MAC cache
    for device_id, mac_address in coordinator._tile_mac_cache.items():
        # Try to find the Tile ID from auth cache
        tile_id = None
        has_auth = False

        for tid, auth_key in coordinator._tile_auth_cache.items():
            # Derive MAC from this Tile ID to see if it matches
            derived_mac = TileBleClient._tile_id_to_mac(tid)
            if derived_mac.upper() == mac_address.upper():
                tile_id = tid
                has_auth = True
                break

        tile_info = {
            "device_id": device_id,
            "mac_address": mac_address,
            "tile_id": tile_id or "Unknown",
            "has_auth_key": has_auth,
        }

        result["tiles"].append(tile_info)

        _LOGGER.warning(
            "  🔹 Device: %s\n"
            "     MAC: %s\n"
            "     Tile ID: %s\n"
            "     Auth: %s",
            device_id,
            mac_address,
            tile_id or "Unknown",
            "✓" if has_auth else "✗"
        )

    result["count"] = len(result["tiles"])

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("✅ Found %d Tile device(s)", result["count"])
    _LOGGER.warning("═══════════════════════════════════════════════════════════")

    return result


async def diagnose_raw_ble_scan(scan_timeout: float = 30.0) -> dict[str, Any]:
    """Direct BLE scan bypassing HA's backend to capture raw advertisement data.

    This diagnostic uses BleakScanner directly to see all advertisement data
    including service UUIDs, service data, manufacturer data, etc.

    Args:
        scan_timeout: How long to scan in seconds

    Returns:
        Dictionary with scan results and details
    """
    if not BLEAK_AVAILABLE:
        _LOGGER.error("❌ bleak library not available for BLE communication")
        return {}

    _LOGGER.warning("═══════════════════════════════════════════════════════════")
    _LOGGER.warning("🔬 DIAGNOSTIC: Raw BLE scan (bypassing HA Bluetooth)")
    _LOGGER.warning("═══════════════════════════════════════════════════════════")

    devices_found = {}
    tiles_found = []

    def detection_callback(device: BLEDevice, advertisement_data):
        """Callback for each detected BLE device - log EVERYTHING."""
        if device.address not in devices_found:
            # Log complete advertisement data
            service_uuids = list(advertisement_data.service_uuids) if advertisement_data.service_uuids else []
            service_data = dict(advertisement_data.service_data) if advertisement_data.service_data else {}
            manufacturer_data = dict(advertisement_data.manufacturer_data) if advertisement_data.manufacturer_data else {}

            devices_found[device.address] = {
                "name": device.name,
                "rssi": advertisement_data.rssi,
                "service_uuids": service_uuids,
                "service_data": {k: v.hex() for k, v in service_data.items()},
                "manufacturer_data": {k: v.hex() for k, v in manufacturer_data.items()},
                "local_name": advertisement_data.local_name,
            }

            _LOGGER.warning("📱 Device: %s (%s)", device.name or device.address, device.address)
            _LOGGER.warning("   RSSI: %s dBm", advertisement_data.rssi)
            _LOGGER.warning("   Service UUIDs: %s", service_uuids or "None")
            if service_data:
                _LOGGER.warning("   Service Data:")
                for uuid, data in service_data.items():
                    _LOGGER.warning("      %s: %s", uuid, data.hex())
            if manufacturer_data:
                _LOGGER.warning("   Manufacturer Data:")
                for company_id, data in manufacturer_data.items():
                    _LOGGER.warning("      Company %s: %s", hex(company_id), data.hex())

            # Check if this is a Tile
            if TILE_SERVICE_UUID in service_uuids:
                _LOGGER.warning("   🎉 THIS IS A TILE!")
                tiles_found.append(device.address)

            # Also check for 0xFEED in service data or 16-bit UUID format
            if "0000feed-0000-1000-8000-00805f9b34fb" in service_uuids or \
               any("feed" in str(uuid).lower() for uuid in service_uuids):
                _LOGGER.warning("   🎉 FOUND FEED UUID!")
                if device.address not in tiles_found:
                    tiles_found.append(device.address)

    try:
        _LOGGER.warning("🔍 Starting direct BLE scan for %d seconds...", scan_timeout)
        _LOGGER.warning("💡 Press Tile buttons NOW to wake them up!")

        scanner = BleakScanner(detection_callback=detection_callback)

        await scanner.start()
        await asyncio.sleep(scan_timeout)
        await scanner.stop()

        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("📊 SCAN RESULTS:")
        _LOGGER.warning("═══════════════════════════════════════════════════════════")
        _LOGGER.warning("   Total devices found: %d", len(devices_found))
        _LOGGER.warning("   Tiles identified: %d", len(tiles_found))
        if tiles_found:
            _LOGGER.warning("   Tile MACs: %s", tiles_found)
        _LOGGER.warning("═══════════════════════════════════════════════════════════")

        return {
            "total_devices": len(devices_found),
            "tiles_found": len(tiles_found),
            "tile_macs": tiles_found,
            "all_devices": devices_found,
        }

    except Exception as err:
        _LOGGER.error("❌ Direct scan failed: %s", err, exc_info=True)
        return {}

