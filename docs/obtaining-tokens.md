# Obtaining Life360 Authentication Tokens

This guide explains how to obtain the authentication tokens needed to configure the Life360 integration in Home Assistant.

## Authentication Methods

Life360 supports two authentication methods:

1. **Username & Password** - Simple but only works for accounts without phone verification
2. **Access Token** - Required for accounts with phone number verification (most accounts)

## Method 1: Username & Password

This method works **only** if your Life360 account has **not** verified a phone number.

Simply enter your Life360 email address and password when configuring the integration.

> ⚠️ **Note:** Most Life360 accounts have phone verification enabled, which disables this method. If login fails with username/password, use Method 2.

## Method 2: Access Token (Recommended)

This method works for **all accounts**, including those with phone verification.

### Option A: Browser Developer Tools

1. Open your web browser (Chrome, Firefox, Edge, or Safari)
2. Navigate to **https://life360.com/login**
3. Open **Developer Tools**:
   - Chrome/Edge: Press `F12` or `Ctrl+Shift+I` (Windows) / `Cmd+Option+I` (Mac)
   - Firefox: Press `F12` or `Ctrl+Shift+I`
   - Safari: Enable Developer menu in Preferences, then `Cmd+Option+I`

4. Go to the **Network** tab
5. Make sure recording is enabled (red dot should be visible)
6. Log into Life360 with your credentials
7. After successful login, look for a request named **`token`** in the Network tab
   - If you see multiple "token" entries, look for one with method **POST** (not OPTIONS)
   - Click on it to view details

8. In the **Response** or **Preview** tab, find:
   ```json
   {
     "access_token": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
     "token_type": "Bearer"
   }
   ```

9. Copy these values for use in Home Assistant:
   - **Access Type:** `Bearer` (the `token_type` value)
   - **Access Token:** The long string from `access_token`

### Option B: Mobile App Traffic Capture (Advanced)

For users comfortable with network analysis tools:

1. Install a proxy tool like **mitmproxy**, **Charles Proxy**, or **Fiddler**
2. Configure your mobile device to use the proxy
3. Install the proxy's SSL certificate on your device
4. Open the Life360 app and let it sync
5. Look for requests to `api-cloudfront.life360.com`
6. Find the `Authorization` header in any authenticated request:
   ```
   Authorization: Bearer xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

The value after "Bearer " is your access token.

### Option C: Using cURL (Command Line)

If you know your Life360 credentials and don't have phone verification:

```bash
curl -X POST "https://api-cloudfront.life360.com/v3/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "username=YOUR_EMAIL" \
  -d "password=YOUR_PASSWORD"
```

The response will contain your access token.

## Configuring Home Assistant

Once you have your token:

1. Go to **Settings** → **Devices & services**
2. Click **+ ADD INTEGRATION**
3. Search for "Life360"
4. Select **Access Type & Token** authentication method
5. Enter:
   - **Account identifier:** Any name to identify this account (e.g., your email)
   - **Access type:** `Bearer`
   - **Access token:** Your token from above

## Token Expiration

Life360 tokens typically remain valid for extended periods. However, if you experience authentication errors:

1. The token may have expired
2. Log into life360.com again and obtain a new token
3. Update the integration configuration with the new token

## Troubleshooting

### "Login Error" or "403 Forbidden"
- Your token may be expired - obtain a new one
- Life360 may be rate limiting requests - wait a few minutes and try again

### "Token not found in response"
- Make sure you're looking at the correct network request (POST method, not OPTIONS)
- Try logging out of life360.com completely and logging in fresh

### Phone Verification Required
- Life360 now requires phone verification for most accounts
- Use Method 2 (Access Token) instead of username/password

## Security Notes

⚠️ **Important Security Considerations:**

- **Never share your access token** - it provides full access to your Life360 account
- **Never commit tokens to git repositories** - add them to `.gitignore`
- **Rotate tokens periodically** - log out and back in to get a new token
- **Use environment variables** when possible to store sensitive data

## API Reference

For developers interested in the Life360 API:
- See [API Endpoints](api_endpoints.md) for discovered endpoints
- Base URL: `https://api-cloudfront.life360.com`
- Authentication: `Authorization: Bearer <token>` header
