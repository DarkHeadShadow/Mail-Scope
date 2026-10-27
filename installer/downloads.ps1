# Project creator: Bálint Szabolcs
#
# Helper for the Mail-Scope installer: find the newest official 7-Zip and
# LibreOffice (Still branch) downloads, and check a downloaded MSI's digital
# signature. Inno Setup downloads the files itself and checks their SHA-256.
#
# Resolve:  downloads.ps1 -Resolve -OutFile <file> [-SevenZip] [-LibreOffice]
#   writes one line per program: NAME|url|sha256|version  or  NAME-ERROR|message
# Verify:   downloads.ps1 -VerifySignature <msi> -Publisher "The Document Foundation"
#   exits with 0 only for a valid Authenticode signature of that publisher.

param(
    [switch]$Resolve,
    [string]$OutFile,
    [switch]$SevenZip,
    [switch]$LibreOffice,
    [string]$VerifySignature,
    [string]$Publisher
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$headers = @{ 'User-Agent' = 'Mail-Scope-Setup' }

function Get-Text([string]$Uri) {
    $content = (Invoke-WebRequest -Uri $Uri -Headers $headers -UseBasicParsing).Content
    if ($content -is [byte[]]) { $content = [Text.Encoding]::ASCII.GetString($content) }
    return [string]$content
}

function Resolve-SevenZip {
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/ip7z/7zip/releases/latest' -Headers $headers -UseBasicParsing
    $asset = $release.assets | Where-Object { $_.name -match '^7z\d+-x64\.msi$' } | Select-Object -First 1
    if (-not $asset) { throw 'A 7-Zip legfrissebb kiadásában nincs 64 bites MSI.' }
    if ($asset.digest -notmatch '^sha256:([0-9a-f]{64})$') { throw 'A 7-Zip kiadásához nincs közzétett SHA-256.' }
    $sha = $Matches[1]
    if ($asset.browser_download_url -notlike 'https://github.com/ip7z/7zip/releases/download/*') { throw 'Váratlan 7-Zip letöltési cím.' }
    return "SEVENZIP|$($asset.browser_download_url)|$sha|$($release.tag_name)"
}

function Resolve-LibreOffice {
    # The stable folder lists the Fresh branch (newest) and the Still branch
    # (the one before it), sometimes with an older branch near its end of life.
    $base = 'https://download.documentfoundation.org/libreoffice/stable/'
    $versions = [regex]::Matches((Get-Text $base), 'href="(\d+\.\d+\.\d+)/"') |
        ForEach-Object { [version]$_.Groups[1].Value } | Sort-Object -Unique
    $branches = @($versions | ForEach-Object { '{0}.{1}' -f $_.Major, $_.Minor } | Select-Object -Unique |
        Sort-Object { [version]$_ } -Descending)
    if ($branches.Count -lt 2) { throw 'A LibreOffice Still ága nem azonosítható.' }
    $version = ($versions | Where-Object { ('{0}.{1}' -f $_.Major, $_.Minor) -eq $branches[1] } |
        Sort-Object -Descending | Select-Object -First 1).ToString()
    $url = "$base$version/win/x86_64/LibreOffice_${version}_Win_x86-64.msi"
    $sha = ((Get-Text "$url.sha256").Trim() -split '\s+')[0].ToLowerInvariant()
    if ($sha -notmatch '^[0-9a-f]{64}$') { throw 'A LibreOffice telepítőjéhez nincs SHA-256.' }
    return "LIBREOFFICE|$url|$sha|$version"
}

if ($VerifySignature) {
    $signature = Get-AuthenticodeSignature -LiteralPath $VerifySignature
    $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '' }
    if ($signature.Status -eq 'Valid' -and $subject -match ('(^|, )O=' + [regex]::Escape($Publisher) + '(,|$)')) { exit 0 }
    exit 1
}

if ($Resolve) {
    $lines = @()
    if ($SevenZip) {
        try { $lines += Resolve-SevenZip } catch { $lines += "SEVENZIP-ERROR|$($_.Exception.Message)" }
    }
    if ($LibreOffice) {
        try { $lines += Resolve-LibreOffice } catch { $lines += "LIBREOFFICE-ERROR|$($_.Exception.Message)" }
    }
    [IO.File]::WriteAllLines($OutFile, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
    exit 0
}

exit 2
