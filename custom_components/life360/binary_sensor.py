"""Life360 Binary Sensor."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from functools import cached_property, partial  # pylint: disable=hass-deprecated-import
import logging
from typing import Any, cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import ATTRIBUTION, SIGNAL_ACCT_STATUS
from .coordinator import CirclesMembersDataUpdateCoordinator, L360ConfigEntry
from .helpers import AccountID, CircleID, ConfigOptions, PlaceAlert

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: L360ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensory platform."""
    coordinator = entry.runtime_data.coordinator
    entities: dict[AccountID, Life360BinarySensor] = {}

    async def process_config(hass: HomeAssistant, entry: L360ConfigEntry) -> None:
        """Add and/or remove binary online sensors."""
        options = ConfigOptions.from_dict(entry.options)
        aids = set(options.accounts)
        cur_aids = set(entities)
        del_aids = cur_aids - aids
        add_aids = aids - cur_aids

        if del_aids:
            old_entities = [entities.pop(aid) for aid in del_aids]
            _LOGGER.debug("Deleting binary online sensors for: %s", ", ".join(del_aids))
            await asyncio.gather(*(entity.async_remove() for entity in old_entities))

        if add_aids:
            new_entities = {
                aid: Life360BinarySensor(coordinator, aid) for aid in add_aids
            }
            entities.update(new_entities)
            _LOGGER.debug("Adding binary online sensors for: %s", ", ".join(add_aids))
            async_add_entities(new_entities.values())

    await process_config(hass, entry)
    entry.async_on_unload(entry.add_update_listener(process_config))

    # Set up place alerts coordinator and sensors
    alerts_coordinator = PlaceAlertsCoordinator(hass, coordinator)
    await alerts_coordinator.async_config_entry_first_refresh()

    alert_entities: list[PlaceAlertBinarySensor] = []
    for cid, alerts in alerts_coordinator.data.items():
        circle_data = coordinator.data.circles.get(cid)
        circle_name = circle_data.name if circle_data else str(cid)
        for alert in alerts:
            alert_entities.append(
                PlaceAlertBinarySensor(alerts_coordinator, cid, circle_name, alert)
            )

    if alert_entities:
        _LOGGER.debug("Adding %d place alert sensors", len(alert_entities))
        async_add_entities(alert_entities)


class Life360BinarySensor(BinarySensorEntity):
    """Life360 Binary Sensor."""

    _attr_attribution = ATTRIBUTION
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_should_poll = False

    def __init__(
        self, coordinator: CirclesMembersDataUpdateCoordinator, aid: AccountID
    ) -> None:
        """Initialize binary sensor."""
        self._attr_name = f"Life360 online ({aid})"
        self._attr_unique_id = aid
        self._enabled = (
            ConfigOptions.from_dict(coordinator.config_entry.options)
            .accounts[aid]
            .enabled
        )
        self._online = partial(coordinator.acct_online, aid)

        self.async_on_remove(
            coordinator.config_entry.add_update_listener(
                self._async_config_entry_updated
            )
        )

    @cached_property
    def aid(self) -> AccountID:
        """Return account ID."""
        return cast(AccountID, self.unique_id)

    @property
    def is_on(self) -> bool:
        """Return if account is online."""
        if not self._enabled:
            return False
        return self._online()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""

        @callback
        def write_state(aid: AccountID) -> None:
            """Write state if account status was updated."""
            if aid == self.aid:
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ACCT_STATUS, write_state)
        )

    async def _async_config_entry_updated(
        self, _: HomeAssistant, entry: L360ConfigEntry
    ) -> None:
        """Run when the config entry has been updated."""
        enabled = ConfigOptions.from_dict(entry.options).accounts[self.aid].enabled
        if enabled == self._enabled:
            return

        self._enabled = enabled
        self.async_write_ha_state()


class PlaceAlertsCoordinator(DataUpdateCoordinator[dict[CircleID, list[PlaceAlert]]]):
    """Coordinator for place alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 Place Alerts",
            update_interval=timedelta(hours=1),  # Alerts don't change often
        )
        self._coordinator = coordinator
        self.data: dict[CircleID, list[PlaceAlert]] = {}

    async def _async_update_data(self) -> dict[CircleID, list[PlaceAlert]]:
        """Fetch place alerts for all circles."""
        alerts: dict[CircleID, list[PlaceAlert]] = {}

        for cid in self._coordinator.data.circles:
            circle_alerts = await self._coordinator.get_place_alerts(cid)
            if circle_alerts:
                alerts[cid] = circle_alerts

        return alerts


class PlaceAlertBinarySensor(
    CoordinatorEntity[PlaceAlertsCoordinator], BinarySensorEntity
):
    """Binary sensor for place alert status."""

    _attr_attribution = ATTRIBUTION
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(
        self,
        coordinator: PlaceAlertsCoordinator,
        circle_id: CircleID,
        circle_name: str,
        alert: PlaceAlert,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator)
        self._circle_id = circle_id
        self._alert = alert
        self._attr_unique_id = f"alert_{alert.alert_id}"
        self._attr_name = f"Life360 Alert: {alert.member_name} at {alert.place_name}"
        self._attr_icon = (
            "mdi:map-marker-alert" if alert.enabled else "mdi:map-marker-off"
        )

    @property
    def is_on(self) -> bool:
        """Return if alert is enabled."""
        # Check if this alert still exists and is enabled
        if self._circle_id not in self.coordinator.data:
            return False
        for alert in self.coordinator.data[self._circle_id]:
            if alert.alert_id == self._alert.alert_id:
                return alert.enabled
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "alert_id": self._alert.alert_id,
            "place_id": self._alert.place_id,
            "place_name": self._alert.place_name,
            "member_id": self._alert.member_id,
            "member_name": self._alert.member_name,
            "alert_type": self._alert.alert_type,
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if self._circle_id not in self.coordinator.data:
            return False
        return any(
            a.alert_id == self._alert.alert_id
            for a in self.coordinator.data[self._circle_id]
        )
