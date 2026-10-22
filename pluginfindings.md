# Plugin findings — Hubcap Manifest API & the SLSsteam Lua plugin

Working notes for `spacebunny1.6beta.lua`. Everything here was verified live against
`https://hubcapmanifest.com/api/v1` with the account's real API key on 2026-10-21.

**Status: local only.** Nothing is published, no manifest is regenerated, no ASSella code
is changed. The plugin is named `spacebunny1.6beta.lua` and is tested from the local
SLSsteam plugin directory.

---

## 1. The endpoint that makes a plugin-only design possible

ASSella talks to Hubcap's *bundle* endpoint:

```
GET /api/v1/manifest/{app_id}[?force_update=true]   → application/zip
```

The bundle is a **deflate-compressed ZIP** (verified: every entry uses compression
method 8). A Lua plugin cannot unzip that — there is no inflate in the SLSsteam Lua
environment and no shell out. On its own, that kills the "let the plugin fetch keys by
itself" idea.

But Hubcap also serves the same content as **plain text**:

```
GET /api/v1/lua/{app_id}          → text/plain
GET /api/v1/lua/basegame/{app_id} → text/plain
GET /api/v1/lua/dlc/{app_id}      → text/plain
```

Example (`/api/v1/lua/2954010`, 926 bytes, `Burn With Me`):

```lua
-- 2954010's Lua and Manifest Created by Hubcap Manifest
-- Burn With Me
-- Created: October 21, 2026 at 17:43:35 EDT
-- Build: 26115562 (public branch)
-- Total Depots: 3
-- Total DLCs: 0

-- MAIN APPLICATION
addappid(2954010, 1, "d72e9987d3d4dac8c686942a66b27e69681d203fd04ef333486227f1f31017c6") -- Burn With Me
-- MAIN APP DEPOTS
addappid(2954011, 1, "80da182082267ac93e826389cb7eaa2a9e65d68d958bc3488cefa1d2f34f176f") -- Depot 2954011
addappid(2954014, 1, "eec5298794953e13a044b6a24d61e6912d0ad9801a2968d396aa08d33ab67fa3") -- Depot 2954014
addappid(2954015, 1, "086fb2e044231f6f1798dd6d03543297716e137f57cd092061a0f4ba90101bf1") -- Depot 2954015
```

`curl.downloadString` returns exactly this. One request, a few kB, all the keys of the
app. No ZIP, no inflate, no ASSella.

### Auth

All three endpoints need the API key:

```
Authorization: Bearer smm_...
X-API-Key: smm_...          (equivalent)
?api_key=smm_...            (query parameter — leaks into proxy logs, avoid)
```

Without a key: `401 {"detail":"API key required. Provide via Authorization header (Bearer token), X-API-Key header, or api_key query parameter."}`

### Size reality check

Measured on `227300` (Euro Truck Simulator 2, 109 manifests, many DLCs):

| Endpoint | Bytes | `addappid` lines | lines with a key |
|---|---|---|---|
| `/lua/227300` (full) | 30 157 | 225 | 110 |
| `/lua/basegame/227300` | 1 318 | 7 | 7 |
| `/lua/dlc/227300` | 28 417 | 206 | 103 |

One `/lua/{app}` call already contains the basegame **and** the DLC sections. The
`basegame`/`dlc` variants are useful when you only want one part and want to keep the
response small — but they cost the same quota (1 call each), so splitting into two
requests is strictly worse than one full request.

---

## 2. LUA format

Identical across every app inspected (`227300`, `995770`, `996770`, `1042550`, `2954010`):

| Line | Meaning |
|---|---|
| `addappid(<depotId>, 1, "<64 lowercase hex>")` | this depot **has** a key |
| `addappid(<appId>)` | a DLC app entry or an empty depot — **no key** |
| `-- addappid(<depotId>)` (commented out) | an EMPTY depot (no content on any branch) — no key |
| `setManifestid(<depot>, "<manifestId>", <size>)` | present in some LUAs only (2954010 has it, ETS2 does not) — optional |

