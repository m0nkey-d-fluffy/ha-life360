"""Tile API client for direct authentication and device access.

This module provides direct access to Tile's API to retrieve device information
and BLE authentication credentials, independent of Life360.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from aiohttp import ClientSession, ClientError

_LOGGER = logging.getLogger(__name__)

# Tile API constants
TILE_API_BASE = "https://production.tile-api.com/api/v1"
TILE_APP_ID = "android-tile-production"
TILE_APP_VERSION = "2.109.0.4485"
TILE_CLIENT_UUID = "26726553-703b-3998-9f0e-c5f256caaf6d"  # Fixed UUID from node-tile


class TileAPIError(Exception):
    """Base exception for Tile API errors."""


class TileAuthenticationError(TileAPIError):
    """Tile authentication failed."""


class TileAPIClient:
    """Client for Tile API."""

    def __init__(
        self,
        email: str,
        password: str,
        session: ClientSession,
    ) -> None:
        """Initialize Tile API client.

        Args:
            email: Tile account email
            password: Tile account password
            session: aiohttp ClientSession
        """
        self.email = email
        self.password = password
        self.session = session
        self.client_uuid = TILE_CLIENT_UUID  # Use fixed UUID
        self.session_cookie: str | None = None

    def _get_headers(self, include_session: bool = False, include_content_type: bool = False) -> dict[str, str]:
        """Get API request headers.

        Args:
            include_session: Whether to include session cookie
            include_content_type: Whether to include Content-Type header

        Returns:
            Headers dict
        """
        import time

        headers = {
            "User-Agent": f"Tile/android/{TILE_APP_VERSION} (Unknown; Android11)",
            "tile_app_id": TILE_APP_ID,
            "tile_app_version": TILE_APP_VERSION,
            "tile_client_uuid": self.client_uuid,
            "tile_request_timestamp": str(int(time.time() * 1000)),
        }

        if include_content_type:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        if include_session and self.session_cookie:
            headers["Cookie"] = self.session_cookie

        return headers

    async def authenticate(self) -> bool:
        """Authenticate with Tile API.

        Returns:
            True if authentication succeeded

        Raises:
            TileAuthenticationError: If authentication fails
        """
        try:
            # Login to Tile API (single POST request)
            _LOGGER.debug("Logging in to Tile API with email: %s", self.email)
            session_url = f"{TILE_API_BASE}/clients/{self.client_uuid}/sessions"

            # Use form-encoded data, not JSON
            from aiohttp import FormData
            form_data = FormData()
            form_data.add_field("email", self.email)
            form_data.add_field("password", self.password)

            headers = self._get_headers(include_content_type=True)
            _LOGGER.debug("POST %s", session_url)
            _LOGGER.debug("Request headers: %s", headers)
            _LOGGER.debug("Request body: email=%s&password=***", self.email)

            async with self.session.post(
                session_url,
                headers=headers,
                data=f"email={self.email}&password={self.password}",
            ) as resp:
                _LOGGER.debug("Response status: %s", resp.status)
                _LOGGER.debug("Response headers: %s", dict(resp.headers))

                # Get session cookie from response
                set_cookie = resp.headers.get("set-cookie")
                if set_cookie:
                    self.session_cookie = set_cookie
                    _LOGGER.debug("Got session cookie")

                # Check response body
                resp_text = await resp.text()
                _LOGGER.debug("Response body: %s", resp_text)

                if resp.status not in (200, 201):
                    _LOGGER.error("Tile login failed: HTTP %s - %s", resp.status, resp_text)
                    if resp.status == 401:
                        raise TileAuthenticationError("Invalid Tile email or password")
                    raise TileAuthenticationError(f"Login failed: {resp.status}")

                # Check for error message in response
                try:
                    data = await resp.json() if not resp_text else __import__("json").loads(resp_text)
                    if data.get("result", {}).get("message") == "Invalid credentials":
                        raise TileAuthenticationError("Invalid Tile email or password")
                except Exception:
                    pass  # Response might not be JSON

                if not self.session_cookie:
                    _LOGGER.error("No session cookie in Tile login response")
                    raise TileAuthenticationError("No session cookie received")

                _LOGGER.info("✅ Tile API authentication successful")
                return True

        except ClientError as err:
            _LOGGER.error("Tile API connection error: %s", err)
            raise TileAuthenticationError(f"Connection error: {err}") from err

    async def get_tiles(self) -> dict[str, dict[str, Any]]:
        """Get all Tile devices with their details.

        Returns:
            Dict mapping tile_id to tile data (including authKey)

        Raises:
            TileAPIError: If request fails
        """
        if not self.session_cookie:
            raise TileAPIError("Not authenticated - call authenticate() first")

        try:
            # Get all devices from /users/groups endpoint
            _LOGGER.debug("Fetching Tile devices from /users/groups")
            groups_url = f"{TILE_API_BASE}/users/groups?last_modified_timestamp=0"

            async with self.session.get(
                groups_url,
                headers=self._get_headers(include_session=True),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Tile groups request failed: HTTP %s - %s", resp.status, text)
                    raise TileAPIError(f"Failed to get tile groups: {resp.status}")

                data = await resp.json()
                _LOGGER.debug("Groups response: %s", data)

                nodes = data.get("result", {}).get("nodes", {})
                _LOGGER.debug("Found %d nodes", len(nodes))

                # Extract Tile devices
                tiles = {}
                for node_id, node_data in nodes.items():
                    if node_data.get("node_type") != "TILE":
                        _LOGGER.debug("Skipping non-TILE node: %s", node_id)
                        continue

                    # Log all available fields to find MAC address
                    _LOGGER.info("🔍 Tile %s raw fields: %s", node_id[:8], list(node_data.keys()))

                    auth_key = node_data.get("auth_key")
                    name = node_data.get("name", f"Tile {node_id[:8]}")
                    product_code = node_data.get("product_code", "UNKNOWN")

                    if auth_key:
                        tile_info = {
                            "tile_id": node_id,
                            "name": name,
                            "auth_key": auth_key,  # Base64 encoded
                            "product_code": product_code,
                            "firmware": node_data.get("firmware", {}),
                            "node_type": node_data.get("node_type"),
                        }

                        tiles[node_id] = tile_info
                        _LOGGER.info(
                            "✓ Retrieved Tile: %s (%s)",
                            name,
                            product_code,
                        )
                    else:
                        _LOGGER.warning("No authKey found for Tile: %s", node_id)

                _LOGGER.info("Successfully retrieved %d Tiles with auth keys", len(tiles))
                return tiles

        except ClientError as err:
            _LOGGER.error("Tile API connection error: %s", err)
            raise TileAPIError(f"Connection error: {err}") from err


async def async_login(
    email: str,
    password: str,
    session: ClientSession,
) -> TileAPIClient:
    """Login to Tile API and return authenticated client.

    Args:
        email: Tile account email
        password: Tile account password
        session: aiohttp ClientSession

    Returns:
        Authenticated TileAPIClient

    Raises:
        TileAuthenticationError: If authentication fails
    """
    client = TileAPIClient(email, password, session)
    await client.authenticate()
    return client
