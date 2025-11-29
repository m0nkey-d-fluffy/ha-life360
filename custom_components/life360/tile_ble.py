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

# MEP (Message Exchange Protocol) connection ID for connectionless commands
# Format: 00 (connectionless marker) + 4-byte connection ID
MEP_CONNECTION_ID = bytes.fromhex("00ffffffff")


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
    TRM = 24  # 0x18 - Tile Ring Module (the CORRECT ring command!)
    ADVINT = 15


class SongType(IntEnum):
    """Song/ring command types."""
    STOP = 0
    FIND = 1
    RING = 2


class TrmType(IntEnum):
    """TRM (Tile Ring Module) transaction types."""
    START_RING = 1  # Start ringing
    STOP_RING = 2   # Stop ringing
    READ = 3        # Read ring status


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
        on_auth_success: Any | None = None,
        known_auth_method: int | None = None,
    ) -> None:
        """Initialize Tile BLE client.

        Args:
            tile_id: The Tile device ID (MAC address or UUID)
            auth_key: 16-byte authentication key for this Tile
            timeout: Connection timeout in seconds
            on_auth_success: Optional callback(tile_id, method_number) called when auth succeeds
            known_auth_method: Optional previously successful auth method number (1-20)
        """
        if not BLEAK_AVAILABLE:
            raise RuntimeError("bleak library not available")

        self.tile_id = tile_id
        self.auth_key = auth_key
        self.timeout = timeout
        self.on_auth_success = on_auth_success
        self.known_auth_method = known_auth_method
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._response_event = asyncio.Event()
        self._response_data: bytes = b""
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._authenticated = False
        self._rand_a: bytes = b""
        self._rand_t: bytes = b""
        self._channel_key: bytes = b""
        self._channel_byte: int = 0
        self._channel_data: bytes = b""
        self._connection_id: bytes = MEP_CONNECTION_ID
        self._tx_counter: int = 0  # Counter for sent commands (cuQ in Android)
        self._rx_counter: int = 0  # Counter for received responses (cuR in Android)

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
        _LOGGER.warning("📨 === TILE RESPONSE RECEIVED ===")
        _LOGGER.warning("   From: %s", sender)
        _LOGGER.warning("   Data: %s", data.hex())
        _LOGGER.warning("   Length: %d bytes", len(data))

        # Parse response type
        if len(data) > 0:
            if data[0] == 0x00:
                _LOGGER.warning("   Type: MEP Connectionless (auth phase)")
            elif data[0] == self._channel_byte:
                _LOGGER.warning("   Type: Channel-based (channel byte: 0x%02x)", self._channel_byte)
                if len(data) > 1:
                    _LOGGER.warning("   Response cmd: 0x%02x", data[1])
            else:
                _LOGGER.warning("   Type: Unknown (first byte: 0x%02x)", data[0])

        self._response_data = data
        self._response_event.set()

        # Also put in queue for channel operations
        try:
            self._response_queue.put_nowait(data)
            _LOGGER.warning("   ✅ Response added to queue (queue size: %d)", self._response_queue.qsize())
        except asyncio.QueueFull:
            _LOGGER.warning("   ⚠️ Response queue full, discarding oldest response")
            try:
                self._response_queue.get_nowait()  # Remove oldest
                self._response_queue.put_nowait(data)  # Add new
                _LOGGER.warning("   ✅ Response added after clearing oldest")
            except Exception as e:
                _LOGGER.error("   ❌ Failed to add response to queue: %s", e)

        _LOGGER.warning("📨 === END RESPONSE ===\n")

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

        _LOGGER.warning("🔧 Sending to Tile: %s (length=%d)", data.hex(), len(data))
        _LOGGER.warning("🔧 Writing to characteristic: %s", MEP_COMMAND_CHAR_UUID)
        try:
            await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, data)
            _LOGGER.warning("🔧 Write completed successfully")
        except Exception as e:
            _LOGGER.error("❌ Failed to write to characteristic: %s", e, exc_info=True)
            return b""

        # Wait for response
        _LOGGER.warning("🔧 Waiting for response (timeout=5.0s)...")
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=5.0)
            _LOGGER.warning("🔧 Response received!")
        except asyncio.TimeoutError:
            _LOGGER.warning("⏱️ Timeout waiting for Tile response after 5 seconds")
            return b""

        return self._response_data

    async def authenticate(self) -> bool:
        """Perform TDI-based authentication handshake with Tile.

        Based on node-tile protocol implementation:
        1. Send TDI request to get Tile information
        2. Send randA (14 bytes)
        3. Receive randT and sresT from Tile
        4. Validate and complete authentication

        Returns:
            True if authentication succeeded
        """
        if not self._client or not self._client.is_connected:
            _LOGGER.error("❌ Not connected to Tile - cannot authenticate")
            return False

        try:
            _LOGGER.warning("🔐 Starting TDI-based Tile authentication handshake...")
            _LOGGER.warning("🔧 Using MEP (Message Exchange Protocol) format")

            # MEP connectionless packet format: [0x00, 0xFF, 0xFF, 0xFF, 0xFF, prefix, data]
            MEP_CONNECTIONLESS = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF])

            # Step 1: Send TDI (Tile Data Information) request
            # Command: 0x13 (19 decimal), Payload: 0x01 (request TILE_ID)
            _LOGGER.warning("🔧 Step 1: Sending TDI request for Tile information...")
            tdi_cmd = MEP_CONNECTIONLESS + bytes([0x13, 0x01])
            _LOGGER.warning("🔧 TDI command: %s (length=%d)", tdi_cmd.hex(), len(tdi_cmd))

            tdi_response = await self._send_command(tdi_cmd)
            _LOGGER.warning("🔧 TDI response: %s (length=%d)", tdi_response.hex() if tdi_response else "empty", len(tdi_response))

            if not tdi_response or len(tdi_response) < 5:
                _LOGGER.error("❌ Invalid TDI response (too short or empty)")
                return False

            # Parse TDI response (format: [0x00, 0xFF, 0xFF, 0xFF, 0xFF, response_data...])
            # Skip MEP header (5 bytes) to get to actual response
            if tdi_response.startswith(MEP_CONNECTIONLESS):
                tdi_data = tdi_response[5:]
                _LOGGER.warning("✅ TDI response received: %s", tdi_data.hex())
            else:
                _LOGGER.warning("⚠️ Unexpected TDI response format, using full response")
                tdi_data = tdi_response

            # Step 2: Generate and send randA (14 bytes for MEP-enabled Tiles)
            self._rand_a = os.urandom(14)
            _LOGGER.warning("🔧 Step 2: Sending randA (14 bytes)...")
            _LOGGER.warning("🔧 Generated randA: %s", self._rand_a.hex())

            # Command: 0x14 (20 decimal), Payload: randA (14 bytes)
            randa_cmd = MEP_CONNECTIONLESS + bytes([0x14]) + self._rand_a
            _LOGGER.warning("🔧 randA command: %s (length=%d)", randa_cmd.hex(), len(randa_cmd))

            auth_response = await self._send_command(randa_cmd)
            _LOGGER.warning("🔧 Auth response: %s (length=%d)", auth_response.hex() if auth_response else "empty", len(auth_response))

            if not auth_response or len(auth_response) < 5:
                _LOGGER.error("❌ Invalid auth response (too short or empty)")
                return False

            # Parse auth response - should contain randT and sresT
            # Skip MEP header if present
            if auth_response.startswith(MEP_CONNECTIONLESS):
                auth_data = auth_response[5:]
            else:
                auth_data = auth_response

            _LOGGER.warning("🔧 Auth data (after MEP header): %s", auth_data.hex())

            # Expected format: [response_prefix, randT, sresT, ...]
            # Based on Android app decompilation (AuthTransaction.java):
            # - Old TOA format: 1 prefix + 10 bytes randT + 4 bytes sresT = 15 bytes
            # - Even older format: 1 prefix + 8 bytes randT + 8 bytes sresT = 17 bytes
            response_prefix = auth_data[0]
            _LOGGER.warning("🔧 Response prefix: 0x%02x (command %d)", response_prefix, response_prefix)

            if len(auth_data) == 15:  # TOA format: 1 prefix + 10 randT + 4 sresT
                _LOGGER.warning("🔧 TOA format detected (10-byte randT, 4-byte sresT)")
                rand_t = auth_data[1:11]   # 10 bytes randT
                sres_t = auth_data[11:15]  # 4 bytes sresT
            elif len(auth_data) >= 17:  # Older format: 1 prefix + 8 randT + 8 sresT
                _LOGGER.warning("🔧 Older format detected (8-byte randT/sresT)")
                rand_t = auth_data[1:9]
                sres_t = auth_data[9:17]
            else:
                _LOGGER.error("❌ Auth response unexpected length: %d bytes", len(auth_data))
                _LOGGER.warning("🔧 Received auth_data breakdown:")
                for i, byte in enumerate(auth_data):
                    _LOGGER.warning("   [%d]: 0x%02x", i, byte)
                return False
            _LOGGER.warning("🔧 Received randT: %s (%d bytes)", rand_t.hex(), len(rand_t))
            _LOGGER.warning("🔧 Received sresT: %s (%d bytes)", sres_t.hex(), len(sres_t))

            # Step 3: Verify Tile's signature
            _LOGGER.warning("🔧 Step 3: Verifying Tile's signature...")

            # Try different HMAC calculations to find the correct one
            _LOGGER.warning("🔧 Trying different HMAC combinations (based on node-tile implementation)...")
            _LOGGER.warning("🔧 Auth key: %s (length=%d)", self.auth_key.hex(), len(self.auth_key))
            _LOGGER.warning("🔧 TDI response data: %s", tdi_data.hex() if tdi_data else "N/A")
            _LOGGER.warning("🔧 Response prefix: 0x%02x", response_prefix)

            # Extract potential channelData and channelPrefix from TDI response
            # TDI response format might be: [cmd, channel_prefix, channel_data...]
            channel_prefix = None
            channel_data = None
            if len(tdi_data) >= 3:
                # TDI data: [0x14, 0x01, 0x3f] - might contain channel info
                channel_prefix = tdi_data[1:2]  # 0x01
                channel_data = tdi_data[2:3]    # 0x3f
                _LOGGER.warning("🔧 Extracted channel_prefix: %s", channel_prefix.hex())
                _LOGGER.warning("🔧 Extracted channel_data: %s", channel_data.hex())

            # Android CryptoUtils.b() method with CORRECT 16-byte padding per value
            # CRITICAL: Android uses BytesUtils.e() to pad EACH value to 16 bytes BEFORE concatenating!
            # See TileBleGattCallback.java line 647-649:
            #   this.bcT = BytesUtils.e(authTransaction.KN(), 16);  // Pad randT to 16 bytes
            #   this.bcS = BytesUtils.e(this.bcS, 16);              // Pad randA to 16 bytes
            # Then CryptoUtils.b() concatenates them: 16 + 16 = 32 bytes exactly

            # Pad randA to 16 bytes (Java Arrays.copyOf behavior)
            rand_a_16 = self._rand_a + b'\x00' * (16 - len(self._rand_a))
            # Pad randT to 16 bytes
            rand_t_16 = rand_t + b'\x00' * (16 - len(rand_t))
            # Concatenate: 16 + 16 = 32 bytes
            message_32 = rand_a_16 + rand_t_16

            # Calculate HMAC with the correctly padded 32-byte message
            full_hmac_1 = hmac.new(self.auth_key, message_32, hashlib.sha256).digest()
            expected_1 = full_hmac_1[4:8]  # Extract bytes 4-7 (4 bytes)
            _LOGGER.warning("🔧 Try 1 (ANDROID CORRECT: randA→16 + randT→16 [4:8]): %s", expected_1.hex())
            _LOGGER.warning("   randA (14→16): %s", rand_a_16.hex())
            _LOGGER.warning("   randT (10→16): %s", rand_t_16.hex())
            _LOGGER.warning("   Full HMAC: %s", full_hmac_1.hex())

            _LOGGER.warning("🔧 Tile sent sresT: %s (%d bytes)", sres_t.hex(), len(sres_t))

            # Verify the signature matches
            # Method 1 is the CORRECT method based on Android app decompilation
            expected_list = [
                (expected_1, "ANDROID CORRECT: randA→16 + randT→16, HMAC[4:8]"),
            ]

            # If we have a known working method, try it first
            if self.known_auth_method and 1 <= self.known_auth_method <= len(expected_list):
                expected, desc = expected_list[self.known_auth_method - 1]
                if expected and sres_t == expected:
                    _LOGGER.warning("✅✅✅ SIGNATURE VERIFIED! Known method %d worked: %s", self.known_auth_method, desc)
                    _LOGGER.warning("✅ Fast auth using cached method!")
                    # No need to call callback since method is already cached
                else:
                    _LOGGER.warning("⚠️ Known method %d failed, trying all methods...", self.known_auth_method)
                    # Continue to try all methods below

            # Try all methods if known method failed or wasn't provided
            if not self._authenticated:  # Only try if not already authenticated above
                for i, (expected, desc) in enumerate(expected_list, 1):
                    if expected and sres_t == expected:
                        _LOGGER.warning("✅✅✅ SIGNATURE VERIFIED! Method %d: %s", i, desc)
                        _LOGGER.warning("✅ This is the correct HMAC calculation method!")

                        # Call the callback to store the successful auth method
                        if self.on_auth_success:
                            try:
                                self.on_auth_success(self.tile_id, i)
                                _LOGGER.info("✅ Stored auth method %d for tile %s", i, self.tile_id[:8])
                            except Exception as callback_err:
                                _LOGGER.warning("Failed to store auth method: %s", callback_err)

                        break
                else:
                    _LOGGER.error("❌ Tile signature mismatch! None of the 23 methods worked.")
                    _LOGGER.error("❌ DIAGNOSIS:")
                    _LOGGER.error("   1. Auth key source: Life360 API (base64 decoded)")
                    _LOGGER.error("   2. Auth key bytes: %s", self.auth_key.hex())
                    _LOGGER.error("   3. Auth key length: %d bytes", len(self.auth_key))
                    _LOGGER.error("   4. Expected sresT: %s", sres_t.hex())
                    _LOGGER.error("")
                    _LOGGER.error("💡 NEXT STEPS TO TRY:")
                    _LOGGER.error("   A. Verify auth key is correct by checking Tile API directly")
                    _LOGGER.error("   B. Check if Life360's auth key matches Tile's auth key")
                    _LOGGER.error("   C. Try accessing Tile API directly instead of Life360 API")
                    _LOGGER.error("   D. Examine if randA size (14 bytes) is correct for your Tile model")
                    _LOGGER.error("   E. Check if TDI-based auth is correct approach (vs regular AUTH)")
                    return False

            # Step 4: Authentication complete
            self._authenticated = True
            self._rand_t = rand_t

            _LOGGER.warning("✅ TDI-based authentication successful!")

            # Clear response queue from authentication phase to avoid getting old responses
            cleared = 0
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                    cleared += 1
                except:
                    break
            if cleared > 0:
                _LOGGER.warning("🔧 Cleared %d old responses from queue", cleared)

            # Step 5: Open channel to get channel data for encryption key
            _LOGGER.warning("🔧 Step 4: Opening communication channel...")
            channel_data = await self._open_channel()
            if not channel_data:
                _LOGGER.error("❌ Channel open failed")
                return False

            # Step 6: Derive channel encryption key from channel data
            self._channel_key = self._derive_channel_encryption_key(channel_data)
            _LOGGER.warning("✅ Channel encryption key derived: %s", self._channel_key.hex())

            # Step 7: Establish channel with signed command
            # BLE capture shows this is CRITICAL - Frame 289-291
            _LOGGER.warning("🔧 Step 5: Establishing communication channel...")
            if not await self._establish_channel():
                _LOGGER.error("❌ Channel establishment failed")
                return False

            _LOGGER.warning("✅ Channel established!")

            # Step 8: Update connection parameters for optimal ringing
            # BLE capture shows this BEFORE ring command - Frame 300
            _LOGGER.warning("🔧 Step 6: Updating connection parameters...")
            if not await self._update_connection_params():
                _LOGGER.warning("⚠️ Connection parameter update failed (continuing anyway)")
                # Don't fail - connection update might not be critical

            _LOGGER.warning("✅ Ready for ring command!")
            return True

        except Exception as err:
            _LOGGER.error("❌ Authentication error: %s", err, exc_info=True)
            return False

    async def _open_channel(self) -> dict[str, any] | None:
        """Open communication channel after authentication.

        Sends CHANNEL OPEN command (0x10) with randA as payload.
        Receives channel_byte and channel_data needed for encryption key derivation.

        Based on BLE capture frame 287-288:
        - Request: 00f3c7d22d10 + randA (14 bytes)
        - Response: 00f3c7d22d12 + channel_byte (1 byte) + channel_data (13 bytes)

        Returns:
            Dict with 'channel_byte' and 'channel_data', or None on failure
        """
        try:
            CHANNEL_OPEN_CMD = 0x10  # 16 decimal

            # Build MEP connectionless command
            cmd = MEP_CONNECTION_ID + bytes([CHANNEL_OPEN_CMD]) + self._rand_a

            _LOGGER.warning("🔧 Channel open command: %s (length=%d)", cmd.hex(), len(cmd))
            _LOGGER.warning("   Connection ID: %s", MEP_CONNECTION_ID.hex())
            _LOGGER.warning("   Command: 0x%02x", CHANNEL_OPEN_CMD)
            _LOGGER.warning("   RandA: %s", self._rand_a.hex())

            if not self._client or not self._client.is_connected:
                raise RuntimeError("Not connected to Tile")

            # Send command and wait for response
            await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, cmd)
            _LOGGER.warning("🔧 Channel open command sent, waiting for response...")

            response = await asyncio.wait_for(
                self._response_queue.get(), timeout=5.0
            )

            _LOGGER.warning("🔧 Channel open response: %s (length=%d)", response.hex(), len(response))

            # Parse response: MEP_ID (5 bytes) + response_byte (1 byte) + channel_byte + channel_data
            if len(response) < 7:  # Min: 5 (MEP) + 1 (response) + 1 (channel) + 0+ (data)
                raise ValueError(f"Response too short: {len(response)} bytes")

            # Verify MEP header (5 bytes) and response byte (should be 0x12 for channel open response)
            expected_header = MEP_CONNECTION_ID + bytes([0x12])  # 6 bytes total
            if response[:6] != expected_header:
                actual = response[:6].hex()
                expected = expected_header.hex()
                raise ValueError(f"Invalid response header: got {actual}, expected {expected}")

            # Extract channel byte and channel data
            channel_byte = response[6]
            channel_data = response[7:]

            _LOGGER.warning("✅ Channel opened successfully!")
            _LOGGER.warning("   Channel byte: 0x%02x", channel_byte)
            _LOGGER.warning("   Channel data: %s (%d bytes)", channel_data.hex(), len(channel_data))

            return {
                "channel_byte": channel_byte,
                "channel_data": channel_data,
            }

        except asyncio.TimeoutError:
            _LOGGER.error("❌ Channel open timeout (5 seconds)")
            return None
        except Exception as err:
            _LOGGER.error("❌ Channel open error: %s", err, exc_info=True)
            return None

    async def _establish_channel(self) -> bool:
        """Establish communication channel after opening.

        Based on BLE capture: sends command 0x12 (CHANNEL) with payload 0x13 (TDI).
        Format: [channel_byte, command, payload, 4-byte-hmac]
        Example: 02 12 13 e15b25de

        The Tile responds with: [channel_byte, 0x01, 0x0e, data, hmac]
        Example response: 02 01 0e 2dde220000000013e1f19a1d

        Returns:
            True if channel command sent successfully
        """
        try:
            CHANNEL_CMD = 0x12  # 18 decimal
            CHANNEL_PAYLOAD = 0x13  # 19 decimal (TDI)

            # Build command: channel_byte + command + payload
            cmd_data = bytes([self._channel_byte, CHANNEL_CMD, CHANNEL_PAYLOAD])

            # CRITICAL: Android ToaProcessor.d() increments counter FIRST (line 54), then uses it (line 58)
            # So: first command uses counter=1, second uses counter=2, etc.
            # This matches the increment-before-use pattern
            self._tx_counter += 1

            # CRITICAL: HMAC is calculated over [command, payload] WITHOUT channel byte!
            # ToaProcessor.d() calculates HMAC, then ToaMepProcessor.m() ADDS channel byte
            # Line 56-58: bArr3 = [command, payload], HMAC over bArr3
            # Line 35-38: ToaMepProcessor.m() adds channel byte AFTER
            sig_data = self._build_hmac_message(
                self._tx_counter,
                bytes([CHANNEL_CMD, CHANNEL_PAYLOAD])
            )
            signature = hmac.new(self._channel_key, sig_data, hashlib.sha256).digest()[:4]

            # Final command: cmd_data + signature
            cmd = cmd_data + signature

            _LOGGER.warning("🔧 Channel establishment command:")
            _LOGGER.warning("   TX counter: %d (incremented before use)", self._tx_counter)
            _LOGGER.warning("   HMAC data: %s", sig_data.hex())
            _LOGGER.warning("   Signature: %s", signature.hex())
            _LOGGER.warning("   Final command: %s (length=%d)", cmd.hex(), len(cmd))

            # Send the command directly without waiting for response
            # Channel establishment is a "fire and forget" command
            if not self._client or not self._client.is_connected:
                raise RuntimeError("Not connected to Tile")

            _LOGGER.warning("🔧 Sending to Tile: %s", cmd.hex())
            _LOGGER.warning("🔧 Sending channel establishment at: %.3f", asyncio.get_event_loop().time())
            await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, cmd)
            _LOGGER.warning("🔧 Channel establishment write completed at: %.3f", asyncio.get_event_loop().time())
            _LOGGER.warning("📥 Waiting for channel establishment response...")
            _LOGGER.warning("   Queue size before wait: %d", self._response_queue.qsize())
            _LOGGER.warning("   Timeout: 2.0 seconds")
            _LOGGER.warning("   Expected response format: [channel_byte=0x%02x, 0x01, data..., hmac(4)]", self._channel_byte)
            _LOGGER.warning("   🔍 NOTE: If no response arrives, check if Tile firmware rejects HMAC or if BLE notifications failed")

            # Wait for channel establishment response
            # BLE capture shows Tile responds with: 02 01 0e [data] [hmac]
            try:
                response = await asyncio.wait_for(
                    self._response_queue.get(), timeout=2.0
                )
                _LOGGER.warning("✅ Channel establishment response received: %s (length=%d)", response.hex(), len(response))

                # Verify it's a channel response (starts with channel_byte + 0x01)
                if len(response) >= 2 and response[0] == self._channel_byte and response[1] == 0x01:
                    _LOGGER.warning("✅ Channel establishment confirmed by Tile")

                    # Verify HMAC signature on the response
                    # This is CRITICAL - the Tile expects us to verify responses!
                    verified, data = self._verify_response_hmac(response)
                    if verified:
                        _LOGGER.warning("✅✅✅ Channel establishment response HMAC verified!")
                    else:
                        _LOGGER.error("❌ Channel establishment response HMAC verification FAILED!")
                        _LOGGER.error("   This might cause the Tile to reject subsequent commands")
                        # Don't return False - continue anyway to see what happens
                else:
                    _LOGGER.warning("⚠️ Unexpected response format: %s", response.hex())

            except asyncio.TimeoutError:
                _LOGGER.warning("⚠️ No response to channel establishment (2s timeout)")
                _LOGGER.warning("   This is CRITICAL - Tile should respond within 100ms")
                _LOGGER.warning("   Check if notifications are being received!")
                # Continue anyway - some Tiles might not respond

            return True

        except Exception as err:
            _LOGGER.error("❌ Channel establishment error: %s", err, exc_info=True)
            return False

    async def _update_connection_params(self) -> bool:
        """Update BLE connection parameters for ringing.

        Based on BLE capture frame 300 and Android BleConnParameters.
        Sets high-latency parameters: 288-304ms intervals, latency 4, timeout 6000ms.

        Android uses BleConnParameters(288, 304, 4, 600) before ringing for
        more reliable command delivery.

        Returns:
            True if update sent successfully
        """
        try:
            TCU_CMD = 0x0c  # 12 decimal - Tile Connection Update
            TCU_SET = 0x03  # Transaction: set parameters

            # Connection parameters (from Android bch high-latency config)
            min_interval = 288  # * 1.25ms = 360ms
            max_interval = 304  # * 1.25ms = 380ms
            latency = 4         # Slave latency
            timeout = 600       # * 10ms = 6000ms

            # Build parameters (little-endian 16-bit values)
            params = (
                min_interval.to_bytes(2, 'little') +
                max_interval.to_bytes(2, 'little') +
                latency.to_bytes(2, 'little') +
                timeout.to_bytes(2, 'little') +
                bytes([0x0e])  # Flags from BLE capture
            )

            # Build command payload: command + transaction + params
            cmd_payload = bytes([TCU_CMD, TCU_SET]) + params

            # Increment TX counter FIRST (Android: line 54), then use for HMAC (line 58)
            self._tx_counter += 1

            # Calculate HMAC signature - HMAC over [command, payload] WITHOUT channel byte
            # Android: ToaProcessor.d() calculates HMAC, then ToaMepProcessor.m() adds channel byte
            sig_data = self._build_hmac_message(
                self._tx_counter,
                cmd_payload
            )
            signature = hmac.new(self._channel_key, sig_data, hashlib.sha256).digest()[:4]

            # Final command: channel_byte + payload + signature
            cmd = bytes([self._channel_byte]) + cmd_payload + signature

            _LOGGER.warning("🔧 Updating BLE connection parameters:")
            _LOGGER.warning("   Intervals: %d-%dms, Latency: %d, Timeout: %dms",
                          int(min_interval * 1.25), int(max_interval * 1.25),
                          latency, timeout * 10)
            _LOGGER.warning("   TX counter: %d", self._tx_counter)
            _LOGGER.warning("   Command: %s", cmd.hex())

            await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, cmd)
            _LOGGER.warning("✅ Connection parameters update sent")

            # Wait briefly for parameter update to take effect
            await asyncio.sleep(0.3)

            return True

        except Exception as err:
            _LOGGER.error("❌ Connection update error: %s", err, exc_info=True)
            return False

    async def _read_song_features(self) -> bool:
        """Read available song/ring features from the Tile.

        Based on BLE capture frame 310 which shows this happens before ring command.
        Command: 02 05 06 [HMAC]
        - 05 = SONG command
        - 06 = Transaction type 0x06 (Read features)

        Android code: Line 912 Kc() - b((byte) 5, new SongTransaction((byte) 6).Lc())

        Returns:
            True if command sent successfully
        """
        try:
            SONG_CMD = 0x05
            SONG_READ_FEATURES = 0x06  # Read available features/songs

            # Build command payload
            cmd_payload = bytes([SONG_CMD, SONG_READ_FEATURES])

            # Increment TX counter
            self._tx_counter += 1

            # Calculate HMAC signature
            sig_data = self._build_hmac_message(
                self._tx_counter,
                cmd_payload
            )
            signature = hmac.new(self._channel_key, sig_data, hashlib.sha256).digest()[:4]

            # Final command: channel_byte + payload + signature
            cmd = bytes([self._channel_byte]) + cmd_payload + signature

            _LOGGER.warning("🎵 Reading song features...")
            _LOGGER.warning("   TX counter: %d", self._tx_counter)
            _LOGGER.warning("   Command: %s", cmd.hex())

            await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, cmd)
            _LOGGER.warning("✅ Song features read command sent")

            # Don't wait for response - Tile may or may not respond
            # BLE capture shows this is "fire and forget"
            await asyncio.sleep(0.1)

            return True

        except Exception as err:
            _LOGGER.error("❌ Song features read error: %s", err, exc_info=True)
            return False

    def _compute_sres(
        self, rand1: bytes, rand2: bytes, key: bytes
    ) -> bytes:
        """Compute SRES (Signed Response) value.

        Args:
            rand1: First random value
            rand2: Second random value
            key: Authentication key (16 bytes)

        Returns:
            8-byte SRES value
        """
        # HMAC-SHA256, truncated to 8 bytes
        msg = rand1 + rand2
        h = hmac.new(key, msg, hashlib.sha256)
        return h.digest()[:8]

    def _compute_sres_padded(
        self, rand1: bytes, rand2: bytes, key: bytes
    ) -> bytes:
        """Compute SRES with 32-byte padding (node-tile method).

        Based on node-tile's CryptoUtils.generateHmac which pads to 32 bytes.

        Args:
            rand1: First random value
            rand2: Second random value
            key: Authentication key (16 bytes)

        Returns:
            8-byte SRES value
        """
        # Concatenate rand1 and rand2, then pad to 32 bytes
        msg = rand1 + rand2
        # Pad with zeros to 32 bytes
        if len(msg) < 32:
            msg = msg + bytes(32 - len(msg))

        h = hmac.new(key, msg, hashlib.sha256)
        return h.digest()[:8]

    def _derive_channel_encryption_key(self, channel_data: dict) -> bytes:
        """Derive channel encryption key from channel opening data.

        Based on Android BaseBleGattCallback.Il() when HR() is true:
        Returns HMAC-SHA256(authKey, randA + channel_data + channel_byte + connection_id)[:16]

        This is the key used for HMAC signatures on all channel commands.

        Args:
            channel_data: Dict with 'channel_byte' and 'channel_data' from channel open response

        Returns:
            16-byte channel encryption key
        """
        # Store channel info for later use
        self._channel_byte = channel_data["channel_byte"]
        self._channel_data = channel_data["channel_data"]

        # Build message: randA + channel_data + channel_byte + connection_id
        message = (
            self._rand_a +
            self._channel_data +
            bytes([self._channel_byte]) +
            self._connection_id
        )

        _LOGGER.warning("🔧 Deriving channel encryption key:")
        _LOGGER.warning("   RandA (%d): %s", len(self._rand_a), self._rand_a.hex())
        _LOGGER.warning("   Channel data (%d): %s", len(self._channel_data), self._channel_data.hex())
        _LOGGER.warning("   Channel byte: 0x%02x", self._channel_byte)
        _LOGGER.warning("   Connection ID (%d): %s", len(self._connection_id), self._connection_id.hex())
        _LOGGER.warning("   Total message (%d): %s", len(message), message.hex())

        # HMAC-SHA256, take first 16 bytes as channel encryption key
        h = hmac.new(self.auth_key, message, hashlib.sha256)
        return h.digest()[:16]

    def _build_hmac_message(self, counter: int, cmd_data: bytes, is_rx: bool = False) -> bytes:
        """Build message for HMAC signature calculation.

        Based on Android ToaProcessor.d() and CryptoUtils.b():
        - TX (outgoing): counter_bytes + {1} + length_byte + cmd_data (padded to 32 bytes)
        - RX (incoming): counter_bytes + {0} + length_byte + cmd_data (padded to 32 bytes)

        CRITICAL: cmd_data is [command, payload...] WITHOUT channel byte!
        Android flow:
          1. ToaProcessor.d() calculates HMAC over [command, payload]
          2. ToaMepProcessor.m() ADDS channel byte after HMAC calculation

        Args:
            counter: Transaction counter
            cmd_data: Command data [command, payload...] WITHOUT channel byte
            is_rx: True for received responses, False for sent commands

        Returns:
            32-byte message for HMAC calculation
        """
        # Convert counter to 4-byte little-endian (BytesUtils.au())
        counter_bytes = counter.to_bytes(4, byteorder='little')

        # Build message: counter + {1 for TX, 0 for RX} + length + data
        direction_byte = bytes([0]) if is_rx else bytes([1])
        message = counter_bytes + direction_byte + bytes([len(cmd_data)]) + cmd_data

        # Pad to 32 bytes
        if len(message) < 32:
            message = message + bytes(32 - len(message))

        return message

    def _verify_response_hmac(self, response: bytes) -> tuple[bool, bytes]:
        """Verify HMAC signature on a received response.

        Based on Android ToaProcessor.a(ToaTransaction, boolean).
        Response format: [channel_byte, data..., hmac(4 bytes)]

        Args:
            response: Full response including channel byte and HMAC

        Returns:
            Tuple of (verified: bool, data_without_hmac: bytes)
        """
        if len(response) < 6:  # Minimum: channel + 1 byte data + 4 byte HMAC
            _LOGGER.warning("⚠️ Response too short for HMAC verification: %d bytes", len(response))
            return (False, response)

        # Split response: [channel_byte, data..., hmac]
        # Last 4 bytes are HMAC, everything before is data
        data_with_channel = response[:-4]
        received_hmac = response[-4:]

        # Data without channel byte (for HMAC calculation)
        # ToaProcessor removes channel byte before HMAC calculation
        data_without_channel = data_with_channel[1:]

        # Increment RX counter BEFORE verification
        # This matches Android: line 100 increments cuR, then line 101 calls HMAC verification
        self._rx_counter += 1

        # Calculate expected HMAC using RX counter
        hmac_msg = self._build_hmac_message(self._rx_counter, data_without_channel, is_rx=True)
        expected_hmac = hmac.new(self._channel_key, hmac_msg, hashlib.sha256).digest()[:4]

        _LOGGER.warning("🔧 RX HMAC verification:")
        _LOGGER.warning("   RX counter: %d", self._rx_counter)
        _LOGGER.warning("   Data (no channel): %s", data_without_channel.hex())
        _LOGGER.warning("   HMAC message: %s", hmac_msg.hex())
        _LOGGER.warning("   Expected HMAC: %s", expected_hmac.hex())
        _LOGGER.warning("   Received HMAC: %s", received_hmac.hex())

        # Verify HMAC matches
        if expected_hmac == received_hmac:
            _LOGGER.warning("✅ RX HMAC verified!")
            return (True, data_with_channel)
        else:
            _LOGGER.error("❌ RX HMAC mismatch!")
            # Decrement counter since verification failed
            self._rx_counter -= 1
            return (False, data_with_channel)

    def _build_ring_command(
        self,
        volume: TileVolume = TileVolume.MED,
        duration_seconds: int = 30,
    ) -> bytes:
        """Build the ring/find command using channel-based SONG command.

        Based on BLE capture frame 314: 02 05 02 01 03 1e [4-byte-hmac]
        - Channel byte: 0x02
        - Command: 0x05 (SONG)
        - Transaction: 0x02 (PLAY/RING)
        - Flags: 0x01
        - Volume level: volume value (1=LOW, 2=MED, 3=HIGH)
        - Duration: seconds
        - HMAC: 4-byte signature

        Args:
            volume: Ring volume level
            duration_seconds: How long to ring

        Returns:
            Command bytes (channel-based with HMAC)
        """
        SONG_CMD = 0x05
        SONG_PLAY = 0x02  # Play/ring transaction
        SONG_FLAGS = 0x01  # Standard flags from BLE capture

        # Build command data (without channel byte or HMAC)
        # This is what goes into the HMAC calculation
        cmd_payload = bytes([
            SONG_CMD,
            SONG_PLAY,
            SONG_FLAGS,
            volume.value,  # Volume level (1-3)
            duration_seconds,  # Duration in seconds
        ])

        # Increment TX counter FIRST (Android: line 54), then use for HMAC (line 58)
        self._tx_counter += 1

        # Calculate HMAC signature using channel encryption key
        # Based on ToaProcessor.d(): HMAC over counter + {1} + length + [command, payload]
        # CRITICAL: HMAC does NOT include channel byte (added later by ToaMepProcessor.m())
        sig_data = self._build_hmac_message(
            self._tx_counter,
            cmd_payload
        )
        signature = hmac.new(self._channel_key, sig_data, hashlib.sha256).digest()[:4]

        # Build final command: channel_byte + cmd_payload + signature
        cmd = bytes([self._channel_byte]) + cmd_payload + signature

        _LOGGER.warning("🔧 Ring command built:")
        _LOGGER.warning("   TX counter: %d", self._tx_counter)
        _LOGGER.warning("   Channel byte: 0x%02x", self._channel_byte)
        _LOGGER.warning("   Command payload: %s", cmd_payload.hex())
        _LOGGER.warning("   HMAC message: %s", sig_data.hex())
        _LOGGER.warning("   Signature: %s", signature.hex())
        _LOGGER.warning("   Final command: %s", cmd.hex())

        return cmd

    def _build_stop_command(self) -> bytes:
        """Build the stop ring command using TRM (Tile Ring Module).

        Returns:
            Command bytes (MEP-wrapped)
        """
        # MEP header for connectionless commands
        MEP_CONNECTIONLESS = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF])

        # TRM STOP_RING command (no data payload needed)
        cmd_payload = bytes([ToaCommand.TRM, TrmType.STOP_RING])

        # Wrap in MEP format
        cmd = MEP_CONNECTIONLESS + cmd_payload
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
            # Read available song features (BLE capture Frame 310 at t=7.377s)
            # This happens BEFORE the ring command in the capture
            _LOGGER.warning("🎵 Querying song features before ring...")
            if not await self._read_song_features():
                _LOGGER.warning("⚠️ Song features read failed (continuing anyway)")

            # Wait for Tile to be ready for ring command
            # BLE capture shows 2.79s delay between song features and ring (Frame 310 → 314)
            # This allows connection parameters to take effect and Tile to process commands
            _LOGGER.warning("⏳ Waiting 2.5s for Tile to be ready for ring command...")
            await asyncio.sleep(2.5)

            _LOGGER.warning("🔔 Sending ring command (volume=%s, duration=%ds)...", volume.name, duration_seconds)
            cmd = self._build_ring_command(volume, duration_seconds)

            # Send ring command directly without waiting for response
            # Ring commands are "fire and forget" - Tile doesn't respond
            if not self._client or not self._client.is_connected:
                raise RuntimeError("Not connected to Tile")

            _LOGGER.warning("🔔 Ring command: %s", cmd.hex())
            await self._client.write_gatt_char(MEP_COMMAND_CHAR_UUID, cmd)
            _LOGGER.warning("✅ Ring command sent successfully - Tile should be ringing!")

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
    on_auth_success: Any | None = None,
    known_auth_method: int | None = None,
) -> bool:
    """High-level function to ring a Tile via BLE.

    Args:
        tile_id: Tile device ID or MAC address
        auth_key: 16-byte auth key or hex string
        volume: Ring volume level
        duration_seconds: Ring duration
        scan_timeout: BLE scan timeout
        on_auth_success: Optional callback(tile_id, method_number) called when auth succeeds
        known_auth_method: Optional previously successful auth method number (1-20)

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

    client = TileBleClient(
        tile_id,
        auth_key,
        on_auth_success=on_auth_success,
        known_auth_method=known_auth_method,
    )

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