Section comments: `-- MAIN APPLICATION`, `-- MAIN APP DEPOTS`,
`-- DLCS WITH DEDICATED DEPOTS`, `-- SHARED DEPOTS (from other apps)`,
`-- EMPTY DEPOTS (no content on any branch)`.

Header lines carry the game name, build id + branch, build date/epoch,
`-- Total Depots: N`, `-- Total DLCs: N (5 excluded)`, `-- Blacklisted Depots: N`
plus the blacklisted depot list.

Notes:

* The second field is always `1` in practice, but ASSella's own regex accepts any
  number: `addappid\((\d+),\s*\d+,\s*["']([a-fA-F0-9]{64})["']\)`. Reuse it.
* For Hubcap DLCs **the depot id equals the DLC app id** — e.g. the "Going East!" DLC
  is `addappid(227310)` and `addappid(227310, 1, "<key>")`.
* `?branch=beta` and `?branch=public` on `/lua/{app}` are **ignored** — both return the
  identical public-branch body. The LUA endpoint always serves the default branch.

---

## 3. Quota — only `/lua/*` costs anything

Account `badniwia` (`user_id` 427132856645582861):

```
daily_limit      135
role_daily_limit 135
daily_usage      <counter for today>
api_key_usage_count   <lifetime counter>
```

Measured counter movement:

| Call | Δ daily_usage |
|---|---|
| `GET /lua/{app}` (200) | **+1** |
| `GET /lua/{unknown}` (404) | **+1** |
| `GET /status/{app}` | 0 (free) |
| `GET /manifest/{app}/contents` | 0 (free) |
| `GET /search?q=...` | 0 (free) |
| `GET /depot-keys` | 0 (free) |

Consequences for the plugin:

* A failed `/lua` call costs the same as a successful one → **never probe blindly**.
  Check for free first (see §5).
* One `/lua/{app}` per app per session is plenty — the plugin caches the result.
* 135/day is plenty for a normal session; the budget only matters for a bulk library scan.

---

## 4. What Hubcap does NOT have

Probed and confirmed absent (all unknown routes return **HTTP 200 + the SPA HTML page**,
so the plugin must validate the body, not the status code):

| Wanted | Result |
|---|---|
| depot → app mapping | `/search?q=<depot>` → 0 matches; `/depot/<id>`, `/depots/<id>`, `/manifest/depot/<id>`, `/manifest?depot_id=` → SPA HTML |
| single depot key | `/depot-key/<id>`, `/depot/<id>/key`, `/keys/<id>`, `/depot-keys/<id>`, `/generate/depot-key?depot_id=` → SPA HTML |
| DLC → parent app | `/status/<dlcAppId>` returns 200 JSON but has **no parent field**; `/search` results have `game_id, game_name, header_image, uploaded_date, manifest_available` — no parent |
| DLC app LUA | `/lua/227310`, `/lua/basegame/227310`, `/lua/dlc/227310` → **404** (HTML "Page Not Found", 2599 B) |

**A DLC's depot key is only reachable through its parent game's LUA**
(`/lua/dlc/{parentAppId}` or `/lua/{parentAppId}`), and Hubcap never tells you which
app is the parent.

`GET /depot-keys` (7.3 MB, free) is only an *index*: `existing_depot_ids` (330 992),
`pending_depot_ids` (46 891), `depot_ids` (377 883) — no key material, no app mapping.
It does give a free three-state answer per depot: key available / manifest pending /
Hubcap does not know the depot at all.

---

## 5. The resolution algorithm

The plugin's problem: Steam asks for a decryption key by **depot id only**
(`GetBinary` receives the channel name `"<depotId>\DecryptionKey"`). Hubcap only
answers by **app id**. Something must bridge the two.

**The bridge is the MRC hook.** Its C signature is

