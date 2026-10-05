"""Config flow for Steirische Linien integration."""
from __future__ import annotations

import logging
from typing import Any
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import DOMAIN
from .const import (
    MODE_TRIP,
    MODE_STATION,
    CONF_MODE,
    CONF_API_URL,
    CONF_ORIGIN_LAT,
    CONF_ORIGIN_LON,
    CONF_DEST_LAT,
    CONF_DEST_LON,
    CONF_STATION_NAME,
    CONF_STOP_POINT_REF,
    CONF_LINES,
)

_LOGGER = logging.getLogger(__name__)

# Schema for selecting mode
STEP_MODE_SCHEMA = vol.Schema({
    vol.Required(CONF_MODE, default=MODE_TRIP): vol.In({
        MODE_TRIP: "Trip Planning (Origin → Destination)",
        MODE_STATION: "Station Departures (Single Station)"
    })
})

# Schema for trip planning mode
STEP_TRIP_SCHEMA = vol.Schema({
    vol.Required(CONF_API_URL): str,
    vol.Required(CONF_ORIGIN_LAT): float,
    vol.Required(CONF_ORIGIN_LON): float,
    vol.Required(CONF_DEST_LAT): float,
    vol.Required(CONF_DEST_LON): float,
})

# Schema for station mode
STEP_STATION_SCHEMA = vol.Schema({
    vol.Required(CONF_API_URL): str,
    vol.Required(CONF_STATION_NAME): str,
})


async def validate_trip_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the trip planning input."""
    # Validate URL format
    if not data[CONF_API_URL].startswith(("http://", "https://")):
        raise ValueError("Invalid API URL format")

    # Validate coordinates are within reasonable bounds
    if not -90 <= data[CONF_ORIGIN_LAT] <= 90:
        raise ValueError("Invalid origin latitude")
    if not -180 <= data[CONF_ORIGIN_LON] <= 180:
        raise ValueError("Invalid origin longitude")
    if not -90 <= data[CONF_DEST_LAT] <= 90:
        raise ValueError("Invalid destination latitude")
    if not -180 <= data[CONF_DEST_LON] <= 180:
        raise ValueError("Invalid destination longitude")

    return {"title": "Steiermark Öffis"}


async def validate_station_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the station input."""
    # Validate URL format
    if not data[CONF_API_URL].startswith(("http://", "https://")):
        raise ValueError("Invalid API URL format")

    # Validate station name is not empty
    if not data[CONF_STATION_NAME].strip():
        raise ValueError("Station name cannot be empty")

    return {"title": "Steiermark Öffis"}


