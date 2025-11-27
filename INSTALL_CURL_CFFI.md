# Installing curl_cffi for Tile/Jiobit Device Names

The Life360 integration uses `curl_cffi` to bypass Cloudflare's WAF and fetch proper device names for your Tile and Jiobit devices.

## Automatic Installation (Recommended)

Home Assistant should automatically install `curl_cffi` when you install this integration via HACS. If it doesn't install automatically:

1. **Restart Home Assistant** after installing the integration via HACS
2. **Reload the Life360 integration**: Settings → Devices & Services → Life360 → ⋯ → Reload

## Manual Installation

If automatic installation doesn't work, install manually:

### Option 1: Home Assistant Terminal Add-on

1. Install the **Terminal & SSH** add-on from the Add-on Store
2. Start the Terminal add-on
3. Run these commands:
   ```bash
   pip3 install curl_cffi
   ```
4. Restart Home Assistant

### Option 2: SSH into Home Assistant Container

If running Home Assistant in Docker:

```bash
# Access the Home Assistant container
docker exec -it homeassistant bash

# Install curl_cffi
pip3 install curl_cffi

# Exit container
exit

# Restart Home Assistant
docker restart homeassistant
```

If running Home Assistant OS:
```bash
# SSH into your HA server
ssh root@homeassistant.local

# Login to the Home Assistant container
ha homeassistant stop
docker exec -it homeassistant bash
pip3 install curl_cffi
exit
ha homeassistant start
```

### Option 3: Home Assistant Core (venv)

If you're running Home Assistant Core in a virtual environment:

```bash
# Activate your Home Assistant venv
cd /srv/homeassistant
source bin/activate

# Install curl_cffi
pip3 install curl_cffi

# Restart Home Assistant
sudo systemctl restart home-assistant@homeassistant
```

## Verify Installation

After installation, check your Home Assistant logs for:

```
✅ Successfully fetched v6/devices via subprocess
```

Or you can run the check script:

```bash
python3 custom_components/life360/scripts/check_curl_cffi.py
```

## Troubleshooting

### Still seeing "Install curl_cffi to bypass Cloudflare" message?

1. **Restart Home Assistant** after installing curl_cffi
2. **Reload the Life360 integration** (don't just restart HA)
3. Check logs for the exact error message

### Installation fails with "permission denied"?

Try with sudo (if applicable):
```bash
sudo pip3 install curl_cffi
```

### curl_cffi installs but device names still don't work?

Check your logs for other errors. The integration has fallback mechanisms:
1. First tries curl_cffi subprocess (best, bypasses Cloudflare)
2. Falls back to direct API call with aiohttp (likely to fail with 401)
3. Falls back to Tile API for BLE auth keys only

Even without curl_cffi, you'll still get:
- ✅ Tile BLE auth keys (for ringing devices)
- ✅ Device locations
- ❌ Proper device names (will show as "Tile 12345678")

## Why is curl_cffi needed?

Life360's v6 API is protected by Cloudflare WAF which blocks standard Python HTTP libraries. The `curl_cffi` library mimics the TLS fingerprint of Android Chrome browser, allowing the integration to bypass Cloudflare and fetch device names properly.

Without curl_cffi:
- Your devices will appear as "Tile 12345678" instead of "Keys" or "Wallet"
- Everything else works (locations, BLE ringing, etc.)
