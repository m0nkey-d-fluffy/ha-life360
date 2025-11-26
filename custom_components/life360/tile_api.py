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
TILE_APP_ID = "ios-tile-production"
TILE_APP_VERSION = "2.131.1"
TILE_API_VERSION = "1.0"


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
        self.client_uuid = str(uuid.uuid4())
        self.session_token: str | None = None
        self.user_uuid: str | None = None

    def _get_headers(self, include_session: bool = False, include_content_type: bool = False) -> dict[str, str]:
        """Get API request headers.

        Args:
            include_session: Whether to include session token
            include_content_type: Whether to include Content-Type header

        Returns:
            Headers dict
        """
        headers = {
            "User-Agent": f"Tile/{TILE_APP_VERSION}",
            "Accept": "application/json",
            "tile_api_version": TILE_API_VERSION,
            "tile_app_id": TILE_APP_ID,
            "tile_app_version": TILE_APP_VERSION,
            "tile_client_uuid": self.client_uuid,
        }

        if include_content_type:
            headers["Content-Type"] = "application/json"

        if include_session and self.session_token:
            headers["tile_session_id"] = self.session_token

        return headers

    async def authenticate(self) -> bool:
        """Authenticate with Tile API.

        Returns:
            True if authentication succeeded

        Raises:
            TileAuthenticationError: If authentication fails
        """
        try:
            # Step 1: Register client
            _LOGGER.debug("Registering Tile API client: %s", self.client_uuid)
            client_url = f"{TILE_API_BASE}/clients/{self.client_uuid}"

            client_data = {
                "app_id": TILE_APP_ID,
                "app_version": TILE_APP_VERSION,
                "locale": "en-US",
            }

            async with self.session.put(
                client_url,
                headers=self._get_headers(include_content_type=True),
                json=client_data,
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    _LOGGER.error("Tile client registration failed: HTTP %s - %s", resp.status, text)
                    raise TileAuthenticationError(f"Client registration failed: {resp.status}")

                _LOGGER.debug("Tile client registered successfully")

            # Step 2: Create session (login)
            _LOGGER.debug("Logging in to Tile API with email: %s", self.email)
            session_url = f"{TILE_API_BASE}/clients/{self.client_uuid}/sessions"

            session_data = {
                "email": self.email,
                "password": self.password,
            }

            async with self.session.post(
                session_url,
                headers=self._get_headers(include_content_type=True),
                json=session_data,
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    _LOGGER.error("Tile login failed: HTTP %s - %s", resp.status, text)
                    if resp.status == 401:
                        raise TileAuthenticationError("Invalid Tile email or password")
                    raise TileAuthenticationError(f"Login failed: {resp.status}")

                data = await resp.json()
                self.session_token = data.get("result", {}).get("session_token")
                self.user_uuid = data.get("result", {}).get("user", {}).get("user_uuid")

                if not self.session_token:
                    _LOGGER.error("No session token in Tile login response")
                    raise TileAuthenticationError("No session token received")

                _LOGGER.info("✅ Tile API authentication successful (user: %s)", self.user_uuid)
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
        if not self.session_token:
            raise TileAPIError("Not authenticated - call authenticate() first")

        try:
            # Get tile states (list of tiles)
            _LOGGER.debug("Fetching Tile device states")
            states_url = f"{TILE_API_BASE}/tiles/tile_states"

            async with self.session.get(
                states_url,
                headers=self._get_headers(include_session=True),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Tile states request failed: HTTP %s - %s", resp.status, text)
                    raise TileAPIError(f"Failed to get tile states: {resp.status}")

                states_data = await resp.json()
                tile_ids = states_data.get("result", [])
                _LOGGER.debug("Found %d Tile devices", len(tile_ids))

            # Get details for each tile (including authKey)
            tiles = {}
            for tile_id in tile_ids:
                _LOGGER.debug("Fetching details for Tile: %s", tile_id)
                tile_url = f"{TILE_API_BASE}/tiles/{tile_id}"

                async with self.session.get(
                    tile_url,
                    headers=self._get_headers(include_session=True),
                ) as resp:
                    if resp.status == 200:
                        tile_data = await resp.json()
                        result = tile_data.get("result", {})

                        # Extract important fields
                        tile_info = {
                            "tile_id": result.get("tile_id"),
                            "tile_uuid": result.get("tile_uuid"),
                            "name": result.get("name", f"Tile {tile_id[:8]}"),
                            "archetype": result.get("archetype"),
                            "auth_key": result.get("auth_key"),  # BLE authentication key!
                            "tile_type": result.get("tile_type"),
                            "hw_version": result.get("hw_version"),
                            "fw_version": result.get("fw_version"),
                            "last_tile_state": result.get("last_tile_state", {}),
                        }

                        if tile_info["auth_key"]:
                            _LOGGER.info(
                                "✓ Retrieved Tile: %s (authKey: %d bytes)",
                                tile_info["name"],
                                len(tile_info["auth_key"]) if isinstance(tile_info["auth_key"], str) else 0,
                            )
                            tiles[tile_id] = tile_info
                        else:
                            _LOGGER.warning("No authKey found for Tile: %s", tile_id)

                    elif resp.status == 412:
                        # Some Tiles (like Tile Labels) may return 412
                        _LOGGER.debug("Tile %s returned 412 (may not support all features)", tile_id)
                    else:
                        text = await resp.text()
                        _LOGGER.warning(
                            "Failed to get details for Tile %s: HTTP %s - %s",
                            tile_id, resp.status, text[:200]
                        )

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
