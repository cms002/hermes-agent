"""Location Services Tool — geographic location detection with cross-platform support.

Uses platform-native geolocation when available:
  - macOS: CoreLocationCLI (precise GPS coordinates via the `location` CLI tool)
  - Windows: PowerShell + geolocation APIs
  - Linux: GeoClue (via `geoclue` CLI) or fallback to IP-based geolocation

All location data is gathered lazily on first use, avoiding network delay
at tool startup. Precise GPS coordinates are reverse-geocoded via
Nominatim (OpenStreetMap) for address details. IP-based geolocation
(ipinfo.io) serves as a cross-platform fallback.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys

import requests

from tools.registry import registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cached location result (None = not yet loaded, dict = loaded)
# ---------------------------------------------------------------------------
_LOCATION_CACHE = None


def _run_command(cmd, timeout=10):
    """Run a shell command and return its stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug("Command %s failed: %s", cmd, e)
    return ""


def _get_ip_address():
    """Get the public IP address via multiple fallback services."""
    for url in [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                ip = resp.text.strip()
                if ip:
                    return ip
        except Exception as e:
            logger.debug("IP lookup via %s failed: %s", url, e)
    return ""


def _get_precise_location_macos():
    """Get precise GPS coordinates on macOS using CoreLocationCLI.

    Requires CoreLocationCLI (brew install --cask corelocationcli).
    Returns (lat, lon, timezone) or (None, None, None) if unavailable.
    """
    if shutil.which("CoreLocationCLI") is None:
        logger.debug("CoreLocationCLI not installed")
        return None, None, None

    output = _run_command(
        ["CoreLocationCLI", "--format", "%latitude,%longitude|%timezone"],
        timeout=15,
    )
    if not output:
        return None, None, None

    # Validate: should contain coords like "47.6062,-122.3321|America/Los_Angeles"
    if "|" not in output or "," not in output.split("|")[0]:
        logger.debug("CoreLocationCLI output format invalid: %s", output)
        return None, None, None

    coords, timezone = output.split("|", 1)
    coords = coords.strip()
    timezone = timezone.strip()

    # Validate timezone format
    import re
    if not re.match(r'^[A-Za-z]+/[A-Za-z_]+$|^(UTC|GMT)$', timezone):
        timezone = ""

    # Validate coordinates are not 0,0
    if coords == "0.000000,0.000000":
        return None, None, None

    return coords, None, timezone


def _get_precise_location_windows():
    """Get approximate location on Windows using PowerShell.

    Uses PowerShell to query the Windows Location API. Requires
    location permissions to be enabled in Windows settings.
    Returns (coords_str, None, timezone) or (None, None, None).
    """
    ps_script = (
        "Add-Type -AssemblyName System.Device; "
        "$loc = New-Object System.Device.Location.GeoCoordinateWatcher; "
        "$loc.Start(); "
        "Start-Sleep -Seconds 5; "
        "$c = $loc.Position.Location; "
        "Write-Output \"$($c.Latitude),$($c.Longitude)\"; "
        "$loc.Stop()"
    )
    output = _run_command(
        ["powershell", "-Command", ps_script],
        timeout=15,
    )
    if output and "," in output:
        coords = output.strip()
        timezone = _get_timezone_windows()
        return coords, None, timezone
    return None, None, None


def _get_timezone_windows():
    """Get timezone on Windows via PowerShell."""
    output = _run_command(
        ["powershell", "-Command",
         "(Get-TimeZone).Id"],
        timeout=5,
    )
    return output or ""


def _get_precise_location_linux():
    """Get location on Linux using GeoClue (if available).

    Requires geoclue-2.0 (typically pre-installed on modern distros).
    Falls back to None if not available.
    """
    # Try geoclue via Python dbus if available
    try:
        import gi
        gi.require_version('Geoclue', '2.0')
        from gi.repository import Geoclue
        # This is complex and often requires running as a service
        # For now, fall back to IP-based
    except Exception:
        pass

    # Try the `whereami` or `geoclue` CLI tools if available
    for cmd in ["whereami", "geocli"]:
        path = shutil.which(cmd)
        if path:
            output = _run_command([path], timeout=10)
            if output and "," in output:
                return output.strip(), None, ""

    return None, None, ""


def _get_ip_location(ip_address):
    """Get location info from IP address using ipinfo.io.

    Returns a dict with city, region, country, coords, isp, timezone.
    """
    if not ip_address:
        return {}

    try:
        resp = requests.get(f"https://ipinfo.io/{ip_address}/json", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug("IP location lookup failed: %s", e)

    return {}


def _reverse_geocode(lat, lon):
    """Reverse geocode coordinates using Nominatim (OpenStreetMap).

    Returns a dict with address details (POI, street, city, state, etc.).
    """
    if not lat or not lon:
        return {}

    # Nominatim requires a User-Agent and has rate limiting
    headers = {"User-Agent": "hermes-agent/1.0"}
    params = {
        "format": "json",
        "lat": lat,
        "lon": lon,
        "zoom": 18,
        "addressdetails": 1,
    }

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params=params,
            headers=headers,
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug("Nominatim reverse geocode failed: %s", e)

    return {}


def _get_timezone_from_coords(lat, lon):
    """Get timezone from coordinates using the TimeZoneDB API (via wttr.in fallback)."""
    # Try wttr.in for timezone from coordinates
    try:
        resp = requests.get(
            f"https://wttr.in/{lat},{lon}",
            params={"format": "j1"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "timezone" in data:
                return data["timezone"].get("name", "") or data["timezone"].get("id", "")
    except Exception:
        pass

    return ""


def _detect_location():
    """Detect the user's location using the best available method.

    Returns a dict with all location fields populated.
    Detection order:
      1. Platform-native precise location (CoreLocationCLI/Geoclue)
      2. IP-based geolocation (ipinfo.io)
      3. Default fallback
    """
    global _LOCATION_CACHE

    if _LOCATION_CACHE is not None:
        return _LOCATION_CACHE

    system = platform.system()
    location = {
        "location": "",
        "location_method": "",
        "precise_coords": "",
        "precise_address": "",
        "poi_name": "",
        "poi_type": "",
        "poi_class": "",
        "house_number": "",
        "street": "",
        "city": "",
        "county": "",
        "state": "",
        "country": "",
        "country_code": "",
        "postcode": "",
        "timezone": "",
        "ip_coords": "",
        "ip_city": "",
        "ip_address": "",
        "ip_isp": "",
        "display_city": "",
    }

    # ── STEP 1: Try platform-native precise location ──
    lat = lon = None
    precise_coords = None
    timezone = ""

    if system == "Darwin":
        precise_coords, _, timezone = _get_precise_location_macos()
    elif system == "Windows":
        precise_coords, _, timezone = _get_precise_location_windows()
    elif system == "Linux":
        precise_coords, _, timezone = _get_precise_location_linux()

    if precise_coords:
        parts = precise_coords.split(",")
        if len(parts) == 2:
            lat, lon = parts[0].strip(), parts[1].strip()

        location["precise_coords"] = precise_coords
        location["location"] = precise_coords
        location["location_method"] = f"{system} {('CoreLocation (precise GPS)' if system == 'Darwin' else '(platform native)')}"

        if timezone:
            location["timezone"] = timezone

        # ── STEP 1B: Reverse geocode with Nominatim ──
        if lat and lon and precise_coords != "0.000000,0.000000":
            addr = _reverse_geocode(lat, lon)
            if addr:
                location["precise_address"] = addr.get("display_name", "")
                location["poi_name"] = addr.get("name", "")
                location["poi_type"] = addr.get("type", "")
                location["poi_class"] = addr.get("class", "")

                address = addr.get("address", {})
                location["house_number"] = address.get("house_number", "")
                location["street"] = address.get("road", "")
                location["city"] = address.get("city") or address.get("town") or address.get("village", "")
                location["county"] = address.get("county", "")
                location["state"] = address.get("state", "")
                location["country"] = address.get("country", "")
                location["country_code"] = address.get("country_code", "").upper()
                location["postcode"] = address.get("postcode", "")

                if lat and lon and not timezone:
                    tz = _get_timezone_from_coords(lat, lon)
                    if tz:
                        location["timezone"] = tz

    # ── STEP 2: Get IP address and IP-based location ──
    ip_address = _get_ip_address()
    location["ip_address"] = ip_address

    if ip_address:
        ip_data = _get_ip_location(ip_address)
        if ip_data:
            location["ip_coords"] = ip_data.get("loc", "")
            location["ip_city"] = ip_data.get("city", "")
            location["ip_isp"] = ip_data.get("org", "")

            ip_timezone = ip_data.get("timezone", "")
            if not location["timezone"] and ip_timezone:
                location["timezone"] = ip_timezone

            # Use IP-based location as fallback if no precise location
            if not location["location"]:
                location["location"] = ip_data.get("city", "")
                location["location_method"] = "IP geolocation (approximate)"
                if not location["display_city"]:
                    location["display_city"] = ip_data.get("city", "")

    # ── STEP 3: Default fallback ──
    if not location["location"]:
        location["location"] = "Seattle"
        location["display_city"] = "Seattle"
        location["ip_city"] = "Seattle, WA, US"
        location["ip_coords"] = "47.6062,-122.3321"
        location["location_method"] = "default fallback"
        if not location["ip_address"]:
            location["ip_address"] = "Unable to detect"
        if not location["ip_isp"]:
            location["ip_isp"] = "Network unavailable"

    # Fill in defaults for display
    if not location["display_city"]:
        location["display_city"] = location["location"]
    if not location["ip_city"]:
        location["ip_city"] = "Unknown"
    if not location["ip_address"]:
        location["ip_address"] = "Unable to detect"
    if not location["ip_isp"]:
        location["ip_isp"] = "Unknown"

    _LOCATION_CACHE = location
    return location


def _reset_location_cache():
    """Clear the cached location so the next call re-detects."""
    global _LOCATION_CACHE
    _LOCATION_CACHE = None


def location_get(args, **kwargs):
    """Get the user's current geographic location with full address details.

    Uses CoreLocationCLI on macOS for precise GPS, or IP-based geolocation
    (ipinfo.io) as a cross-platform fallback. Location is cached after first
    detection. Use refresh=true to bypass cache.

    Returns coordinates, address, POI/business name, city, state, country,
    timezone, and IP info.
    """
    refresh = args.get("refresh", False)
    if refresh:
        _reset_location_cache()

    location = _detect_location()

    # Build a clean response — only include non-empty fields
    response = {"status": "ok", "location": {}}
    for key, value in location.items():
        if value:
            response["location"][key] = value
    response["location"]["detection_method"] = location.get("location_method", "unknown")

    # Build a human-friendly summary that highlights the business/POI name
    summary_parts = []
    if location.get("poi_name"):
        summary_parts.append(f"{location['poi_name']}")
    if location.get("street") and location.get("house_number"):
        summary_parts.append(f"{location['house_number']} {location['street']}")
    elif location.get("street"):
        summary_parts.append(location["street"])
    if location.get("city"):
        summary_parts.append(location["city"])
    if location.get("state"):
        summary_parts.append(location["state"])
    if location.get("postcode"):
        summary_parts.append(location["postcode"])
    if location.get("country"):
        summary_parts.append(location["country"])
    response["location"]["formatted_address"] = ", ".join(p for p in summary_parts if p)

    return json.dumps(response)


def location_get_coords(args, **kwargs):
    """Get the user's current GPS coordinates (latitude, longitude)."""
    refresh = args.get("refresh", False)
    if refresh:
        _reset_location_cache()

    location = _detect_location()

    coords = {"status": "ok"}
    if location["precise_coords"]:
        coords["source"] = "precise"
        coords["coords"] = location["precise_coords"]
    elif location["ip_coords"]:
        coords["source"] = "ip"
        coords["coords"] = location["ip_coords"]
    else:
        coords["source"] = "none"
        coords["coords"] = ""

    coords["latitude"] = location["precise_coords"].split(",")[0] if location["precise_coords"] else ""
    coords["longitude"] = location["precise_coords"].split(",")[1] if location["precise_coords"] else ""
    coords["timezone"] = location.get("timezone", "")
    coords["precision_note"] = (
        "GPS coordinates from CoreLocationCLI" if location["precise_coords"] else
        "Approximate coordinates from IP geolocation" if location["ip_coords"] else
        "Could not detect coordinates"
    )

    return json.dumps(coords)


def location_reverse_geocode(args, **kwargs):
    """Reverse geocode coordinates to a human-readable address.

    If no coordinates are provided, uses the detected current location.
    """
    lat = args.get("latitude", "")
    lon = args.get("longitude", "")

    # If no coordinates provided, use detected location
    if not lat or not lon:
        location = _detect_location()
        if location["precise_coords"]:
            parts = location["precise_coords"].split(",")
            lat = lat or parts[0]
            lon = lon or parts[1]
        elif location["ip_coords"]:
            parts = location["ip_coords"].split(",")
            lat = lat or parts[0]
            lon = lon or parts[1]

    if not lat or not lon:
        return json.dumps({"error": "Could not determine coordinates for reverse geocoding"})

    addr = _reverse_geocode(lat.strip(), lon.strip())
    if not addr:
        return json.dumps({"error": "Reverse geocoding failed — coordinates may be invalid or Nominatim is unavailable"})

    response = {"status": "ok", "coordinates": f"{lat},{lon}", "address": {}}
    address = addr.get("address", {})
    response["address"]["display_name"] = addr.get("display_name", "")
    response["address"]["poi_name"] = addr.get("name", "")
    response["address"]["poi_type"] = addr.get("type", "")
    response["address"]["poi_class"] = addr.get("class", "")
    response["address"]["house_number"] = address.get("house_number", "")
    response["address"]["road"] = address.get("road", "")
    response["address"]["city"] = address.get("city") or address.get("town") or address.get("village", "")
    response["address"]["county"] = address.get("county", "")
    response["address"]["state"] = address.get("state", "")
    response["address"]["country"] = address.get("country", "")
    response["address"]["country_code"] = address.get("country_code", "").upper()
    response["address"]["postcode"] = address.get("postcode", "")

    return json.dumps(response, indent=2)


def location_get_ip(args, **kwargs):
    """Get the user's public IP address and ISP information."""
    ip_address = _get_ip_address()

    if not ip_address:
        return json.dumps({"error": "Could not detect public IP address"})

    ip_data = _get_ip_location(ip_address) if ip_address else {}

    response = {
        "status": "ok",
        "ip_address": ip_address,
    }

    if ip_data:
        response["isp"] = ip_data.get("org", "")
        response["city"] = ip_data.get("city", "")
        response["region"] = ip_data.get("region", "")
        response["country"] = ip_data.get("country", "")
        response["coords"] = ip_data.get("loc", "")
        response["timezone"] = ip_data.get("timezone", "")
    else:
        response["isp"] = ""
        response["message"] = "IP detected but location details unavailable"

    return json.dumps(response)


def location_get_timezone(args, **kwargs):
    """Get the user's current timezone."""
    location = _detect_location()
    tz = location.get("timezone", "")

    if not tz:
        # Try to get timezone from IP
        ip_data = _get_ip_location(location.get("ip_address", ""))
        if ip_data:
            tz = ip_data.get("timezone", "")

    if not tz:
        return json.dumps({"error": "Could not determine timezone"})

    response = {
        "status": "ok",
        "timezone": tz,
        "method": location.get("location_method", "unknown"),
    }

    # Add UTC offset if available
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo(tz))
        offset = now.utcoffset()
        response["utc_offset"] = str(offset)
        response["current_time"] = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        pass

    return json.dumps(response)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

LOCATION_GET_SCHEMA = {
    "name": "location_get",
    "description": (
        "Get the user's current geographic location with full address details. "
        "Uses CoreLocationCLI on macOS for precise GPS, or IP-based geolocation "
        "as a cross-platform fallback. Location is cached after first detection. "
        "Returns coordinates, address, POI, city, state, country, timezone, and IP info."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "refresh": {
                "type": "boolean",
                "description": "If true, bypass cache and re-detect location (slower).",
                "default": False,
            }
        },
    },
}

LOCATION_GET_COORDS_SCHEMA = {
    "name": "location_get_coords",
    "description": (
        "Get the user's current GPS latitude and longitude coordinates. "
        "Uses CoreLocationCLI on macOS (precise GPS), or ipinfo.io for "
        "approximate coordinates based on IP address. Includes timezone if available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "refresh": {
                "type": "boolean",
                "description": "If true, bypass cache and re-detect location.",
                "default": False,
            }
        },
    },
}

