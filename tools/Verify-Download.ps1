[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Path,
  [string]$SignaturePath,
  [string]$TrustPath = (Join-Path $PSScriptRoot '../security/release-public-key.json')
)

$ErrorActionPreference = 'Stop'
if (-not $SignaturePath) { $SignaturePath = "$Path.sig" }
$trust = Get-Content -LiteralPath $TrustPath -Raw | ConvertFrom-Json
if ($trust.algorithm -cne 'ecdsa-p256-sha256-p1363' -or $trust.repository -cne 'perpetus/krypton-windows') {
  throw 'Unexpected release trust configuration.'
}
$point = [Convert]::FromHexString($trust.public_key)
if ($point.Length -ne 65 -or $point[0] -ne 4) { throw 'The release public key is invalid.' }
$fingerprint = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($point)).ToLowerInvariant()
if ($fingerprint -cne '2622d0bb9114a6cb72bb83388be6f3c8055cc75888b6a6f1b12b75acbd1a4ece' -or $trust.key_id -cne $fingerprint) {
  throw 'The release public key fingerprint does not match.'
}
$signature = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $SignaturePath).Path)
if ($signature.Length -ne 64) { throw 'Expected a 64-byte Krypton release signature.' }
$parameters = [Security.Cryptography.ECParameters]::new()
$parameters.Curve = [Security.Cryptography.ECCurve+NamedCurves]::nistP256
$publicPoint = [Security.Cryptography.ECPoint]::new()
$publicPoint.X = $point[1..32]
$publicPoint.Y = $point[33..64]
$parameters.Q = $publicPoint
$ecdsa = [Security.Cryptography.ECDsa]::Create()
$stream = $null
try {
  $ecdsa.ImportParameters($parameters)
  $stream = [IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
  if (-not $ecdsa.VerifyData($stream, $signature, [Security.Cryptography.HashAlgorithmName]::SHA256,
      [Security.Cryptography.DSASignatureFormat]::IeeeP1363FixedFieldConcatenation)) {
    throw 'Signature verification failed. Do not install or use this file.'
  }
  Write-Output "Verified Krypton release signature: $Path"
} finally {
  if ($stream) { $stream.Dispose() }
  $ecdsa.Dispose()
}
