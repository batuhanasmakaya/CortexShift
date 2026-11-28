param([Parameter(Mandatory)][string]$Binary, [Parameter(Mandatory)][string]$Subject)
$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $Binary
if ($signature.Status -ne 'Valid' -or !$signature.TimeStamperCertificate -or
    $signature.SignerCertificate.Subject -cne $Subject) {
    throw "Missing trusted, timestamped Authenticode signature from the expected publisher: $Binary"
}
