<p align="center">
  <img src="assets/krypton.png" width="112" height="112" alt="Krypton logo">
</p>

# Krypton for Windows

A Chromium-based browser with a native Windows interface, workspaces, and a sidebar that keeps your tabs within reach.

**[Download Krypton for Windows](https://github.com/perpetus/krypton-windows/releases)** · [Report a problem](https://github.com/perpetus/krypton-windows/issues/new/choose)

This repository is the home for Krypton's Windows downloads and release notes. The current release is available for testing, so expect rough edges and keep a backup of important browser data.

## Get started

1. Open [Releases](https://github.com/perpetus/krypton-windows/releases) and download **`KryptonSetup-0.20.3.2-offline-x64.exe`**.
2. Close any open Krypton windows, then run setup and follow the on-screen steps.
3. Open Krypton from the Start menu or desktop shortcut.

Choose the **setup `.exe`** for installation. The `.7z` file is used by the updater; `.sig` and checksum files help verify downloads.

Setup includes the browser, its native UI, and installers for required Microsoft runtimes. You do not need to copy DLLs yourself.

## What you'll find

- A native Windows sidebar, tabs, menus, and settings surfaces.
- Workspaces and folders to organize browsing.
- Chromium-powered web compatibility and address-bar suggestions.
- Chrome Web Store extension support.
- Signed update packages and a separate uninstaller.

## System requirements

| | Requirement |
| --- | --- |
| Windows | Windows 10 version 1903/build 18362 or newer, including Windows 11 |
| Architecture | 64-bit Intel or AMD; this download is x64 |
| Processor | AVX2 and the other instruction features checked by setup |

Setup checks compatibility and explains any missing requirement. This build does not include native ARM64 or 32-bit support.

## Updates and uninstalling

Regular releases are checked when Krypton starts. Downloaded updates are verified and installed after the browser closes and its files are unlocked. Testing prereleases are available for manual installation and are not offered by the stable automatic updater.

To uninstall, open **Windows Settings → Apps → Installed apps → Krypton → Uninstall**. The uninstaller keeps your browser profile data.

## Download verification

Download the setup's `.sha256` file alongside it. In PowerShell, run:

```powershell
Get-FileHash .\KryptonSetup-0.20.3.2-offline-x64.exe -Algorithm SHA256
```

Compare the result with the checksum file. For cryptographic signature verification, see [Verify a download](docs/verify-downloads.md).

Krypton's release signatures authenticate files using its pinned release key. They are separate from a Microsoft-trusted publisher certificate, so Windows may still display an unfamiliar-publisher or SmartScreen prompt. Only install files obtained from this repository's Releases page that you intended to download.

## Help improve Krypton

[Report a bug](https://github.com/perpetus/krypton-windows/issues/new/choose) with your Windows version, Krypton version, and the steps that caused it. Screenshots are welcome; remove private information before sharing them.

## Source and credits

Krypton builds on [Chromium](https://www.chromium.org/) and [Helium](https://github.com/imputnet/helium), with its own native Windows UI and installer. Browser development lives in [perpetus/Krypton](https://github.com/perpetus/Krypton). The release includes a source snapshot of Krypton's browser layers, native UI, and updater, with upstream revision information.

Krypton's browser source uses [GPL-3.0](LICENSE); third-party components retain their own licenses and notices. Open `krypton://credits` in the browser for Chromium's acknowledgments.
