# ClipABit - DaVinci Resolve Plugin

A semantic video search plugin for DaVinci Resolve that allows you to search through your media pool using natural language queries and automatically add matching clips to your timeline.

## Features

- **Semantic Video Search**: Search your media pool using natural language (e.g., "woman walking", "car driving")
- **Video Preview & Trim**: Preview search results with a built-in video player and trim in/out points before inserting
- **Smart Upload Management**: Track which files have been processed and upload only new files
- **Non-blocking Networking**: All HTTP I/O (upload, search, job polling) runs on Qt's event loop via `QNetworkAccessManager` — no threads, no UI freezes, fully compatible with Resolve's fuscript.exe
- **Background Job Tracking**: Monitor upload and processing jobs with real-time status updates
- **Timeline Integration**: Add search results directly to your timeline with precise timing

## Prerequisites

* **Python 3.11.12** (3.12+ not supported by Resolve)
* **uv** (recommended) or pip
* **DaVinci Resolve Studio** (paid version required for scripting support)

## Quick Start

### 1. Install Dependencies

```bash
# From repository root
uv sync
```

### 2. Configure Environment Variables

Set these before running the plugin:

```bash
# Required for authentication
export CLIPABIT_AUTH0_DOMAIN="your-tenant.auth0.com"
export CLIPABIT_AUTH0_CLIENT_ID="your_client_id"
export CLIPABIT_AUTH0_AUDIENCE="https://api.clipabit.com"

# Optional runtime mode flags
export CLIPABIT_ENVIRONMENT="dev"      # dev (default), staging, prod
```

On Windows PowerShell:

```powershell
$env:CLIPABIT_AUTH0_DOMAIN = "your-tenant.auth0.com"
$env:CLIPABIT_AUTH0_CLIENT_ID = "your_client_id"
$env:CLIPABIT_AUTH0_AUDIENCE = "https://api.clipabit.com"
$env:CLIPABIT_ENVIRONMENT = "dev"
```

You can also use a `.env` file. The plugin auto-loads `.env` values at startup (without overwriting already-exported variables). It looks in:
- current working directory
- parent directories of the installed module path
- Resolve utility script folder (`Fusion/Scripts/Utility/.env`) when running inside Resolve
- `~/.clipabit/.env`

You can force a specific file with `CLIPABIT_ENV_FILE=/absolute/path/to/.env`.

### 3. Preview the Plugin (Standalone)

You can preview the UI without DaVinci Resolve:

```bash
# From repository root
uv run python clipabit.py
```

When running outside Resolve, you'll see `Resolve API not available`. That is expected.

### 4. Move into DaVinci Resolve

Move both:
- the shim (`clipabit.py`) into `Fusion/Scripts/Utility/` as `ClipABit.py`
- the package (`clipabit/`) into `Fusion/Modules/clipabit`

**macOS:**
```bash
FUSION_DIR="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion"
mkdir -p "$FUSION_DIR/Scripts/Utility" "$FUSION_DIR/Modules"
cp "clipabit.py" "$FUSION_DIR/Scripts/Utility/ClipABit.py"
rm -rf "$FUSION_DIR/Modules/clipabit"
cp -R "clipabit" "$FUSION_DIR/Modules/clipabit"
```

**Windows (PowerShell):**
```powershell
$fusionDir = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion"
New-Item -ItemType Directory -Path "$fusionDir\Scripts\Utility" -Force | Out-Null
New-Item -ItemType Directory -Path "$fusionDir\Modules" -Force | Out-Null
Copy-Item ".\clipabit.py" "$fusionDir\Scripts\Utility\ClipABit.py" -Force
Remove-Item "$fusionDir\Modules\clipabit" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item ".\clipabit" "$fusionDir\Modules\clipabit" -Recurse -Force
```

**Linux:**
```bash
FUSION_DIR="$HOME/.local/share/DaVinci Resolve/Fusion"
mkdir -p "$FUSION_DIR/Scripts/Utility" "$FUSION_DIR/Modules"
cp "clipabit.py" "$FUSION_DIR/Scripts/Utility/ClipABit.py"
rm -rf "$FUSION_DIR/Modules/clipabit"
cp -R "clipabit" "$FUSION_DIR/Modules/clipabit"
```

### 5. Run in DaVinci Resolve

1. Open DaVinci Resolve
2. Open or create a project
3. Navigate to: **Workspace → Scripts → Utility → ClipABit**
4. The plugin window will open



## Development Workflow

### Making Changes

```bash
# 1. Make changes in clipabit.py and/or clipabit/

# 2. Run to preview
uv run python clipabit.py

# 3. Close the window (Cmd+Q) and re-run to see new changes

# 4. Sync to Resolve (see install commands above)
```

