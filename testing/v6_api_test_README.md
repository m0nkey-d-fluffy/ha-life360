# Life360 v6/devices API Testing Kit

This testing kit helps you rapidly iterate and test the Life360 v6/devices API with different authentication configurations.

## Files

1. **test_v6_api.py** - Main test script for v6 API
2. **extract_bearer_token.py** - Helper to extract credentials from logs/flows
3. **v6_api_test_README.md** - This file

## Quick Start

### Step 1: Get your Bearer Token

You have two options:

**Option A: From Home Assistant logs (easiest)**
```bash
# Enable debug logging in configuration.yaml:
# logger:
#   logs:
#     custom_components.life360: debug

# Then extract from logs:
python3 extract_bearer_token.py /path/to/home-assistant.log
```

**Option B: Capture your own network traffic (advanced)**

⚠️ **You must capture YOUR OWN network traffic - don't use anyone else's flows!**

To capture network traffic from the Life360 mobile app:
1. Install mitmproxy on your computer: https://mitmproxy.org/
2. Configure your phone to use mitmproxy as HTTP proxy
3. Install mitmproxy's CA certificate on your phone
4. Open the Life360 app and let it sync
5. Save the captured flows from mitmproxy
6. Extract credentials:

```bash
# Extract from YOUR captured flows
python3 extract_bearer_token.py /path/to/your/captured/flows
```

**Option C: Manual configuration**

If extraction doesn't work, manually configure credentials:

1. Get Bearer token from HA logs (look for "Authorization: Bearer ...")
2. Get x-device-id from network capture (look for "x-device-id:" header)
3. Update `test_v6_configured.py` with your values

### Step 2: Configure the Test Script

Both test scripts need the same configuration:

Edit `test_v6_configured.py` or `test_v6_api.py` and update these lines:

```python
BEARER_TOKEN = "YOUR_BEARER_TOKEN_HERE"  # From HA logs
DEVICE_ID = "YOUR_DEVICE_ID_HERE"  # From network capture
CIRCLE_ID = "YOUR_CIRCLE_ID_HERE"  # From HA configuration.yaml
```

**To find your CIRCLE_ID:**
- Check your Home Assistant `configuration.yaml` under `life360:` section
- Or look in HA logs for `life360` and find the circle UUID (format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
- Or run `curl` with your bearer token to `https://api-cloudfront.life360.com/v3/circles` to list your circles

### Step 3: Run Tests

```bash
# Install dependencies (curl_cffi for Android Chrome TLS impersonation)
pip3 install curl_cffi

# Run the test
python3 test_v6_configured.py
```

**What the scripts now do:**
- ✅ **TLS Fingerprinting**: Uses `curl_cffi` to impersonate Android Chrome (bypasses Cloudflare fingerprint detection)
- ✅ **Session Establishment**: Calls other Life360 API endpoints first to build session cookies (mimics mobile app behavior)
- ✅ **HTTP/2**: Native HTTP/2 support matching mobile app
- ✅ **Cookie Handling**: Persistent cookies across requests like mobile app

## What the Test Does

The script will test two scenarios:

1. **Test 1**: Call v6/devices API **with** x-device-id header
   - Expected: HTTP 200 with full device list and names

2. **Test 2**: Call v6/devices API **without** x-device-id header
   - Expected: HTTP 401 (unauthorized) OR HTTP 200 if it works without it

## Expected Output

### Successful Response (HTTP 200)
```json
{
  "data": {
    "items": [
      {
        "id": "dr1234abcd-5678-efgh-ijkl-mnopqrstuvwx",
        "name": "My Tile Device",
        "provider": "tile",
        "typeData": {
          "deviceId": "1a2b3c4d5e6f7g8h",
          "authKey": "BASE64_ENCODED_AUTH_KEY=="
        }
      }
    ]
  }
}
```

### Failed Response (HTTP 401)
```
Response Status: 401 Unauthorized
```

## Testing Different Scenarios

You can modify the test script to try different things:

### Test with different headers:
```python
headers["ce-specversion"] = "1.0"  # Try removing this
headers["ce-type"] = "com.life360.device.devices.v1"  # Try different values
```

### Test with different query parameters:
```python
params = {
    "activationStates": "activated",  # Try just "activated"
    # Or no params at all
}
```

### Test with different User-Agent:
```python
headers["User-Agent"] = "Home Assistant Life360 Integration"
# vs
headers["User-Agent"] = "com.life360.android.safetymapd/KOKO/25.45.0 android/12"
```

## Troubleshooting

### "401 Unauthorized" with x-device-id
- Bearer token might be expired
- x-device-id might be wrong
- Try getting a fresh token from HA logs

### "401 Unauthorized" without x-device-id
- This is expected - the API requires x-device-id
- You'll need to extract it from network capture

### How to get a fresh Bearer Token:
1. Restart the Life360 integration in Home Assistant
2. Enable debug logging:
   ```yaml
   logger:
     logs:
       custom_components.life360: debug
   ```
3. Look for lines like:
   ```
   Authorization: Bearer <token>
   ```

### How to capture x-device-id:
1. Install mitmproxy on your computer
2. Configure Android phone to use mitmproxy as proxy
3. Install mitmproxy CA certificate on phone
4. Open Life360 app
5. Capture traffic and look for x-device-id header in requests to api-cloudfront.life360.com

## Next Steps After Testing

Once you find a working combination:

1. **If it works WITH x-device-id:**
   - Add x-device-id to your Life360 integration configuration
   - Entity names should auto-populate

2. **If it works WITHOUT x-device-id:**
   - Let me know! We can update the integration to work without it
   - This would be a huge win

3. **If nothing works:**
   - Fall back to manual renaming in Home Assistant
   - BLE ringing should still work with cached auth keys