LOCATION_REVERSE_GEOCODE_SCHEMA = {
    "name": "location_reverse_geocode",
    "description": (
        "Reverse geocode a latitude/longitude pair to a human-readable address. "
        "Uses Nominatim (OpenStreetMap). If no coordinates are provided, "
        "uses the detected current location."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "latitude": {
                "type": "string",
                "description": "Latitude (e.g. '47.6062'). If omitted, uses detected location.",
            },
            "longitude": {
                "type": "string",
                "description": "Longitude (e.g. '-122.3321'). If omitted, uses detected location.",
            },
        },
    },
}

LOCATION_GET_IP_SCHEMA = {
    "name": "location_get_ip",
    "description": (
        "Get the user's public IP address and ISP information. "
        "Uses ipify.org as the primary service with fallbacks. "
        "Also attempts to resolve ISP and approximate location from IP."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

LOCATION_GET_TIMEZONE_SCHEMA = {
    "name": "location_get_timezone",
    "description": (
        "Get the user's current timezone. Uses CoreLocationCLI on macOS "
        "for precise timezone detection, or ipinfo.io as a fallback. "
        "Returns timezone name, UTC offset, and current time."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="location_get",
    toolset="web",
    schema=LOCATION_GET_SCHEMA,
    handler=location_get,
    check_fn=lambda: True,
    requires_env=[],
    emoji="📍",
)

registry.register(
    name="location_get_coords",
    toolset="web",
    schema=LOCATION_GET_COORDS_SCHEMA,
    handler=location_get_coords,
    check_fn=lambda: True,
    requires_env=[],
    emoji="📍",
)

registry.register(
    name="location_reverse_geocode",
    toolset="web",
    schema=LOCATION_REVERSE_GEOCODE_SCHEMA,
    handler=location_reverse_geocode,
    check_fn=lambda: True,
    requires_env=[],
    emoji="🗺",
)

registry.register(
    name="location_get_ip",
    toolset="web",
    schema=LOCATION_GET_IP_SCHEMA,
    handler=location_get_ip,
    check_fn=lambda: True,
    requires_env=[],
    emoji="🌐",
)

registry.register(
    name="location_get_timezone",
    toolset="web",
    schema=LOCATION_GET_TIMEZONE_SCHEMA,
    handler=location_get_timezone,
    check_fn=lambda: True,
    requires_env=[],
    emoji="🕐",
)