```c
bool (*GetMRC_t)(void*, uint32_t appId, uint32_t depotId, uint64_t manifestId,
                  const char* branch, uint64_t* out);
```

Steam calls it with the app id **and** the depot id together, exactly when it starts a
download of that depot. So `hkGetMRC` is where the plugin learns "depot X belongs to
app Y".

Order of resolution for a missing depot `D`:

1. **In-memory cache** `app → {depot → key}` — instant, no network.
2. **Persistent depot→app index** built from every LUA the plugin has ever fetched
   (and from `app_hints` in `spacebunny.conf.lua`).
3. `hkGetMRC` recorded a candidate app `A` for `D`.
4. **Free check**: `GET /manifest/{A}/contents`. If `zip_exists` is false or
   `manifest_count` is 0, Hubcap has no bundle for `A` → report "not on Hubcap"
   **without spending quota**.
5. `GET /lua/{A}` — 1 quota. Parse `addappid(...)`; if `D` is present, take its key.
   The full LUA covers the base game *and* the DLCs of `A` in one request.
6. If `A` is a DLC app (404 on `/lua/{A}`), the parent is unknown → use the persistent
   index, else `app_hints`, else log an actionable message naming the app id to add.

---

## 6. Writing the key back into `config.yaml`

The plugin must persist a fetched key so the next session has it without a fetch.

* SLSsteam's Lua environment has `os`, `dofile` (used by the 1.5 plugin) — so `io` is
  available and the plugin can edit the config itself, with no ASSella in the loop.
