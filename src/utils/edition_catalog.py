"""Edition catalog: names, contents and platform facts for the depot selector.

The Steam Store's ``appdetails`` lists the packages (subs) a game sells through,
but the editions people see on the store ("Premium Edition", "Ancestral
Edition") are bundles, which Valve exposes only as store pages. PICS knows
package and depot truth but has no concept of a bundle, and the client's local
``packageinfo.vdf`` only knows the apps this machine has already seen.

This module joins the two views:

* the game's app page says which bundles name it as an edition,
* each bundle page says what the bundle is called and which apps it includes,
* the game's own app info (PICS via api.steamcmd.net) says which platforms the
  game supports and which apps are its DLCs.

The app info is what keeps a crossover bundle from being offered as an edition:
a bundle that also carries an unrelated game is not "this game, but more".

Every answer is cached on disk for a week. A failed fetch keeps the last
known-good answer instead of replacing it with a shorter list.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

from utils.helpers import get_cache_dir

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

_STORE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_APPINFO_URL = "https://api.steamcmd.net/v1/info/{appid}"
_APP_PAGE_URL = "https://store.steampowered.com/app/{appid}/?cc=us&l=en"
_BUNDLE_PAGE_URL = "https://store.steampowered.com/bundle/{bundle_id}/?cc=us&l=en"

_BUNDLE_LINK_RE = re.compile(r"/bundle/(\d+)")
_APP_LINK_RE = re.compile(r"/app/(\d+)/")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
#: The bundle page states its own name here; <title> may be the discount text.
_PAGEHEADER_RE = re.compile(
    r'class="pageheader[^"]*"[^>]*>(.*?)<', re.IGNORECASE | re.DOTALL
)

#: A store page that answers with Steam's front page is not a bundle page.
_FRONT_PAGE_MARKER = "Welcome to Steam"


def _cache_file() -> Path:
    return Path(get_cache_dir("editions")) / "catalog.json"


def _read_cache() -> Dict[str, Any]:
    path = _cache_file()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"apps": {}, "bundles": {}, "bundle_data": {}}
    except (OSError, ValueError) as exc:
        logger.debug("Edition cache unreadable, starting fresh: %s", exc)
        return {"apps": {}, "bundles": {}, "bundle_data": {}}
    if not isinstance(data, dict):
        return {"apps": {}, "bundles": {}, "bundle_data": {}}
    for bucket in ("apps", "bundles", "bundle_data"):
        data.setdefault(bucket, {})
        if not isinstance(data[bucket], dict):
            data[bucket] = {}
    return data


def _write_cache(data: Dict[str, Any]) -> None:
    path = _cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        tmp.replace(path)
    except OSError as exc:
        logger.debug("Edition cache could not be written: %s", exc)


def _fresh(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    stamp = entry.get("ts")
    if not isinstance(stamp, (int, float)):
        return False
    return (time.time() - stamp) < CACHE_TTL_SECONDS


def _store_get(url: str, timeout: float) -> Optional[str]:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": _STORE_UA})
    except requests.RequestException as exc:
        logger.debug("Store page %s could not be read: %s", url, exc)
        return None
    if response.status_code != 200 or not response.text:
        logger.debug("Store page %s answered HTTP %s", url, response.status_code)
        return None
    return response.text


def _clean_title(raw: str) -> str:
    name = re.sub(r"<[^>]+>", " ", raw or "")
    name = name.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    name = re.sub(r"\s+", " ", name).strip()
    suffix = " on Steam"
    if name.endswith(suffix):
        name = name[: -len(suffix)].strip()
    # A discounted bundle's <title> is "Save 60% on <name>".
    name = re.sub(r"^Save\s+\d+%\s+on\s+", "", name, flags=re.IGNORECASE).strip()
    return name


# --------------------------------------------------------------------- app facts

def fetch_app_facts(
    appid: str,
    *,
    timeout: float = 6.0,
    allow_network: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return ``{"name", "platforms", "dlc_appids"}`` for an app.

    ``None`` means "nothing known"; an empty list means "asked, and the answer
    was empty". The distinction lets callers keep a depot-based guess rather
    than pretending the game supports no platform at all.
    """
    key = str(appid).strip()
    if not key or key in ("0", "N/A", "unknown"):
        return None

    cache = _read_cache()
    entry = cache["apps"].get(key)
    if _fresh(entry):
        return {"name": str(entry.get("name") or ""),
                "platforms": list(entry.get("platforms") or []),
                "dlc_appids": list(entry.get("dlc_appids") or [])}
    if not allow_network:
        return None

    url = _APPINFO_URL.format(appid=key)
    text = _store_get(url, timeout)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except ValueError as exc:
        logger.debug("App info for %s is not JSON: %s", key, exc)
        return None

    node = ((payload or {}).get("data") or {}).get(key) or {}
    common = node.get("common") or {}
    extended = node.get("extended") or {}
    raw_os = common.get("oslist")
    if isinstance(raw_os, str):
        platforms = [p.strip().lower() for p in raw_os.split(",") if p.strip()]
    elif isinstance(raw_os, list):
        platforms = [str(p).strip().lower() for p in raw_os if str(p).strip()]
    else:
        platforms = []
    raw_dlc = extended.get("listofdlc")
    if isinstance(raw_dlc, str):
        dlc_appids = [d.strip() for d in raw_dlc.split(",") if d.strip()]
    elif isinstance(raw_dlc, list):
        dlc_appids = [str(d).strip() for d in raw_dlc if str(d).strip()]
    else:
        dlc_appids = []

    if not platforms and not dlc_appids:
        return None

    cache["apps"][key] = {
        "ts": int(time.time()),
        "name": str(common.get("name") or ""),
        "platforms": platforms,
        "dlc_appids": dlc_appids,
    }
    _write_cache(cache)
    return {"name": str(common.get("name") or ""),
            "platforms": platforms, "dlc_appids": dlc_appids}


