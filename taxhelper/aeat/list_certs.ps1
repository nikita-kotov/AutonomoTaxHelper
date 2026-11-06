# Lists the personal certificates of the current Windows user that can authenticate a client to AEAT:
# a private key is present and "Client Authentication" is among the key usages. Prints one JSON array.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$clientAuth = '1.3.6.1.5.5.7.3.2'
$found = @()
foreach ($c in Get-ChildItem Cert:\CurrentUser\My) {
    if (-not $c.HasPrivateKey) { continue }
    $usages = @($c.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId })
    if ($usages -notcontains $clientAuth) { continue }
    $found += [pscustomobject]@{
        thumbprint = $c.Thumbprint
        subject    = $c.Subject
        notBefore  = $c.NotBefore.ToString('yyyy-MM-dd')
        notAfter   = $c.NotAfter.ToString('yyyy-MM-dd')
    }
}
ConvertTo-Json -InputObject @($found) -Compress
