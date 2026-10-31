# Verify a download

Only download Krypton from [this repository's Releases page](https://github.com/perpetus/krypton-windows/releases).

## Check the SHA-256 checksum

Download the setup and its `.sha256` file. In PowerShell:

```powershell
Get-FileHash .\KryptonSetup-0.20.1.1-offline-x64.exe -Algorithm SHA256
Get-Content .\KryptonSetup-0.20.1.1-offline-x64.exe.sha256
```

The two hashes must match. A checksum detects damaged or changed downloads; the signature below verifies that the file was signed with Krypton's release key.

## Verify the release signature

Download the file and its matching `.sig`. Use PowerShell 7 with a trusted copy of this repository:

```powershell
pwsh ./tools/Verify-Download.ps1 -Path ./KryptonSetup-0.20.1.1-offline-x64.exe
```

The script reads the pinned public key in [security/release-public-key.json](../security/release-public-key.json), verifies ECDSA P-256/SHA-256, and prints a success message only when verification succeeds. It also works with `SHA256SUMS` and the update metadata. Do not edit signed metadata or signatures.

Public key fingerprint, SHA-256 of the uncompressed SEC1 public point:

```text
2622d0bb9114a6cb72bb83388be6f3c8055cc75888b6a6f1b12b75acbd1a4ece
```

The initial repository/public key must come from a source you trust. A key published beside an installer alone cannot establish that first connection. This release-key signature does not provide Microsoft publisher identity or SmartScreen reputation.