### Using the File Watcher (Optional)

For automatic sync during development:

```bash
# From the repository root
uv run python watch_clipabit.py --source .
```

This watches the plugin source and syncs both:
- `clipabit.py` to `Fusion/Scripts/Utility/ClipABit.py`
- `clipabit/` to `Fusion/Modules/clipabit`

## Usage

### Searching Videos

1. Enter a natural language query in the search box (e.g., "person walking", "sunset scene")
2. Press Enter to search
3. Browse the results in the grid view (thumbnails load asynchronously)
4. Click **Preview & Trim** on any result to open the video preview dialog
5. Adjust the trim range with the dual-handle slider, then click **Insert to Timeline**

### Managing Uploads

1. Click the **Media Pool** button in the header
2. Click **Select Files to Upload** to choose clips from your media pool
3. Monitor progress via the **i** (info) button in the header

## Configuration

### Environment Settings

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `CLIPABIT_AUTH0_DOMAIN` | Yes (for sign-in) | none | Auth0 tenant domain |
| `CLIPABIT_AUTH0_CLIENT_ID` | Yes (for sign-in) | none | Auth0 application client ID |
| `CLIPABIT_AUTH0_AUDIENCE` | Yes (for sign-in) | none | API audience used in token requests |
| `CLIPABIT_ENVIRONMENT` | No | `dev` | `dev`, `staging`, `prod` |

### File Locations

| Platform | Utility Script Path | Modules Path |
|----------|----------------------|--------------|
| **macOS** | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/ClipABit.py` | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Modules/clipabit/` |
| **Windows** | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\ClipABit.py` | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Modules\clipabit\` |
| **Linux** | `~/.local/share/DaVinci Resolve/Fusion/Scripts/Utility/ClipABit.py` | `~/.local/share/DaVinci Resolve/Fusion/Modules/clipabit/` |

## Release Flow (Automated)

### Staging prereleases
- Pushes to `staging` run semantic-release automatically.
- Tags are created as `vX.Y.Z-staging.N`.
- `CHANGELOG.md` and `pyproject.toml` are updated.

### Promote to main (manual trigger)
- Use GitHub Actions workflow **Promote staging to main**.
- It asks for **release type** (`patch`, `minor`, `major`).
- It merges `staging` → `main`, then runs semantic-release on `main` with the chosen release type.

### Permissions probe
- Run **Permissions Probe** workflow to verify whether `GITHUB_TOKEN` has write access.
- If it fails, org-level settings likely block write permissions.

## Troubleshooting

### Scripts Menu Shows "No Scripts"

- **Likely cause**: You're using the free version of DaVinci Resolve
- **Solution**: Scripting requires DaVinci Resolve Studio ($295)

### "Resolve API not available"

- This is normal when running standalone (outside of Resolve)
- The UI will still work, but media pool features are disabled

### Plugin Doesn't Appear After Copying

1. Restart DaVinci Resolve completely
2. Confirm both targets exist: `Fusion/Scripts/Utility/ClipABit.py` and `Fusion/Modules/clipabit/`
3. Check **Preferences → System → External scripting using** is set to **Local**

### "Auth0 environment variables are not set"

- Set `CLIPABIT_AUTH0_DOMAIN`, `CLIPABIT_AUTH0_CLIENT_ID`, and `CLIPABIT_AUTH0_AUDIENCE`
- Or create a `.env` file in one of the auto-loaded locations listed above

## File Structure

```
.
├── clipabit.py          # Resolve shim / standalone entry point
├── clipabit/            # Main plugin package (sync to Fusion/Modules/clipabit)
│   ├── api/             # Auth (PKCE login, token storage) and backend config
│   ├── core/            # NetworkClient (QNAM), FileUploader, JobTracker, file utilities
│   ├── ui/              # PyQt6 application UI (main window, video preview, theme)
│   └── assets/          # UI assets (logos, icons)
├── watch_clipabit.py    # Development sync watcher (shim + package)
├── scripts/             # Release and auth utility scripts
├── pyproject.toml       # Project dependencies
├── uv.lock              # Dependency lock file
└── README.md            # This file
```

## Architecture

- **PyQt6 Interface**: Dark-themed UI with non-blocking networking via `QNetworkAccessManager`
- **No Threads**: All HTTP calls (upload, search, job polling, delete) run on Qt's event loop — safe inside Resolve's fuscript.exe which crashes on `QThread.start()`
- **Modal.com Backend**: Serverless video processing
- **Pinecone Vector Database**: Semantic search with CLIP embeddings
- **Cloudflare R2**: Video file storage