* **The write must be in place**: open `r+`, seek 0, write, truncate, flush. The inode
  has to survive, because SLSsteam's inotify watcher only fires on an in-place write.
  (Same rule `.agents/AGENTS.md` states for ASSella's `BatchConfigEditor`.)
* The target is the `DecryptionKeys:` map, one line per depot:

  ```yaml
  DecryptionKeys:
    108600: fc06c9893d7792ec35292dfc1b216f92d0f9e173df982804f4ab00293e9ab25b # Project Zomboid [AppKey]
    2954012: 80da182082267ac93e826389cb7eaa2a9e65d68d958bc3488cefa1d2f34f176f # Burn With Me
  ```

  The YAML style is `  <depotId>: <key>  # <comment>`; the comment is optional.
* Safeguards: only add a key that is absent, only accept exactly 64 hex characters,
  keep one `.bak`, re-read the file afterwards to verify, and never write when the API
  key is missing or `write_config = false`.
* Verification that the reload happened: poll `~/.SLSsteam.log`; if the watcher is dead
  (`is_sls_filewatcher_dead()` looks for `Failed to read from FileWatcher`) the write
  lands on disk but is ignored → tell the user to restart Steam.

---

## 7. Bugs found

### 7a. The plugin that actually runs today — `download.lua` v1.0.1

`~/.config/SLSsteam/plugins/download.lua` (9166 B) is the active interceptor on this
machine; the 1.5 only exists as `.bak` files and as the published copy in ASSella's
plugin dir.

1. **Never reads `mrc_config.lua`** → ASSella's entire MRC settings tab is a no-op
   here. The tab only says "✓ Synced to mrc_config.lua (Restart Steam to apply)".
2. **Uncached blocking fetch per manifest request**, with up to 5 sequential retries.
3. **`curl.downloadString(url, 5)` sets only `--connect-timeout`, never `--max-time`** →
   a server that accepts the connection and then stalls holds the global `LuaMutex`
   forever. This is the 33-minute stall the 1.5 header documents; it is still live here.
4. **`tonumber(codeStr) == nil` accepts `"0"`** as a valid manifest request code.
5. **No `pcall` around the hook body** → an error inside the hook escapes into Steam's
   call stack.
6. **`tonumber(sub, 16)` accepts `"-1"`, `"+1"`, `" 1"`** → a malformed key silently
   becomes wrong bytes.
7. **`hkGetBinary` writes 64 bytes into the output buffer without checking `outSize`**
   → buffer overflow.

### 7b. In the 1.5 (the base for 1.6)

8. **The global is named `Downloader`**, with the same `setup` include guard as the
   Standard plugin. If both files are ever loaded, the second one returns at the guard
   and the hooks are never installed — a silent no-op. → rename to `SpaceBunny`.
9. **`luaReload` does not restore the depot list.** The comment says "Write the original
   depot list back" but it calls `writeDepotIds()`, which writes the **merged** list
   (originals + `AdditionalDepots`). Nothing is restored. → needs a real
   `restoreDepotIds()` that puts the backed-up original ids back into the vector.
   Note: the backup (`PackageDepotIds`) must **survive** a reload. Clearing it would
   make the next `GetPackage(0)` back up the already-injected vector as "the
   original", and the injected depots would become permanent. The restore has to
   verify that the live vector still is the one the plugin wrote before it replaces
   it, so a package Steam rebuilt in the meantime is left alone.
10. **The MRC authority uses plain HTTP** (`http://gmrc.wudrm.com/manifest/`).
    `https://gmrc.wudrm.com/manifest/<id>` returns 200 in 0.74 s → the switch is free.
11. **The header still claims** "Test build. Not hashed into plugins_manifest.json yet",
    although it *is* hashed and published.

### 7c. In ASSella (recorded, not being changed now)

12. `MRC_PROVIDER_HEADERS["manifestdex"] = ['"User-Agent: ManifestDeX/1.0"']` — the
    embedded quotes put the quote characters *inside* the string. Verified live:
    the exact UA `ManifestDeX/1.0` returns **200**, the quoted variant returns **403**.
    The 1.5's own header is correct; ASSella writes the string wrong, so the MRC
    rescue path would be dead if the settings tab were used with the 1.5.
    The settings tab also still writes `FULL_MS`/`PROBE_MS` (milliseconds) while the
    1.5 migrated to `FULL_SEC`/`PROBE_SEC`.

---

## 8. Open points for 1.6beta

* The fetch happens inside a hook while the global Lua mutex is held → the timeout
  must stay small (3–5 s) and it must happen at most once per app.
* Hubcap genuinely has no key for some depots (e.g. `2954012` for `Burn With Me` — it
  is in neither `existing_depot_ids` nor `pending_depot_ids`). The plugin must say
  "no key published yet" and never invent one.
* The depot→app mapping for DLCs has no authoritative source → the persistent index
  plus a user-editable `app_hints` map in `spacebunny.conf.lua`.
* A self-test mode (`SPACE_BUNNY_SELFTEST=1`) should exercise the LUA parser and the
  config writer against fixtures at load time, so both can be verified without
  downloading anything.

---

## 9. Local layout

| Path | Purpose |
|---|---|
| `~/.local/share/ACCELA/debug/spacebunny1.6beta.lua` | plugin source (local only, not in ASSella's plugin dir) |
| `~/.local/share/ACCELA/debug/test_spacebunny_1_6beta.lua` | 60 checks of the parser, the resolver and the writer |
| `~/.local/share/ACCELA/debug/live_hubcap_test.lua` | the resolver against the real Hubcap API |
| `~/.local/share/ACCELA/debug/README.md` | how to install and test it |
| `~/.config/SLSsteam/plugins/spacebunny1.6beta.lua` | live copy that SLSsteam loads (not installed yet) |
| `~/.config/SLSsteam/plugins/download.lua` → `download.lua.bak` | moved aside during the test so only one interceptor is active |
| `~/.config/SLSsteam/spacebunny.conf.lua` | user-editable Hubcap access (api key, base url, toggles), `chmod 600` |

`lua` 5.1 / 5.4 / luajit / `luac` are installed on this machine, so the plugin can be
syntax-checked with `luac -p` and its pure-Lua parts can be unit-tested outside Steam.
