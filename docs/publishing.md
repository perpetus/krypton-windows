# Publishing Windows releases

Public repository: `perpetus/krypton-windows`. This repository holds release documentation and the public verification key. Large binary assets belong to GitHub Releases, not Git history.

## Prepare locally

The build and signing tools live in the sibling `KryptonUpdater` workspace. Build the offline installer there using the browser binaries you intend to release. The private signing key remains outside the repository, protected by Windows DPAPI.

Then stage a release in this repository:

```powershell
python tools/stage-release.py
```

This copies the already signed installer/update assets, verifies their signatures, checks the update archive's size/hash, bundles the current Krypton source layers and native components, signs that source snapshot, and produces signed `SHA256SUMS`. Outputs go to the ignored `release-assets/v<browser-version>/` directory. No release is created by this staging command. Browser and Chromium versions come from the prepared package and its signed native package marker.

The source snapshot includes local modifications, upstream source layers and revision records, and instructions for obtaining Chromium's source/dependencies. It excludes build outputs, dependencies, reference checkouts, repository metadata, and private signing material. GitHub's automatic archive of this release-documentation repository is not the browser source snapshot.

## First testing release

Publish tag `v0.20.1.1` using `releases/v0.20.1.1.md` as its release notes, attaching every file listed in the generated `release-assets.json`. Mark it as a **prerelease**. The browser's stable updater uses GitHub's latest stable-release endpoint and will skip testing prereleases.

The current release is for manual installer testing. Builds, archive integrity, packaged UI hashes, file properties, and signatures have been checked. Automated tests for the new update logic, live upgrades, clean-machine installation, same-version UI replacement, and native visual acceptance remain pending.

## Later stable releases

For UI maintenance without a Chromium rebuild, use `tools/prepare-ui-release.py`
with a new four-part release marker. It copies the existing browser runtime,
updates equal-length release strings and version resources, and verifies that
executable code sections and Chromium's engine version are unchanged. The
original browser output is preserved. The new UI DLL retains its independent
KryptonUI version.

```powershell
python tools/prepare-ui-release.py --browser-dir D:/code/Krypton/build/src/out/Default --ui-dir D:/code/KryptonUI/out/build/windows/bin/Release --output-dir .release-staging/ui-0.20.3.3 --version 0.20.3.3
pwsh ../KryptonUpdater/tools/build-offline.ps1 -BrowserDir .release-staging/ui-0.20.3.3/src/out/Default -OutputDir D:/code/KryptonUpdater/out/dist-ui-0.20.3.3
python tools/stage-release.py --dist-dir D:/code/KryptonUpdater/out/dist-ui-0.20.3.3 --browser-stage-record .release-staging/ui-0.20.3.3/ui-release-inputs.json --release-type stable
```

Keep the signed metadata, native package marker, GitHub tag, and installer
version aligned to the new release marker. Publish it as a regular release;
the independent UI DLL version does not replace the updater's increasing
release marker.

Use `python tools/stage-release.py --release-type stable` to stage a release
for the automatic updater. The default remains `prerelease` for manual setup
testing. GitHub's latest-release endpoint excludes prereleases; a build meant
to exercise the installed updater must be published as stable and latest.

Use a new four-part browser version for changed browser binaries, and a new GitHub tag. Keep old full packages and their manifests to generate useful deltas. Verify the intended native UI DLL against the local Release output before packaging. Fully close Krypton before testing repair/replacement of an installed version.

When a release is ready for automatic updates, publish it as a stable release and mark it latest. Signed metadata expires after 90 days; refresh it with a higher sequence before expiry when necessary. Retain the pinned public key. Rotating it requires a compatible trust transition for installed browsers.

Never upload `.dpapi`, `.pfx`, private keys, credentials, or the signing account's recovery material. The public key JSON and detached `.sig` files are safe to publish. Release-key signatures do not replace a Microsoft-trusted publisher certificate.
