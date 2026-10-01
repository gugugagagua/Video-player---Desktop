[**简体中文**](./README.md)|[**English**](./README-en.md)
# Video Set Player (Windows Desktop)
#This software was developed with AI assistance

**Current version: 1.3.2**

> The Android version lives in a separate repository [https://github.com/gugugagagua/Video-player]

## Tech Stack

- Python 3.10+ / PyQt6 (Qt 6)
- PyQt6.QtMultimedia (FFmpeg backend, video playback)
- OpenCV-Python (first-frame extraction, duration probing, hover frame grabbing)
- Pillow (cover image format conversion)
- SQLite3 (bundled with Python, data persistence)
- PyQt6.QtSvg (Material icons drawn at runtime, theme-tintable)
- PyInstaller + Inno Setup (packaging and installer)

## Feature Comparison

| Android Version Feature | Desktop Version Implementation |
|-------------------------|--------------------------------|
| Import a folder to create a video set | File menu / home "Import Video Set" picks a folder → `os.scandir` scans for videos |
| Video set cover (cover.png / first frame) | Right-click "Set cover (from local image)" or "Extract first frame as cover"; always saved as `cover.png` in the video set root |
| Group management (create / rename / delete / move) | Groups appear as **folder cards**; tap to **navigate in**, right-click to rename / set cover / delete |
| Group cover | Choose from the cover of any video set inside the group |
| Playlist, previous episode / next episode | Right side panel on the player page + bottom control bar |
| Playback progress memory (resume) | `collections.last_video_id` + `videos.last_position`, saved on exit / back home / episode switch |
| Watch progress visualization | "Watched N/M" badge + progress bar on video set covers; per-episode watched / unwatched state and progress bar in the playlist |
| Search | Top bar search across video set names and video file names; video results jump straight to playback |
| Playback control | Play/pause, seek, previous/next episode, speed, fullscreen |
| Fast-forward | Short press `→` seeks forward 10s; long-press `D` / `→` plays at 2× speed, release to restore |
| Swipe to seek | Drag the progress bar to seek; **hover the progress bar** to preview the target frame and time |
| Fullscreen | Toggle with `F` or double-click; exit with `Esc` |
| Light / dark theme | Material 3, **follows the system** color mode automatically; switch manually via the Theme menu |
| Volume | Bottom volume slider + up/down arrow keys |

## Download and Install

Get the latest version from the [Releases](../../releases) page:

| Download | Description |
|---|---|
| **`Video-Set-Player.exe`** | Installer (recommended); installs to `C:\Program Files\视频集播放器` by default |
| **`Video-Set-Player.zip`** | Portable, no installation needed — just extract and run |

**Installer**: double-click it and follow the wizard, then launch from the Start menu or the desktop shortcut.

**Portable**: extract the archive and run `Video-Set-Player\Video Set Player.exe`. (The `_internal\` folder must stay next to the exe — do not move the exe out on its own.)

**System requirements: Windows 10 64-bit or newer.**

## Build

### Run from source

```powershell
pip install -r requirements.txt
python main.py
```

### Package as a portable program

```
build.bat
```

The output is located in `dist\视频集播放器\` (directory mode, not a single file).

### Build the installer

Install [Inno Setup 6](https://jrsoftware.org/isdl.php) first, then:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

The output is located in `installer_output\`. To change the app name or version, edit the `#define` values at the top of `installer.iss`.

## Usage

### Media Library Directory (recommended)

Each **video set = one folder**. Place your videos like this:

```
Any location/
├── Series A/         ← automatically becomes a video set
│   ├── cover.png     ← optional, used as the cover
│   ├── --01.mp4
│   └── --02.mp4
├── Documentary B/    ← another video set
│   └── ...
```

- Supported video formats: `mp4 / avi / mkv / mov / wmv / flv / webm / m4v / mpg / mpeg / 3gp`
- Files are sorted **naturally**: `--1` → `--2` → `--10` (not the lexicographic `--1` → `--10` → `--2`)
- Scanning is **single-level**: the videos inside the folder are the video set; deeper levels are not recursed

### Importing Video Sets

Two ways:

- The **"+ Import Video Set"** button at the bottom of the home page
- The **File → Import Video Set** menu

Pick a folder and it is scanned into the library automatically. If the folder was already imported, it is re-sorted from the current files (handy for syncing added / removed files).

### Groups and Video Sets

- Root level of the home page: groups appear as **folder cards**, mixed with ungrouped video sets
- Tap a folder to **enter the group**; a "← Back to all / group name" breadcrumb is shown at the top
- **Right-click a group**: rename / set group cover / delete (the video sets inside are kept)
- **Right-click a video set**: rename / set cover / extract first frame / new group (move into it) / move to group / remove from group / delete
- **Drag cards** to reorder freely; the order is persisted in the database

### Covers

Each video set has at most one `cover.png`, stored in the video set folder root:

| Method | Description |
|---|---|
| Set cover (from local image) | Pick a png / jpg / jpeg / bmp / gif; it is converted to `cover.png` in the video set folder |
| Extract first frame as cover | Takes the first frame of the first episode and saves it as `cover.png` at **full resolution** |

> Covers are not generated automatically — you must choose one of the two methods manually. Portrait posters and landscape screenshots are both scaled proportionally and centered, without distortion.

### Search

Type a keyword in the top bar search box:

- **Video sets**: filtered in real time by name, shown as cards
- **Videos**: fuzzy match on file names (e.g. type "EP05"); tapping a result **jumps straight to playing that episode**

### Watch Progress

- Video set cards show a status badge at the bottom left: `Watched N/M` / `✓ Completed` / `Unwatched 0/N`, with an overall progress bar along the cover
- Each item in the playlist shows a state icon and a per-episode progress bar: now playing / completed / watched to mm:ss / unwatched
- Missing durations are filled in with OpenCV when a video set is opened, so percentages are accurate
- **Opening a video set automatically jumps to the last watched episode and resumes**; progress is saved on "exit / back home / switch to another episode" (fragments shorter than 3 seconds are ignored)

### Player Controls

| Action | Description |
|---|---|
| Space | Play / pause |
| ← | Seek backward 10 seconds |
| → | Short press: seek forward 10 seconds |
| Long-press D / → | Play at 2× speed; release to restore (a `»2x` hint appears at the top) |
| ↑ / ↓ | Volume +5 / -5 |
| F | Toggle fullscreen |
| Esc | Exit fullscreen (in fullscreen, Esc does not go back home) |
| Double-click the video | Toggle fullscreen |
| Drag the progress bar | Seek; **hover the bar to preview the target frame and time** |
| Speed button | 0.5x / 0.75x / 1.0x / 1.25x / 1.5x / 2.0x |

> **Progress preview**: hovering over the progress bar pops up a small preview — the **target frame** on top and the target time below. Frames are grabbed with a 120ms throttle and video handles are LRU-cached (up to 4), so dragging stays smooth.
>
> While typing in the search box, playback shortcuts are temporarily disabled to avoid conflicts.

The next episode plays automatically when one finishes; unsupported or broken videos show a clear error message.

### Theme and Audio

- **Theme**: the system color mode is detected on launch and the Material 3 dark / light scheme is applied; switch manually via "Theme → Dark mode / Light mode"
- **Audio**: the "Audio" menu lists all system audio output devices and lets you switch at any time (speakers / headphones / Bluetooth)

## Data Storage

- Database: `data/videos.db` (SQLite, with the `groups` / `collections` / `videos` tables)
- Covers: `cover.png` inside each video set folder
- Video files are **not duplicated**; the database stores the original file paths

## Known Limitations

- Directory scanning is **single-level**: a video set is the selected folder itself; subdirectories are not recursed
- Each video set's cover is the single fixed `cover.png`
- Playback relies on the QtMultimedia FFmpeg backend; a few codecs may not decode
- The installer is fairly large (PyQt6 + OpenCV, about 67 MB)
