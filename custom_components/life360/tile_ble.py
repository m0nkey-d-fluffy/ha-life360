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
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    BleakClient = None
    BleakScanner = None
    BLEDevice = None
    BleakError = Exception

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

    async def scan_for_tile(self, scan_timeout: float = 10.0) -> BLEDevice | None:
        """Scan for the Tile device by ID.

        Args:
            scan_timeout: Scan timeout in seconds

        Returns:
            BLEDevice if found, None otherwise
        """
        _LOGGER.info("🔍 Scanning for Tile device: %s (timeout: %ds)", self.tile_id, scan_timeout)

        # Normalize tile_id for comparison
        tile_id_lower = self.tile_id.lower().replace(":", "").replace("-", "")
        _LOGGER.debug("Normalized Tile ID for matching: %s", tile_id_lower)

        try:
            devices = await BleakScanner.discover(
                timeout=scan_timeout,
                service_uuids=[TILE_SERVICE_UUID],
            )
            _LOGGER.info("📡 BLE scan complete: Found %d Tile devices nearby", len(devices))

            for device in devices:
                _LOGGER.debug(
                    "  Device found: name=%s, address=%s, rssi=%s",
                    device.name or "N/A",
                    device.address,
                    getattr(device, 'rssi', 'N/A')
                )

                # Check by address
                addr_normalized = device.address.lower().replace(":", "").replace("-", "")
                if tile_id_lower in addr_normalized or addr_normalized in tile_id_lower:
                    _LOGGER.info("✅ Found matching Tile by address: %s (RSSI: %s)", device.address, getattr(device, 'rssi', 'N/A'))
                    self._device = device
                    return device

                # Check by name if it contains tile ID
                if device.name and tile_id_lower[:8] in device.name.lower():
                    _LOGGER.info("✅ Found matching Tile by name: %s at %s (RSSI: %s)", device.name, device.address, getattr(device, 'rssi', 'N/A'))
                    self._device = device
                    return device

            _LOGGER.warning("❌ Tile %s not found in BLE range after scanning %d devices", self.tile_id, len(devices))
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
            _LOGGER.info("🔌 Connecting to Tile at %s...", self._device.address)
            self._client = BleakClient(self._device, timeout=self.timeout)
            await self._client.connect()

            _LOGGER.debug("📝 Subscribing to Tile response notifications...")
            # Subscribe to responses
            await self._client.start_notify(
                MEP_RESPONSE_CHAR_UUID,
                self._handle_response,
            )

            _LOGGER.info("✅ Connected to Tile %s successfully", self._device.address)
            return True

        except BleakError as err:
            _LOGGER.error("❌ Failed to connect to Tile: %s", err, exc_info=True)
            self._client = None
            return False

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