def get_app_name(appid: str, *, allow_network: bool = True) -> str:
    """The game's own name, or ``""`` when it is not cached and offline."""
    facts = fetch_app_facts(appid, allow_network=allow_network)
    return str((facts or {}).get("name") or "").strip()


def get_supported_platforms(
    appid: str, *, allow_network: bool = True, timeout: float = 6.0
) -> Set[str]:
    """Platforms the game itself says it supports (linux, windows, macos)."""
    facts = fetch_app_facts(appid, timeout=timeout, allow_network=allow_network)
    return set(facts.get("platforms") or []) if facts else set()


def get_dlc_appids(
    appid: str, *, allow_network: bool = True, timeout: float = 6.0
) -> Set[str]:
    """The app's own DLC app ids, as published in its app info."""
    facts = fetch_app_facts(appid, timeout=timeout, allow_network=allow_network)
    return set(facts.get("dlc_appids") or []) if facts else set()


# --------------------------------------------------------------------- bundles

def discover_bundle_ids(
    appid: str, *, timeout: float = 6.0, allow_network: bool = True
) -> List[int]:
    """Bundle ids the game's store page links to as its editions."""
    key = str(appid).strip()
    if not key or key in ("0", "N/A", "unknown"):
        return []

    cache = _read_cache()
    entry = cache["bundles"].get(key)
    if _fresh(entry):
        return [int(b) for b in entry.get("ids") or []]
    if not allow_network:
        return []

    html = _store_get(_APP_PAGE_URL.format(appid=key), timeout)
    if html is None or _FRONT_PAGE_MARKER in html and "/bundle/" not in html:
        return [int(b) for b in (entry or {}).get("ids") or []]

    seen: List[int] = []
    for raw in _BUNDLE_LINK_RE.findall(html):
        value = int(raw)
        if value not in seen:
            seen.append(value)
    if not seen:
        # The page answered but lists no bundles. Cache the empty answer so a
        # game with no editions is not fetched on every menu open.
        logger.debug("App page for %s links no bundles", key)
    cache["bundles"][key] = {"ts": int(time.time()), "ids": seen}
    _write_cache(cache)
    return seen


def fetch_bundle(
    bundle_id: int, *, timeout: float = 6.0, allow_network: bool = True
) -> Optional[Dict[str, Any]]:
    """Return ``{"id", "name", "app_ids"}`` for a store bundle."""
    key = str(int(bundle_id))
    cache = _read_cache()
    entry = cache["bundle_data"].get(key)
    if _fresh(entry):
        return {
            "id": int(key),
            "name": str(entry.get("name") or ""),
            "app_ids": [str(a) for a in entry.get("app_ids") or []],
        }
    if not allow_network:
        return None

    html = _store_get(_BUNDLE_PAGE_URL.format(bundle_id=key), timeout)
    if html is None:
        return None
    if _FRONT_PAGE_MARKER in html and "tab_item" not in html:
        logger.debug("Bundle %s is delisted (front page answered)", key)
        return None

    header_match = _PAGEHEADER_RE.search(html)
    title_match = _TITLE_RE.search(html)
    name = _clean_title(header_match.group(1)) if header_match else ""
    if not name and title_match:
        name = _clean_title(title_match.group(1))
    app_ids: List[str] = []
    for raw in _APP_LINK_RE.findall(html):
        if raw == "0" or raw in app_ids:
            continue
        app_ids.append(raw)
    if not name or not app_ids:
        logger.debug("Bundle %s page held no usable name or items", key)
        return None

    cache["bundle_data"][key] = {
        "ts": int(time.time()),
        "name": name,
        "app_ids": app_ids,
    }
    _write_cache(cache)
    return {"id": int(key), "name": name, "app_ids": app_ids}


def bundle_editions(
    appid: str,
    dlc_appids: Optional[Iterable[str]] = None,
    *,
    allow_network: bool = True,
    timeout: float = 6.0,
) -> List[Dict[str, Any]]:
    """Editions the store sells as bundles, minus pure crossovers.

    A bundle qualifies when it includes this game and its DLC apps are at least
    as numerous as the apps outside the game's own family. Without the DLC list
    a deluxe edition cannot be told from a crossover, so callers pass the list
    when they have it.
    """
    base = str(appid).strip()
    dlc_set: Optional[Set[str]] = None
    if dlc_appids is not None:
        dlc_set = {str(d).strip() for d in dlc_appids if str(d).strip()}

    editions: List[Dict[str, Any]] = []
    for bundle_id in discover_bundle_ids(base, timeout=timeout, allow_network=allow_network):
        bundle = fetch_bundle(bundle_id, timeout=timeout, allow_network=allow_network)
        if not bundle:
            continue
        apps = {str(a) for a in bundle["app_ids"]}
        if base not in apps:
            continue
        if dlc_set is not None:
            family = (apps - {base}) & dlc_set
            others = apps - {base} - family
            # A crossover bundle (this game plus an unrelated series) carries
            # mostly apps that are not this game's DLCs. Keep bundles whose
            # family is at least as large as the rest, so an edition that also
            # ships an upgrade or soundtrack pack still qualifies.
            if not family or len(family) < len(others):
                logger.debug(
                    "Bundle %s skipped for %s: it carries %d DLC app(s) and %d outsider(s)",
                    bundle_id, base, len(family), len(others),
                )
                continue
        editions.append({
            "id": f"bundle:{bundle['id']}",
            "name": bundle["name"],
            "app_ids": sorted(apps, key=lambda value: (len(value), value)),
            "source": "bundle",
        })
    return editions
