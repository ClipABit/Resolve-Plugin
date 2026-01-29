# ClipABit - DaVinci Resolve Plugin

A semantic video search plugin for DaVinci Resolve that allows you to search through your media pool using natural language queries and automatically add matching clips to your timeline.

## Features

- **Semantic Video Search**: Search your media pool using natural language (e.g., "woman walking", "car driving")
- **Smart Upload Management**: Track which files have been processed and upload only new files
- **Background Job Tracking**: Monitor upload and processing jobs with real-time status updates
- **Timeline Integration**: Add search results directly to your timeline with precise timing
- **Dark/Light Theme**: Modern UI with theme toggle support

## Prerequisites

* **Python 3.12+**
* **uv** (recommended) or pip
* **DaVinci Resolve Studio** (paid version required for scripting support)

## Quick Start

### 1. Install Dependencies

```bash
# Navigate to plugin directory
cd plugin

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -e .

# Or install globally via pip
pip install PyQt6 requests watchdog
```

### 2. Preview the Plugin (Standalone)

You can preview the UI without DaVinci Resolve:

```bash
# Navigate to plugin directory
cd plugin

# Activate virtual environment
source .venv/bin/activate

# Run the plugin
python clipabit.py

# Close the window (Cmd+Q on Mac) and re-run to see new changes
```

### 3. Install in DaVinci Resolve

Copy the plugin to DaVinci Resolve's Scripts folder:

**macOS:**
```bash
cp "plugin/clipabit.py" "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve 20/Fusion/Scripts/Edit/"
```

**Windows (PowerShell):**
```powershell
Copy-Item "plugin\clipabit.py" "$env:APPDATA\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\"
```

**Linux:**
```bash
cp "plugin/clipabit.py" "$HOME/.local/share/DaVinci Resolve/Fusion/Scripts/Edit/"
```

### 4. Run in DaVinci Resolve

1. Open DaVinci Resolve
2. Open or create a project
3. Navigate to: **Workspace → Scripts → clipabit**
4. The plugin window will open

> **Note**: Scripting is only available in **DaVinci Resolve Studio** (paid version). The free version does not support Python scripting.

## Development Workflow

### Making Changes

```bash
# 1. Make changes to clipabit.py in your editor

# 2. Run to preview
cd plugin
source .venv/bin/activate
python clipabit.py

# 3. Close the window (Cmd+Q) and re-run to see new changes

# 4. When ready, copy to Resolve
cp "clipabit.py" "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve 20/Fusion/Scripts/Edit/"
```

### Using the File Watcher (Optional)

For automatic copying during development:

```bash
# From the repository root
source plugin/.venv/bin/activate
python watch_clipabit.py --source plugin/clipabit.py
```

This watches for changes and automatically copies to Resolve's Scripts folder.

## Usage

### Searching Videos

1. Enter a natural language query in the search box (e.g., "person walking", "sunset scene")
2. Click **Search** or press Enter
3. Browse the results in the grid view
4. Click **Add to timeline** on any result to insert it

### Managing Uploads

1. Click the **⚙** (settings) button in the top-right
2. Select files to upload for processing
3. Monitor progress via **Active Jobs/Debug** button

### Theme Toggle

1. Click **⚙** (settings) button
2. Click **Toggle Dark/Light Mode**

## Configuration

### Environment Settings

Set the `CLIPABIT_ENVIRONMENT` environment variable:
- `dev` (default) - Development environment
- `prod` - Production environment
- `staging` - Staging environment

### File Locations

| Platform | Scripts Location |
|----------|-----------------|
| **macOS** | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve 20/Fusion/Scripts/Edit/` |
| **Windows** | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\` |
| **Linux** | `~/.local/share/DaVinci Resolve/Fusion/Scripts/Edit/` |

## Troubleshooting

### Scripts Menu Shows "No Scripts"

- **Likely cause**: You're using the free version of DaVinci Resolve
- **Solution**: Scripting requires DaVinci Resolve Studio ($295)

### "Resolve API not available"

- This is normal when running standalone (outside of Resolve)
- The UI will still work, but media pool features are disabled

### Plugin Doesn't Appear After Copying

1. Restart DaVinci Resolve completely
2. Make sure you copied to the correct folder for your Resolve version
3. Check **Preferences → System → External scripting using** is set to **Local**

## File Structure

```
plugin/
├── clipabit.py          # Main plugin file
├── pyproject.toml       # Dependencies
├── README.md            # This file
└── uv.lock              # Dependency lock file

watch_clipabit.py        # Development file watcher (in repo root)
```

## Architecture

- **PyQt6 Interface**: Modern, responsive UI with dark/light themes
- **Modal.com Backend**: Serverless video processing
- **Pinecone Vector Database**: Semantic search with CLIP embeddings
- **Cloudflare R2**: Video file storage
