[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$BrowserDir,
  [Parameter(Mandatory)][string]$OutputDir,
  [string]$UpdaterRoot = (Join-Path $PSScriptRoot '../../KryptonUpdater'),
  [string]$KryptonUiDir = (Join-Path $PSScriptRoot '../../KryptonUI'),
  [string[]]$BasePackageJson = @(),
  [string]$KeyPath
)

# Same signed offline pipeline as KryptonUpdater/tools/build-offline.ps1,
# with explicit build directories so no preset injects a parallel-job argument.
# This command performs packaging/signature checks, never automated test suites.
$ErrorActionPreference = 'Stop'
$updater = (Resolve-Path -LiteralPath $UpdaterRoot).Path
$browser = (Resolve-Path -LiteralPath $BrowserDir).Path
$version = (Get-Item -LiteralPath (Join-Path $browser 'chrome.exe')).VersionInfo.FileVersion
if ($version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw 'Invalid browser file version.' }
$installerDir = 'C:/Program Files (x86)/Microsoft Visual Studio/Installer'
$vs = & (Join-Path $installerDir 'vswhere.exe') -latest -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ tools were not found.' }
$env:PATH = "$installerDir;$env:PATH"
Import-Module (Join-Path $vs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
Remove-Item Env:CMAKE_BUILD_PARALLEL_LEVEL -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$output = (Resolve-Path -LiteralPath $OutputDir).Path
$work = Join-Path $updater ('out/packages/offline-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
  & $Program @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

Push-Location -LiteralPath $updater
try {
  Invoke-Checked 'pwsh' @('-NoProfile', 'tools/dev.ps1', 'configure',
    '-DKR_OFFLINE_PAYLOAD=', '-DKR_OFFLINE_MANIFEST=', '-DKR_OFFLINE_SIGNATURE=',
    '-DKR_ALLOW_UNSIGNED=OFF', "-DKR_PRODUCT_VERSION=$version")
  Invoke-Checked 'cmake' @('--build', 'out/build/windows', '--config', 'Release',
    '--target', 'KryptonSetup', 'KryptonWebSetup', 'KryptonUpdater', 'KryptonUninstaller')
  $web = @('-NoProfile', 'tools/build-web.ps1', '-OutputDir', $output, '-SkipBuild')
  if ($KeyPath) { $web += @('-KeyPath', $KeyPath) }
  Invoke-Checked 'pwsh' $web
  $prerequisites = Join-Path $work 'prerequisites'
  Invoke-Checked 'pwsh' @('-NoProfile', 'tools/prepare-prerequisites.ps1',
    '-BrowserDir', $browser, '-OutputDir', $prerequisites, '-KryptonUiDir', $KryptonUiDir)
  $stage = @('tools/package-browser.py', 'stage', '--browser-dir', $browser,
    '--work-dir', $work, '--uninstaller', 'out/build/windows/bin/Release/KryptonUninstaller.exe',
    '--updater', 'out/build/windows/bin/Release/KryptonUpdater.exe',
    '--prerequisites-dir', $prerequisites)
  if ($KeyPath) { $stage += @('--key-path', $KeyPath) }
  Invoke-Checked 'python' $stage
  $packagePath = Join-Path $work 'package.json'
  $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
  if ($package.browser_version -ne $version) { throw 'Package version mismatch.' }
  Invoke-Checked 'pwsh' @('-NoProfile', 'tools/dev.ps1', 'configure', '-Preset', 'windows-offline',
    '-OfflinePayload', $package.archive, '-OfflineManifest', $package.manifest,
    '-OfflineSignature', $package.manifest_signature, '-DKR_ALLOW_UNSIGNED=OFF',
    "-DKR_PRODUCT_VERSION=$version")
  Invoke-Checked 'cmake' @('--build', 'out/build/windows-offline', '--config', 'Release',
    '--target', 'KryptonSetup')
  $setup = Join-Path $output "KryptonSetup-$version-offline-x64.exe"
  Copy-Item -LiteralPath 'out/build/windows-offline/bin/Release/KryptonSetup.exe' -Destination $setup
  foreach ($helper in @('KryptonUpdater.exe', 'KryptonUninstaller.exe')) {
    Copy-Item -LiteralPath "out/build/windows/bin/Release/$helper" -Destination (Join-Path $output $helper)
    Copy-Item -LiteralPath "out/build/windows/bin/Release/$helper.sig" -Destination (Join-Path $output "$helper.sig")
  }
  $sign = @('-NoProfile', 'tools/release-signing.ps1', 'sign', '-InputFile', $setup)
  if ($KeyPath) { $sign += @('-KeyPath', $KeyPath) }
  Invoke-Checked 'pwsh' $sign
  Invoke-Checked 'python' @('tools/package-browser.py', 'verify-embedded',
    '--installer', $setup, '--package-json', $packagePath)
  $release = @('tools/prepare-release.py', '--package-json', $packagePath,
    '--output-dir', (Join-Path $output 'github'))
  if ($KeyPath) { $release += @('--key-path', $KeyPath) }
  foreach ($base in $BasePackageJson) { $release += @('--base-package-json', $base) }
  Invoke-Checked 'python' $release
  $hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
  "$hash  $([IO.Path]::GetFileName($setup))" | Set-Content -LiteralPath "$setup.sha256" -Encoding ascii
  [pscustomobject]@{ Installer=$setup; WebInstaller=(Join-Path $output "KryptonSetup-$version-web-x64.exe"); Bytes=(Get-Item -LiteralPath $setup).Length;
    BrowserVersion=$version; Files=$package.file_count; VerificationDirectory=$work }
} finally {
  Pop-Location
}