async def search_stations(api_url: str, station_name: str) -> list[dict]:
    """Search for stations by name using TRIAS API."""
    xml_request = _create_location_request_xml(station_name)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                data=xml_request.encode('utf-8'),
                headers={
                    'User-Agent': 'HomeAssistant',
                    'Content-Type': 'text/xml'
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error(f"Station search failed with status {response.status}")
                    return []

                response_text = await response.text()
                return _parse_location_response(response_text)
    except Exception as e:
        _LOGGER.error(f"Error searching stations: {e}")
        return []


def _create_location_request_xml(station_name: str) -> str:
    """Create XML request to search for a station by name."""
    now = datetime.now(timezone.utc).isoformat()

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Trias xmlns="http://www.vdv.de/trias" version="1.2">
  <ServiceRequest>
    <siri:RequestTimestamp xmlns:siri="http://www.siri.org.uk/siri">{now}</siri:RequestTimestamp>
    <siri:RequestorRef xmlns:siri="http://www.siri.org.uk/siri">homeassistant</siri:RequestorRef>
    <RequestPayload>
      <LocationInformationRequest>
        <InitialInput>
          <LocationName>{station_name}</LocationName>
        </InitialInput>
        <Restrictions>
          <Type>stop</Type>
          <NumberOfResults>10</NumberOfResults>
        </Restrictions>
      </LocationInformationRequest>
    </RequestPayload>
  </ServiceRequest>
</Trias>"""


def _parse_location_response(xml_text: str) -> list[dict]:
    """Parse the location search response to extract station information."""
    try:
        root = ET.fromstring(xml_text)
        namespaces = {
            'trias': 'http://www.vdv.de/trias',
            'siri': 'http://www.siri.org.uk/siri'
        }

        stations = []
        locations = root.findall('.//trias:Location', namespaces)

        for location in locations:
            stop_point = location.find('.//trias:StopPoint', namespaces)
            if stop_point is not None:
                stop_point_ref_elem = stop_point.find('.//trias:StopPointRef', namespaces)
                stop_point_ref = stop_point_ref_elem.text if stop_point_ref_elem is not None else None

                stop_point_name_elem = stop_point.find('.//trias:StopPointName/trias:Text', namespaces)
                stop_point_name = stop_point_name_elem.text if stop_point_name_elem is not None else None

                location_name_elem = location.find('.//trias:LocationName/trias:Text', namespaces)
                location_name = location_name_elem.text if location_name_elem is not None else None

                if stop_point_ref:
                    stations.append({
                        'stop_point_ref': stop_point_ref,
                        'stop_point_name': stop_point_name,
                        'location_name': location_name,
                        'display_name': stop_point_name or location_name or stop_point_ref
                    })

        return stations
    except Exception as e:
        _LOGGER.error(f"Error parsing location response: {e}")
        return []


async def fetch_station_lines(api_url: str, stop_point_ref: str) -> list[str]:
    """Fetch the lines departing from a station using a TRIAS StopEventRequest."""
    xml_request = _create_stop_event_request_xml(stop_point_ref)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                data=xml_request.encode('utf-8'),
                headers={
                    'User-Agent': 'HomeAssistant',
                    'Content-Type': 'text/xml'
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error(f"Line lookup failed with status {response.status}")
                    return []

                response_text = await response.text()
                return _parse_lines_response(response_text)
    except Exception as e:
        _LOGGER.error(f"Error fetching lines: {e}")
        return []


def _create_stop_event_request_xml(stop_point_ref: str) -> str:
    """Create XML request for many upcoming departures to discover the station's lines."""
    now = datetime.now(timezone.utc).isoformat()

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Trias xmlns="http://www.vdv.de/trias" version="1.2">
  <ServiceRequest>
    <siri:RequestTimestamp xmlns:siri="http://www.siri.org.uk/siri">{now}</siri:RequestTimestamp>
    <siri:RequestorRef xmlns:siri="http://www.siri.org.uk/siri">homeassistant</siri:RequestorRef>
    <RequestPayload>
      <StopEventRequest>
        <Location>
          <LocationRef>
            <StopPointRef>{stop_point_ref}</StopPointRef>
          </LocationRef>
          <DepArrTime>{now}</DepArrTime>
        </Location>
        <Params>
          <NumberOfResults>200</NumberOfResults>
          <StopEventType>departure</StopEventType>
          <IncludePreviousCalls>false</IncludePreviousCalls>
          <IncludeOnwardCalls>false</IncludeOnwardCalls>
          <IncludeRealtimeData>false</IncludeRealtimeData>
        </Params>
      </StopEventRequest>
    </RequestPayload>
  </ServiceRequest>
</Trias>"""


def _parse_lines_response(xml_text: str) -> list[str]:
    """Parse the distinct line names from a StopEventRequest response."""
    try:
        root = ET.fromstring(xml_text)
        namespaces = {'trias': 'http://www.vdv.de/trias'}

        lines = {
            elem.text.strip()
            for elem in root.findall('.//trias:StopEvent//trias:PublishedLineName/trias:Text', namespaces)
            if elem.text and elem.text.strip()
        }
        return sort_lines(lines)
    except Exception as e:
        _LOGGER.error(f"Error parsing lines response: {e}")
        return []


def sort_lines(lines) -> list[str]:
    """Sort line names naturally, e.g. 1, 4, 30, 64, N5, S1."""
    def sort_key(line: str):
        digits = ''.join(ch for ch in line if ch.isdigit())
        number = int(digits) if digits else 0
        return (not line[:1].isdigit(), ''.join(ch for ch in line if not ch.isdigit()), number, line)

    return sorted(set(lines), key=sort_key)


def _lines_schema(available_lines: list[str], selected: list[str]) -> vol.Schema:
    """Build the multi-select schema for choosing lines."""
    return vol.Schema({
        vol.Optional(CONF_LINES, default=selected): SelectSelector(
            SelectSelectorConfig(
                options=sort_lines([*available_lines, *selected]),
                multiple=True,
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    })


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Steirische Linien."""

    VERSION = 2

    def __init__(self):
        """Initialize the config flow."""
        self._mode = None
        self._api_url = None
        self._station_name = None
        self._stations = []
        self._selected_station = None
        self._available_lines = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - mode selection."""
        if user_input is not None:
            self._mode = user_input[CONF_MODE]

            if self._mode == MODE_TRIP:
                return await self.async_step_trip()
            else:
                return await self.async_step_station()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_MODE_SCHEMA,
            description_placeholders={
                "trip_desc": "Search for transit connections between two locations using coordinates",
                "station_desc": "Monitor departures from a specific station by name"
            }
        )

    async def async_step_trip(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle trip planning mode configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_trip_input(self.hass, user_input)
                # Add mode to the config data
                user_input[CONF_MODE] = MODE_TRIP
                return self.async_create_entry(title=info["title"], data=user_input)
            except ValueError as err:
                _LOGGER.error(f"Validation error: {err}")
                errors["base"] = "invalid_coordinates"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="trip",
            data_schema=STEP_TRIP_SCHEMA,
            errors=errors,
            description_placeholders={
                "mode": "Trip Planning"
            }
        )

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle station mode configuration - get station name and API URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_station_input(self.hass, user_input)

                # Store API URL and station name for next step
                self._api_url = user_input[CONF_API_URL]
                self._station_name = user_input[CONF_STATION_NAME]

                # Search for stations
                _LOGGER.info(f"Searching for stations matching: {self._station_name}")
                self._stations = await search_stations(self._api_url, self._station_name)

                if not self._stations:
                    errors["base"] = "no_stations_found"
                else:
                    # Move to station selection step
                    return await self.async_step_select_station()

            except ValueError as err:
                _LOGGER.error(f"Validation error: {err}")
                errors["base"] = "invalid_station"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="station",
            data_schema=STEP_STATION_SCHEMA,
            errors=errors,
            description_placeholders={
                "mode": "Station Departures"
            }
        )

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle station selection from search results."""
        if user_input is not None:
            selected_ref = user_input["station"]

            # Find the selected station
            selected_station = next(
                (s for s in self._stations if s['stop_point_ref'] == selected_ref),
                None
            )

            if selected_station:
                self._selected_station = selected_station
                self._available_lines = await fetch_station_lines(
                    self._api_url, selected_station['stop_point_ref']
                )
                return await self.async_step_select_lines()

        # Build the selection schema with found stations
        station_options = {
            station['stop_point_ref']: station['display_name']
            for station in self._stations
        }

        select_station_schema = vol.Schema({
            vol.Required("station"): vol.In(station_options)
        })

        return self.async_show_form(
            step_id="select_station",
            data_schema=select_station_schema,
            description_placeholders={
                "count": str(len(self._stations))
            }
        )

    async def async_step_select_lines(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose which lines to show (empty = all lines)."""
        if user_input is not None:
            station = self._selected_station
            config_data = {
                CONF_MODE: MODE_STATION,
                CONF_API_URL: self._api_url,
                CONF_STATION_NAME: station['display_name'],
                CONF_STOP_POINT_REF: station['stop_point_ref'],
            }

            return self.async_create_entry(
                title=f"Station: {station['display_name']}",
                data=config_data,
                options={CONF_LINES: sort_lines(user_input.get(CONF_LINES, []))},
            )

        return self.async_show_form(
            step_id="select_lines",
            data_schema=_lines_schema(self._available_lines, []),
            description_placeholders={
                "station": self._selected_station['display_name'],
                "count": str(len(self._available_lines)),
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options (line filter) for an existing entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change which lines are shown."""
        if self._entry.data.get(CONF_MODE) != MODE_STATION:
            return self.async_abort(reason="not_supported")

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={CONF_LINES: sort_lines(user_input.get(CONF_LINES, []))},
            )

        available_lines = await fetch_station_lines(
            self._entry.data[CONF_API_URL], self._entry.data[CONF_STOP_POINT_REF]
        )
        selected = self._entry.options.get(CONF_LINES, [])

        return self.async_show_form(
            step_id="init",
            data_schema=_lines_schema(available_lines, selected),
            description_placeholders={
                "station": self._entry.data.get(CONF_STATION_NAME, ""),
                "count": str(len(available_lines)),
            }
        )