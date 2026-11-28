param([Parameter(Mandatory)][string]$Binary)
$ErrorActionPreference = 'Stop'

if (!$env:DESKTOP_AZURE_DLIB -or !$env:DESKTOP_AZURE_METADATA) {
    throw 'Artifact Signing client and metadata must be configured.'
}
$sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
$signtool = Get-ChildItem "$sdk\*\x64\signtool.exe" | Sort-Object FullName -Descending | Select-Object -First 1
if (!$signtool) { throw 'Windows SDK SignTool is required.' }

& $signtool.FullName sign /fd SHA256 /tr 'http://timestamp.acs.microsoft.com' /td SHA256 `
    /dlib $env:DESKTOP_AZURE_DLIB /dmdf $env:DESKTOP_AZURE_METADATA $Binary
if ($LASTEXITCODE) { throw 'Artifact Signing failed.' }
& $signtool.FullName verify /pa /tw $Binary
if ($LASTEXITCODE) { throw 'Authenticode verification failed.' }
