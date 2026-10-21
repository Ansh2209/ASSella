import json
import logging
import re
import urllib.request
from typing import Any, Dict, Optional, Tuple

from utils.paths import Paths

logger = logging.getLogger(__name__)


def _load_version() -> str:
    """Load the application version from the resource file."""
    version_file = Paths.resource("version")

    if not version_file.exists():
        logger.warning("Version file not found, using unknown version")
        return "unknown version"

    try:
        with open(version_file, "r", encoding="utf-8") as f:
            return f.read().strip() or "unknown version"
    except Exception as e:
        logger.warning(f"Failed to read version file: {e}")
        return "unknown version"


app_version = _load_version()


def extract_semver(raw_version: str) -> str:
    """Extract a clean semver string from a raw version string (e.g. 'v3.0.0-beta.1' -> '3.0.0-beta.1')."""
    if not raw_version:
        return ""
    v = raw_version.strip()
    m = re.search(r'v?(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)', v, re.IGNORECASE)
    if m:
        return m.group(1)
    return v.lstrip("vV")


def parse_version(v_str: str) -> Tuple[int, int, int, int, int]:
    """
    Parse a version string into a comparable 5-element tuple:
    (major, minor, patch, pre_tag_weight, pre_num)
    Weights:
      canary / testing: -4
      dev / alpha: -3
      beta: -2
      rc: -1
      clean release: 0
      hotfix / post: 1
    """
    if not v_str:
        return (0, 0, 0, 0, 0)
    m = re.match(r'^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-?([a-zA-Z]+)(?:\.?(\d+))?)?(?:-(.*))?$', v_str.strip())
    if m:
        major = int(m.group(1))
        minor = int(m.group(2))
        patch = int(m.group(3)) if m.group(3) else 0
        pre_tag = (m.group(4) or "").lower()
        pre_num = int(m.group(5)) if m.group(5) else 0
        pre_extra = m.group(6) or ""

        if pre_tag:
            if "canary" in pre_tag or "testing" in pre_tag or "alpha" in pre_tag:
                pre_val = -4
            elif "dev" in pre_tag:
                pre_val = -3
            elif "beta" in pre_tag:
                pre_val = -2
            elif "rc" in pre_tag:
                pre_val = -1
            else:
                pre_val = -5
        elif pre_extra:
            if "hotfix" in pre_extra.lower():
                hm = re.search(r'\d+', pre_extra)
                pre_val = 1
                pre_num = int(hm.group(0)) if hm else 1
            else:
                pre_val = -5
                pre_num = 0
        else:
            pre_val = 0
            pre_num = 0
        return (major, minor, patch, pre_val, pre_num)
    return (0, 0, 0, 0, 0)


def get_branch_from_version(ver_str: str) -> str:
    """Determine the release branch label from a version string."""
    v = ver_str.lower()
    if any(x in v for x in ("canary", "testing")):
        return "canary"
    elif any(x in v for x in ("beta", "dev", "rc")):
        return "beta"
    return "main"


def get_effective_update_channel(configured_channel: Optional[str] = None, current_version: Optional[str] = None) -> str:
    """Resolve the effective update channel ('stable', 'beta', 'canary')."""
    ch = (configured_channel or "").strip().lower()
    if ch in ("stable", "beta", "canary"):
        return ch

    cur = current_version or app_version
    cur_clean = extract_semver(cur).lower()
    if any(x in cur_clean for x in ("canary", "testing", "alpha")):
        return "canary"
    elif any(x in cur_clean for x in ("beta", "dev", "rc")):
        return "beta"
    return "stable"


def check_for_updates(
    configured_channel: Optional[str] = None,
    current_version: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Check GitHub for ASSella application updates.
    
    Returns dict:
      - status: 'update_available' | 'up_to_date' | 'offline'
      - remote_version: str or None
      - local_version: str
      - channel: str ('stable' | 'beta' | 'canary')
      - is_cross_channel: bool
      - error: str or None
    """
    cur = current_version or app_version
    local_clean = extract_semver(cur)
    channel = get_effective_update_channel(configured_channel, cur)
    remote_clean = None
    last_error = None

    # 1. Primary check: GitHub Releases API
    try:
        api_url = "https://api.github.com/repos/niwia/ASSella/releases"
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "ASSella-Updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            releases = json.loads(response.read().decode("utf-8"))
            for r in releases:
                tag = str(r.get("tag_name", "")).strip()
                tag_lower = tag.lower()

                is_canary = any(x in tag_lower for x in ("canary", "testing"))
                is_beta = any(x in tag_lower for x in ("beta", "dev", "rc")) and not is_canary

                # Decouple stable release detection from GitHub's manual 'prerelease' flag
                clean_tag = tag.lstrip("vV")
                is_clean_semver = bool(re.match(r"^\d+\.\d+(\.\d+)?$", clean_tag))
                is_stable = is_clean_semver and not is_canary and not is_beta

                # Channel filtering
                if channel == "stable" and not is_stable:
                    continue
                elif channel == "beta" and not is_beta and not is_stable:
                    continue
                elif channel == "canary" and not is_canary:
                    continue

                assets = [a.get("name", "") for a in r.get("assets", []) if isinstance(a, dict)]
                has_appimage = any(
                    a.lower().endswith(".appimage")
                    and not a.lower().endswith(".zsync")
                    and not a.lower().endswith(".sha256")
                    for a in assets
                )

                if has_appimage:
                    remote_clean = extract_semver(tag)
                    break
    except Exception as e:
        last_error = str(e)
        logger.debug(f"[Updater] GitHub Releases API check error: {e}")

    # 2. Fallback check: raw version file on GitHub branch
    if not remote_clean:
        try:
            branch = channel if channel in ("beta", "canary") else "main"
            url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
            req = urllib.request.Request(url, headers={"User-Agent": "ASSella-Updater"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                remote_raw = response.read().decode("utf-8").strip()
                remote_clean = extract_semver(remote_raw)
        except Exception as e:
            last_error = str(e)
            logger.debug(f"[Updater] Fallback raw version check error: {e}")

    if not remote_clean:
        return {
            "status": "offline",
            "remote_version": None,
            "local_version": local_clean,
            "channel": channel,
            "is_cross_channel": False,
            "error": last_error or "Could not determine latest version.",
        }

    is_local_canary = any(x in local_clean.lower() for x in ("canary", "testing", "devtest"))
    is_local_beta = any(x in local_clean.lower() for x in ("beta", "dev", "rc")) and not is_local_canary
    is_local_stable = not is_local_canary and not is_local_beta

    channel_mismatch = (
        (channel == "stable" and not is_local_stable)
        or (channel == "beta" and not is_local_beta)
        or (channel == "canary" and not is_local_canary)
    )

    is_newer = parse_version(remote_clean) > parse_version(local_clean)
    is_cross = bool(channel_mismatch and remote_clean != local_clean)

    if is_cross or is_newer:
        return {
            "status": "update_available",
            "remote_version": remote_clean,
            "local_version": local_clean,
            "channel": channel,
            "is_cross_channel": is_cross,
            "error": None,
        }

    return {
        "status": "up_to_date",
        "remote_version": remote_clean,
        "local_version": local_clean,
        "channel": channel,
        "is_cross_channel": False,
        "error": None,
    }

