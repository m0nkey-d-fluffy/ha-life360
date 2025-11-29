# Tile BLE Technical Documentation
## Complete Tile Over Air (TOA) Protocol Implementation

---

## Table of Contents

1. [Overview](#overview)
2. [BLE Characteristics & UUIDs](#ble-characteristics--uuids)
3. [Protocol Layers](#protocol-layers)
4. [Authentication Flow](#authentication-flow)
5. [Channel Establishment](#channel-establishment)
6. [Command Sequence](#command-sequence)
7. [HMAC Signature Calculation](#hmac-signature-calculation)
8. [Counter Synchronization](#counter-synchronization)
9. [Ring Command Details](#ring-command-details)
10. [Error Handling](#error-handling)
11. [Implementation Reference](#implementation-reference)

---

## Overview

The Tile BLE protocol (officially called **TOA** - Tile Over Air) is a proprietary protocol used by Tile trackers to communicate over Bluetooth Low Energy. This document describes the complete protocol as reverse-engineered from the Tile Android app v2.140.0 and BLE packet captures.

**Key Features:**
- **HMAC-SHA256 security** for all authenticated commands
- **Counter-based replay protection** embedded in HMAC
- **Channel-based encryption** after authentication
- **Multiple authentication methods** (20 different HMAC constructions)
- **Connectionless and channel-based** command modes

**Protocol Version:** As of 2025, this is based on Tile firmware compatible with Android app v2.140.0

---

## BLE Characteristics & UUIDs

### Service UUID
```
TILE_SERVICE_UUID = 0000feed-0000-1000-8000-00805f9b34fb
```

### Characteristics

| UUID | Name | Properties | Purpose |
|------|------|------------|---------|
| `9d410018-35d6-f4dd-ba60-e7bd8dc491c0` | MEP Command | Write | Send commands to Tile |
| `9d410019-35d6-f4dd-ba60-e7bd8dc491c0` | MEP Response | Notify | Receive responses from Tile |
| `9d410007-35d6-f4dd-ba60-e7bd8dc491c0` | Tile ID | Read | Read Tile's identifier |

### MEP (Message Exchange Protocol)

MEP is the packet format used for both commands and responses. There are two modes:

**Connectionless Mode** (before authentication):
```
Format: 0x00 + connection_id(4) + command(1) + payload(N)
Example: 00 12345678 01 [auth_data]
```

**Channel Mode** (after authentication):
```
Format: channel_byte(1) + command(1) + payload(N) + hmac(4)
Example: 02 05 02 01 03 1e [hmac_4_bytes]
```

---

## Protocol Layers

The Tile BLE protocol has three distinct layers:

### Layer 1: BLE Connection
- Standard BLE GATT connection
- Service discovery
- Characteristic subscription (MEP Response notifications)

### Layer 2: TOA Authentication
- Connectionless commands only
- HMAC-based challenge-response
- Establishes shared secrets (auth_key → channel_key)
- Creates encrypted channel

### Layer 3: Channel Commands
- All commands after authentication
- HMAC signature on every command
- Counter-based replay protection
- Encrypted command/response

---

## Authentication Flow

### Step 1: TDI (Tile Device Info) Requests

Before authentication, request device information using connectionless TDI commands:

```python
# TDI command structure (connectionless)
command_byte = 0x03  # TDI command
transaction_type = 0x01  # TILE_ID
transaction_type = 0x03  # FIRMWARE
transaction_type = 0x04  # MODEL
transaction_type = 0x05  # HARDWARE

# MEP format
packet = bytes([0x00]) + connection_id + bytes([0x03, transaction_type])
```

**Purpose:** These provide basic device information but are optional for ring functionality.

### Step 2: Authentication Challenge

Send authentication request with random challenge (rand_a):

```python
# Generate random 8-byte challenge
rand_a = secrets.token_bytes(8)

# Build auth command (connectionless)
AUTH_CMD = 0x01
packet = bytes([0x00]) + connection_id + bytes([AUTH_CMD]) + rand_a
```

### Step 3: Authentication Response

Tile responds with:
```
Format: rand_b(8) + tile_mac(16)
Total: 24 bytes
```

Where:
- `rand_b`: Tile's 8-byte random challenge
- `tile_mac`: Tile's HMAC signature proving it knows the auth_key

### Step 4: Verify Tile & Respond

Calculate expected Tile MAC and verify:

```python
# HMAC message structure for Tile's MAC
message = rand_b + rand_a  # Note the order!
expected_mac = hmac.new(auth_key, message, hashlib.sha256).digest()

if tile_mac == expected_mac[:16]:
    # Tile is authentic!
    # Calculate our response MAC
    our_message = rand_a + rand_b  # Reversed order
    our_mac = hmac.new(auth_key, our_message, hashlib.sha256).digest()

    # Send our MAC (connectionless)
    packet = bytes([0x00]) + connection_id + bytes([0x01]) + our_mac[:16]
```

### Step 5: Derive Channel Key

After successful mutual authentication, derive the channel encryption key:

```python
# Channel key derivation
channel_key_message = rand_a + rand_b + bytes([0x00])
channel_key = hmac.new(auth_key, channel_key_message, hashlib.sha256).digest()[:16]
```

**Critical:** The channel key is 16 bytes (128 bits).

---

## Channel Establishment

After authentication, establish an encrypted channel:

### Channel OPEN Request (Connectionless)
```python
# Request channel opening
CHANNEL_CMD = 0x02  # Channel command
CHANNEL_OPEN = 0x01  # Open sub-command

packet = bytes([0x00]) + connection_id + bytes([CHANNEL_CMD, CHANNEL_OPEN])
```

### Channel Establishment (First Channel Command)

```python
# This is the FIRST command using channel protocol
# Counter starts at 1
CHANNEL_ESTABLISH_CMD = 0x12
CHANNEL_ESTABLISH_DATA = 0x13

counter = 1
channel_byte = 0x02  # Received from Tile's channel OPEN response

# Build command payload
cmd_payload = bytes([CHANNEL_ESTABLISH_CMD, CHANNEL_ESTABLISH_DATA])

# Calculate HMAC
hmac_message = (
    counter.to_bytes(8, 'little') +  # 8-byte counter
    bytes([0x01]) +                   # Direction (0x01 = TX)
    bytes([len(cmd_payload)]) +       # Payload length
    cmd_payload                        # Command data
)
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()[:4]

# Final command
command = bytes([channel_byte]) + cmd_payload + signature
```

**Result:** Channel is now established. All subsequent commands use channel protocol.

---

## Command Sequence

### The Critical Discovery: Counter Synchronization

**Problem:** The Tile firmware tracks a receive (RX) counter independently. The counter value is **embedded in the HMAC** but **not sent in the packet**. This means both sides must stay perfectly synchronized.

**Solution:** Send ALL intermediate commands that the Tile expects, in the exact order.

### Complete Ring Sequence

Based on BLE captures from the official Android app:

| Step | Counter | Command | Purpose |
|------|---------|---------|---------|
| 1 | 1 | Channel Establish (0x12 0x13) | Open encrypted channel |
| 2 | 2 | TDG Diagnostic (0x0a 0x01) | Read diagnostics |
| 3 | 3 | AdvInt (0x07 0x02) | Read advertisement interval |
| 4 | 4 | TCU (0x0c 0x03 + params) | Update connection parameters |
| 5 | 5 | SONG READ_FEATURES (0x05 0x06) | Read audio capabilities |
| 6 | 6 | **SONG PLAY/RING** (0x05 0x02 + params) | **Ring the Tile!** |

**Why this matters:**
- If you skip any intermediate command, your counter will desync from the Tile's counter
- The Tile will reject your ring command with silent HMAC failure
- There's no error message - the Tile just ignores the command

### Implementation of Each Command

#### Command 2: TDG Diagnostic
```python
TDG_CMD = 0x0a
TDG_READ = 0x01

counter += 1  # Now counter = 2
cmd_payload = bytes([TDG_CMD, TDG_READ])

hmac_message = (
    counter.to_bytes(8, 'little') +
    bytes([0x01, len(cmd_payload)]) +
    cmd_payload
)
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()[:4]
command = bytes([channel_byte]) + cmd_payload + signature
```

#### Command 3: AdvInt
```python
ADVINT_CMD = 0x07
ADVINT_READ = 0x02

counter += 1  # Now counter = 3
cmd_payload = bytes([ADVINT_CMD, ADVINT_READ])

hmac_message = (
    counter.to_bytes(8, 'little') +
    bytes([0x01, len(cmd_payload)]) +
    cmd_payload
)
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()[:4]
command = bytes([channel_byte]) + cmd_payload + signature
```

#### Command 4: TCU (Connection Update)
```python
TCU_CMD = 0x0c
TCU_SET = 0x03

# Connection parameters (from BLE capture)
params = bytes([
    0x20, 0x01,  # Min interval: 0x0120 = 288 * 1.25ms = 360ms
    0x30, 0x01,  # Max interval: 0x0130 = 304 * 1.25ms = 380ms
    0x04, 0x00,  # Slave latency: 4
    0x58, 0x02,  # Supervision timeout: 0x0258 = 600 * 10ms = 6s
    0x0e,        # Unknown parameter
])

counter += 1  # Now counter = 4
cmd_payload = bytes([TCU_CMD, TCU_SET]) + params

hmac_message = (
    counter.to_bytes(8, 'little') +
    bytes([0x01, len(cmd_payload)]) +
    cmd_payload
)
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()[:4]
command = bytes([channel_byte]) + cmd_payload + signature
```

#### Command 5: SONG READ_FEATURES
```python
SONG_CMD = 0x05
SONG_READ_FEATURES = 0x06

counter += 1  # Now counter = 5
cmd_payload = bytes([SONG_CMD, SONG_READ_FEATURES])

hmac_message = (
    counter.to_bytes(8, 'little') +
    bytes([0x01, len(cmd_payload)]) +
    cmd_payload
)
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()[:4]
command = bytes([channel_byte]) + cmd_payload + signature
```

#### Command 6: SONG PLAY (RING!)
```python
SONG_CMD = 0x05
SONG_PLAY = 0x02
SONG_FLAGS = 0x01  # Standard flags

volume = 2  # 1=low, 2=medium, 3=high
duration = 30  # seconds

counter += 1  # Now counter = 6 ✅
cmd_payload = bytes([SONG_CMD, SONG_PLAY, SONG_FLAGS, volume, duration])

hmac_message = (
    counter.to_bytes(8, 'little') +
    bytes([0x01, len(cmd_payload)]) +
    cmd_payload
)
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()[:4]
command = bytes([channel_byte]) + cmd_payload + signature

# 🔔 Tile rings!
```

---

## HMAC Signature Calculation

### HMAC Message Structure

For **channel-based commands** (after authentication):

```python
hmac_message = (
    counter.to_bytes(8, byteorder='little') +  # 8 bytes: TX counter
    bytes([0x01]) +                             # 1 byte: Direction (TX=0x01, RX=0x02)
    bytes([payload_length]) +                   # 1 byte: Payload length
    cmd_payload                                 # N bytes: Command + data
)

# Pad to 32 bytes with zeros
if len(hmac_message) < 32:
    hmac_message += bytes(32 - len(hmac_message))

# Calculate HMAC-SHA256
signature = hmac.new(channel_key, hmac_message, hashlib.sha256).digest()

# Use first 4 bytes as signature
signature_4bytes = signature[:4]
```

### For Authentication (Connectionless)

```python
# Tile verifying us
message = rand_a + rand_b
mac = hmac.new(auth_key, message, hashlib.sha256).digest()[:16]

# Us verifying Tile
message = rand_b + rand_a  # Note: reversed!
mac = hmac.new(auth_key, message, hashlib.sha256).digest()[:16]
```

---

## Counter Synchronization

### How Counters Work

**TX Counter (Transmit):**
- Maintained by the sender
- Incremented **before** sending each channel command
- Starts at 0, first command uses counter=1

**RX Counter (Receive):**
- Maintained by the receiver (Tile firmware)
- Incremented when a valid command is **received**
- Tile validates HMAC using its RX counter

**Critical Rule:** The counter is **embedded in HMAC**, not sent separately!

### Counter Flow Example

```
Our Side:                           Tile Side:
TX counter = 0                      RX counter = 0

-- Channel Establishment --
TX++ → 1                            Receives packet
Build HMAC(counter=1, ...)          Expects HMAC(counter=1, ...)
Send command                        RX++ → 1
                                    ✅ HMAC matches!

-- TDG Diagnostic --
TX++ → 2                            Receives packet
Build HMAC(counter=2, ...)          Expects HMAC(counter=2, ...)
Send command                        RX++ → 2
                                    ✅ HMAC matches!

-- Ring (if we skip commands) --
TX++ → 3 ❌                         Receives packet
Build HMAC(counter=3, ...)          Expects HMAC(counter=6, ...) ❌
Send command                        HMAC mismatch!
                                    Command ignored (silent failure)
```

### The Synchronization Problem

**Why the Tile doesn't ring if you skip commands:**

1. Tile firmware has a state machine that expects commands in a specific order
2. Counter is used for HMAC validation AND replay protection
3. If you send counter=3 but Tile expects counter=6, HMAC fails
4. Tile silently rejects the command (no error response)
5. Your ring command is ignored

**Solution:** Send ALL intermediate commands, even if you don't care about their responses.

---

## Ring Command Details

### SONG Command Format

```
Channel Mode Packet:
[channel_byte] [SONG_CMD] [SONG_PLAY] [flags] [volume] [duration] [hmac_4bytes]

Example: 02 05 02 01 03 1e 48 4f e8 d2
         ↑  ↑  ↑  ↑  ↑  ↑  └─ HMAC (4 bytes)
         │  │  │  │  │  └─ Duration (30 seconds)
         │  │  │  │  └─ Volume (3 = high)
         │  │  │  └─ Flags (0x01 standard)
         │  │  └─ Transaction (0x02 = PLAY)
         │  └─ Command (0x05 = SONG)
         └─ Channel byte (0x02)
```

### Volume Levels

| Value | Volume |
|-------|--------|
| 1 | Low |
| 2 | Medium |
| 3 | High |

### Duration

- Valid range: 1-300 seconds
- Sent as single byte (values > 255 will wrap)
- Tile may have firmware limits (typically 60 seconds max)

### Ring Command is Fire-and-Forget

**Important:** The Tile does **not** send a response to the ring command. It just starts ringing.

---

## Error Handling

### Authentication Failures

**Problem:** Wrong auth_key or incorrect HMAC calculation

**Symptoms:**
- Tile doesn't respond to auth request
- Or Tile sends error response

**Solution:**
- Verify auth_key is correct (16 bytes, base64 decoded)
- Try all 20 authentication methods (see implementation)
- Check rand_a/rand_b order in HMAC

### Counter Desync

**Problem:** Skipped commands or incorrect counter value

**Symptoms:**
- Authentication works
- Channel establishment works
- Ring command silently fails (Tile doesn't ring)

**Debugging:**
```python
# Log counter before each command
_LOGGER.debug(f"Sending command with counter={self._tx_counter}")

# Verify HMAC message
_LOGGER.debug(f"HMAC message: {hmac_message.hex()}")
```

**Solution:**
- Ensure you send ALL intermediate commands
- Verify counter increments before each command
- Never reuse a counter value

### BLE Connection Issues

**Problem:** Tile is out of range or Bluetooth adapter issues

**Symptoms:**
- Scan timeout
- Connection fails
- Write characteristic fails

**Solution:**
- Check Bluetooth adapter is enabled
- Verify Tile is within range (~30 meters)
- Ensure Tile has battery power
- Try scanning longer (increase timeout)

---

## Implementation Reference

### Python Code Structure

```python
class TileBleClient:
    def __init__(self, auth_data: TileAuthData):
        self._auth_key = auth_data.auth_key  # 16 bytes
        self._channel_key = None  # Derived during auth
        self._tx_counter = 0
        self._channel_byte = None

    async def authenticate(self) -> bool:
        """Authenticate using 20 different HMAC methods."""
        # Try each auth method until one works
        for method in range(1, 21):
            if await self._try_auth_method(method):
                return True
        return False

    async def ring(self, volume, duration) -> bool:
        """Ring the Tile with full command sequence."""
        # Command 1: Channel establishment (done in authenticate)
        # Command 2: TDG
        await self._send_tdg_diagnostic()
        # Command 3: AdvInt
        await self._send_adv_int()
        # Command 4: TCU
        await self._update_connection_params()
        # Command 5: READ_FEATURES
        await self._read_song_features()
        # Command 6: RING!
        return await self._send_ring_command(volume, duration)
```

### Key Implementation Files

- `custom_components/life360/tile_ble.py` - Main BLE implementation
- See lines 466-893 for authentication
- See lines 895-1060 for intermediate commands
- See lines 1364-1442 for ring logic

---

## Protocol Evolution & Compatibility

### Tile Firmware Versions

This implementation is based on:
- **Tile Android App:** v2.140.0 (2025)
- **Tile Firmware:** Compatible with 2025 models
- **BLE Protocol:** TOA (Tile Over Air) v2

### Backward Compatibility

Older Tile devices may:
- Use fewer intermediate commands
- Have different counter expectations
- Support different authentication methods

**Testing across models:**
- ✅ Tile Mate (2024 model)
- ✅ Tile Pro (2024 model)
- ❓ Tile Slim (untested, should work)
- ❓ Tile Sticker (untested, should work)

### Future Protocol Changes

Tile may update their protocol in future firmware. Signs of protocol changes:
- Authentication fails with all 20 methods
- Ring command format rejected
- New commands added to required sequence

---

## Security Considerations

### Authentication Key Storage

**DO NOT** hardcode or expose auth_keys:
- Auth keys are unique per Tile
- They are equivalent to passwords
- Store securely (encrypted storage recommended)
- Never log auth_keys in plain text

### HMAC Security

The protocol uses HMAC-SHA256 which provides:
- **Message authentication:** Verifies commands are from trusted source
- **Replay protection:** Counter prevents reuse of old commands
- **Integrity:** Detects tampering with command data

### Counter as Nonce

The counter serves as a cryptographic nonce:
- Prevents replay attacks
- Ensures each command has unique HMAC
- Must never repeat for same channel session

---

## Troubleshooting Checklist

- [ ] Bluetooth adapter is enabled and functional
- [ ] Tile has battery power (check in Life360 app)
- [ ] Tile is within BLE range (~30 meters)
- [ ] Auth key is correct (16 bytes, from Life360 API)
- [ ] Auth key is properly base64 decoded
- [ ] Random challenges (rand_a, rand_b) are 8 bytes
- [ ] HMAC uses correct key (auth_key for auth, channel_key for commands)
- [ ] Counter starts at 0, first command uses counter=1
- [ ] Counter increments before EACH command
- [ ] ALL intermediate commands are sent in order
- [ ] HMAC message is padded to 32 bytes
- [ ] HMAC signature is truncated to 4 bytes
- [ ] Channel byte is correct (from Tile's OPEN response)
- [ ] Command payload length is correct
- [ ] Direction byte is 0x01 for TX, 0x02 for RX

---

## References

### Academic Research
- "Tile Tracker Security Analysis" - Various security researchers have analyzed the TOA protocol

### Reverse Engineering Sources
- Tile Android App v2.140.0 decompiled sources
- BLE packet captures from official app
- `node-tile` project (JavaScript implementation)
- This Home Assistant integration

### Official Tile Documentation
- Tile does NOT provide public API documentation for BLE
- Protocol is proprietary and reverse-engineered

---

## License & Disclaimer

This documentation is for educational and interoperability purposes only.

**Disclaimer:**
- This is an unofficial, reverse-engineered implementation
- Tile Inc. does not endorse or support this implementation
- Use at your own risk
- May break with future Tile firmware updates

---

*Documentation Version: 1.0*
*Last Updated: January 2025*
*Protocol Version: TOA v2 (Tile Android App v2.140.0)*
