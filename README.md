<p align="center">
  <img src="assets/krypton.png" width="112" height="112" alt="Krypton Browser for Windows logo">
</p>

<h1 align="center">Download Krypton Browser for Windows</h1>

<p align="center">
  <strong>The free, private browser for Windows 10 and 11, with vertical tabs, split view, workspaces and uBlock Origin built in.</strong>
</p>

<p align="center">
  <a href="https://github.com/perpetus/krypton-windows/releases/latest"><strong>Download the latest installer</strong></a>
  ·
  <a href="https://krypton.run">Website</a>
  ·
  <a href="https://krypton.run/whats-new/">What's new</a>
  ·
  <a href="https://github.com/perpetus/krypton-windows/issues/new/choose">Report a problem</a>
</p>

<p align="center">
  <a href="https://github.com/perpetus/krypton-windows/releases/latest"><img src="https://img.shields.io/github/v/release/perpetus/krypton-windows?label=release&color=3b5bdb" alt="Latest Krypton release"></a>
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6" alt="Windows 10 and 11">
  <img src="https://img.shields.io/badge/x64-AVX2-3b5bdb" alt="64-bit x64 with AVX2">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="GPL-3.0 license"></a>
</p>

<p align="center">
  <img src="assets/krypton-browser-windows.webp" width="900" alt="Krypton browser on Windows with vertical tabs in the sidebar, pinned Essentials and a page open beside them">
</p>

This is the official home of Krypton's Windows installers, signed updates and
release notes. Krypton keeps every tab in a sidebar, opens pages side by side
and blocks ads before they load.

## Install Krypton

1. Open the [latest release](https://github.com/perpetus/krypton-windows/releases/latest).
2. Download one installer:
   - **`KryptonSetup-<version>-web-x64.exe`**: a small download that fetches
     the browser during setup.
   - **`KryptonSetup-<version>-offline-x64.exe`**: the complete package, for
     installing without a connection.
3. Close any open Krypton windows, run setup and follow the steps.

The current version is **Krypton 0.22.1.2** on Chromium 158.0.8127.36. Both
installers include the native interface and the Microsoft runtimes Krypton
needs. The `.7z`, `.delta` and `.json` files are for the automatic updater, so
you don't need to download them.

## System requirements

| | Requirement |
| --- | --- |
| Windows | Windows 10 version 1903 or later, or Windows 11 |
| Architecture | 64-bit Intel or AMD (x64) |
| Processor | AVX2 support (x86-64-v3) |

Setup checks your PC and explains any missing requirement. ARM64 and 32-bit
Windows aren't supported yet.

## What you get

- **Vertical tabs and workspaces**, with folders, Live Folders and pinned
  Essentials.
- **Split view** for up to four pages, and **Glance** to preview a link.
- **uBlock Origin built in**, with no usage statistics sent.
- **On-device translation and voice typing.**
- **A command bar** for tabs, Spaces, history, bookmarks and actions.
- **Screenshots, Notes and Easel** for capturing and organizing what you find.
- **Chromium extensions** and themes to make it yours.

Read the [0.22.1.2 release notes](https://github.com/perpetus/krypton-windows/releases/tag/v0.22.1.2)
or the full history on [krypton.run](https://krypton.run/whats-new/).

## Updates

Krypton checks for updates when it starts. Updates are signed, verified, and
installed after you close the browser, often as a small delta download.

## Verify a download

Each installer has a `.sha256` checksum and a `.sig` signature. To check the
checksum in PowerShell:

```powershell
Get-FileHash .\KryptonSetup-0.22.1.2-offline-x64.exe -Algorithm SHA256
```

Compare the result with the `.sha256` file. Signatures use Krypton's release
key in [`security/release-public-key.json`](security/release-public-key.json).
Windows may still show a SmartScreen prompt for a new publisher, so install
only files you downloaded from this repository's Releases page.

## Uninstall

Open **Settings → Apps → Installed apps → Krypton → Uninstall**. Your browser
profile data is kept.

## Source and credits

Krypton is free software under the [GPL-3.0](LICENSE). Its browser and
interface are developed in [Krypton](https://github.com/perpetus/Krypton) and
[KryptonUI](https://github.com/perpetus/KryptonUI), and every release includes a
signed source archive. Krypton is an independent project in active development.
[Credits and attribution](CREDITS.md)
