# Life360 v6/devices API Testing & Development Tools

This tools directory contains standalone scripts for testing and debugging the Life360 v6/devices API integration.

## What's New

**🎉 No manual configuration needed!** The integration now:
- ✅ Auto-generates random Android device IDs
- ✅ Automatically installs curl_cffi via HACS
- ✅ Bypasses Cloudflare WAF using subprocess + TLS fingerprinting
- ✅ Shows device names automatically (Tiles, Jiobits)

These tools are for **debugging and development only** - regular users don't need them!

## Files

### Test Scripts
- **test_v6_configured.py** - Pre-configured test with all features
- **test_v6_api.py** - Flexible test for experimentation
- **test_v6_aiohttp.py** - Test if aiohttp works (it doesn't - Cloudflare blocks it)

### Helper Tools
- **decode_v6_mappings.py** - Decode and visualize device mappings
- **DEVICE_MAPPING_GUIDE.md** - Complete guide to device ID hierarchy

### Documentation
- **v6_api_test_README.md** - This file

## Quick Start (For Developers)

### Prerequisites

```bash
# Install curl_cffi (if not already installed by HA)
pip3 install curl_cffi
```

### Run a Test

```bash
cd tools

# Edit test_v6_configured.py and add your credentials
# Then run:
./test_v6_configured.py
```

### Configuration

Edit the test script and update:

```python
BEARER_TOKEN = "your_bearer_token_here"  # From HA logs
DEVICE_ID = "androidXXXXXXXXXXXXXX"      # Can be randomized!
CIRCLE_ID = "xxxxxxxx-xxxx-xxxx..."      # From HA config
```

**Finding Your Credentials:**

**Bearer Token:**
- Check Home Assistant logs: `Authorization: Bearer ...`
- Or enable debug logging in `configuration.yaml`:
  ```yaml
  logger:
    logs:
      custom_components.life360: debug
  ```

**Device ID (Optional):**
- The integration auto-generates this now!
- Format: `android` + 22 random alphanumeric characters
- Example: `androidK3mP9xQw2Vn4Ry8Lz7Jc5T`
- You can use any random string in this format

**Circle ID:**
- From your `configuration.yaml` under `life360:`
- Or check HA logs for Life360 circle UUID
- Or call: `curl -H "Authorization: Bearer <token>" https://api-cloudfront.life360.com/v3/circles`

## How It Works

### Integration Flow (Production)

```
Home Assistant starts
    ↓
Integration loaded
    ↓
manifest.json requires: curl_cffi>=0.5.0
    ↓
HA automatically: pip install curl_cffi
    ↓
Coordinator auto-generates device ID
    └─> Example: "androidK3mP9xQw2Vn4Ry8Lz7Jc5T"
    ↓
Calls subprocess: python3 fetch_v6_devices.py <token> <device_id> <circle_id>
    ↓
Script uses curl_cffi with:
    - TLS fingerprinting (impersonates Android Chrome)
    - Session establishment (calls /v4/circles, /v5/circles first)
    - HTTP/2 protocol
    - CloudEvents headers
    ↓
Bypasses Cloudflare WAF → Returns JSON
    ↓
Coordinator parses device names
    ↓
Entities show real names: "Upstairs TV", "Wallet", "Keys" ✓
```

### Test Scripts Flow (Development)

```
Developer runs test_v6_configured.py
    ↓
Script uses curl_cffi directly
    ↓
Establishes session (mimics mobile app)
    ↓
Calls v6/devices API
    ↓
Displays results + device mappings
    ↓
Developer can debug/iterate quickly
```

## Test Scripts Explained

### test_v6_configured.py
**Purpose:** Quick testing with known credentials

**Features:**
- Pre-configured test scenarios
- TLS fingerprinting via curl_cffi
- Session establishment
- Auto-generated device IDs
- Detailed output

**Use when:** Testing if v6 API works with specific credentials

### test_v6_api.py
**Purpose:** Flexible experimentation

**Features:**
- Customizable headers and params
- Multiple test scenarios
- Easy to modify for testing different approaches

**Use when:** Experimenting with different API configurations

### test_v6_aiohttp.py
**Purpose:** Verify aiohttp compatibility

**Features:**
- Tests if plain aiohttp works
- Proves Cloudflare blocks it
- Shows why curl_cffi is needed

**Use when:** Demonstrating why we need curl_cffi

## Helper Tools

### decode_v6_mappings.py

Decodes and visualizes device mappings from v6 API responses.

```bash
# Pipe test output directly
./test_v6_configured.py | python3 decode_v6_mappings.py

# Or decode saved JSON
python3 decode_v6_mappings.py v6_response.json
```

**Shows:**
- 📱 Device names and categories
- 🔷 Tile BLE IDs and hardware info
- 🔑 Auth keys (base64 and hex)
- 🔗 Life360 ID → Tile BLE ID mappings

### DEVICE_MAPPING_GUIDE.md

Complete documentation explaining:
- Device ID hierarchy (Life360 ↔ Tile ↔ HA)
- How auth keys are decoded
- Bidirectional caching strategy
- Troubleshooting guide

## What's Tested

### Confirmed Working ✅

1. **TLS Fingerprinting** - curl_cffi successfully impersonates Android Chrome
2. **Session Establishment** - Calling preliminary APIs helps bypass Cloudflare
3. **HTTP/2** - Native HTTP/2 support matches mobile app
4. **CloudEvents Headers** - Required ce-* headers are properly set
5. **Random Device IDs** - Any `androidXXXXXXXXXXXXXXXX` format works
6. **Bearer Tokens** - Works with tokens from website OR mobile app

### Confirmed NOT Working ❌

1. **aiohttp** - Cloudflare blocks it (403 Forbidden)
2. **No session establishment** - Direct v6 call gets blocked
3. **Missing CloudEvents headers** - Returns 400 Bad Request
4. **No device ID** - Returns 400 "ce-source must not be blank"

## Integration Architecture

```
custom_components/life360/
├── coordinator.py
│   ├── _get_or_register_device_id()      # Auto-generates Android device ID
│   ├── _fetch_v6_via_subprocess()         # Calls helper script
│   └── _fetch_device_metadata()           # Processes v6 response
│
└── scripts/
    └── fetch_v6_devices.py                # Subprocess helper
        ├── Uses curl_cffi
        ├── TLS fingerprinting
        ├── Session establishment
        └── Returns JSON to stdout
```

## Troubleshooting

### "403 Forbidden" in tests
**Cause:** Cloudflare blocking your IP or fingerprint

**Solutions:**
- Wait a few minutes between tests
- Try from different IP (VPN, mobile hotspot)
- Ensure using curl_cffi (not aiohttp)

### "curl_cffi not installed"
```bash
pip3 install curl_cffi
```

### "Bearer token expired"
Get fresh token from HA logs after restarting integration

### "No devices in response"
Check:
- Do you have Tile/Jiobit devices in your Life360 account?
- Is `activationStates` param correct?
- Check raw JSON response for errors

## For Regular Users

**You don't need these tools!** The integration handles everything automatically:

1. Install via HACS
2. Restart Home Assistant
3. Device names appear automatically
4. Done!

These tools are only for:
- Developers debugging the integration
- Contributors testing changes
- Advanced users troubleshooting issues

## Next Steps

### If you're testing the integration:
1. Run test scripts to verify v6 API works
2. Check that device names appear
3. Test BLE ringing functionality

### If you're developing:
1. Modify scripts to test different approaches
2. Use decode_v6_mappings.py to understand data structure
3. Read DEVICE_MAPPING_GUIDE.md for architecture details

### If you found bugs:
1. Run test scripts to isolate the issue
2. Capture output and logs
3. Report at: https://github.com/m0nkey-d-fluffy/ha-life360/issues
