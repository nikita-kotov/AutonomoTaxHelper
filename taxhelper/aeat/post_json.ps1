# One HTTPS call to AEAT authenticated with a certificate of the Windows store (SChannel).
# The private key of such a certificate cannot be exported, so Python's ssl module cannot use it;
# .NET asks Windows to sign the TLS handshake instead (a PIN or smart-card prompt appears when the key needs it).
# Prints one JSON object: {"status": <http code>, "location": ..., "body": <base64 of the raw body>}
# or {"error": <message>}. The request body is read from a file so it never appears on a command line.
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Thumbprint,
    [string]$BodyFile = '',
    [ValidateSet('GET', 'POST')][string]$Method = 'POST',
    [int]$TimeoutSec = 90
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -eq $Thumbprint } | Select-Object -First 1
    if (-not $cert) { throw 'The certificate is not in the Windows store of this user.' }

    $req = [Net.HttpWebRequest]::Create($Url)
    $req.Method = $Method
    $req.Timeout = $TimeoutSec * 1000
    $req.ReadWriteTimeout = $TimeoutSec * 1000
    $req.AllowAutoRedirect = $false
    $req.UserAgent = 'TaxHelper'
    [void]$req.ClientCertificates.Add($cert)
    if ($Method -eq 'POST') {
        $bytes = [IO.File]::ReadAllBytes($BodyFile)
        $req.ContentType = 'application/json;charset=UTF-8'
        $req.ContentLength = $bytes.Length
        $stream = $req.GetRequestStream()
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Close()
    }
    try {
        $resp = $req.GetResponse()
    } catch [Net.WebException] {
        if ($_.Exception.Response) { $resp = $_.Exception.Response } else { throw }
    }
    $memory = New-Object IO.MemoryStream
    $resp.GetResponseStream().CopyTo($memory)
    ConvertTo-Json -Compress -InputObject @{
        status      = [int]$resp.StatusCode
        location    = [string]$resp.Headers['Location']
        body        =[Convert]::ToBase64String($memory.ToArray())
    }
    $resp.Close()
} catch {
    ConvertTo-Json -Compress -InputObject @{ error = $_.Exception.Message }
}
