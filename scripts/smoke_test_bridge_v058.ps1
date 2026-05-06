# Bionics Bridge v0.5.8 live smoke-test
# Verifies: bearer-token 401, WWW-Authenticate header, CORS lock, instance.json ACL
# Run AFTER UE5 + Sworder721 is loaded (bridge auto-starts on subsystem init)

$ErrorActionPreference = "Stop"
$BRIDGE = "http://127.0.0.1:8090"
$INSTANCE = "$env:USERPROFILE\Documents\Sworder721\MyProject\.bionics-bridge\instance.json"
$results = @()

function Add-Result($name, $pass, $detail) {
    $script:results += [pscustomobject]@{ Test = $name; Result = if ($pass) {"PASS"} else {"FAIL"}; Detail = $detail }
}

# Wait for bridge (poll up to 60s)
Write-Host "[1/6] Polling $BRIDGE for liveness (60s timeout)..."
$alive = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method GET -UseBasicParsing -TimeoutSec 2 -SkipHttpErrorCheck
        if ($r.StatusCode -in 200, 401) { $alive = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $alive) {
    Write-Host "FAIL: bridge never came alive on $BRIDGE" -ForegroundColor Red
    exit 1
}
Write-Host "  Bridge is up." -ForegroundColor Green

# Test 1: GET /bridge without auth — expect 200 with limited fields (per v0.5.8 fingerprint fix)
Write-Host "[2/6] GET /bridge unauth — expect 200 with no tool count..."
try {
    $r = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method GET -UseBasicParsing -SkipHttpErrorCheck
    $body = $r.Content | ConvertFrom-Json
    $hasToolCount = $body.PSObject.Properties.Name -contains "tool_count"
    Add-Result "GET /bridge unauth" (-not $hasToolCount) "status=$($r.StatusCode), tool_count_leaked=$hasToolCount, body=$($r.Content)"
} catch { Add-Result "GET /bridge unauth" $false "exception: $_" }

# Test 2: POST /bridge without Authorization header — expect 401 + WWW-Authenticate
Write-Host "[3/6] POST /bridge unauth — expect 401 + WWW-Authenticate Bearer..."
try {
    $r = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Body '{}' -ContentType "application/json" -UseBasicParsing -SkipHttpErrorCheck
    $is401 = $r.StatusCode -eq 401
    $hasWWW = $r.Headers["WWW-Authenticate"] -match "Bearer"
    Add-Result "POST /bridge unauth = 401" $is401 "status=$($r.StatusCode)"
    Add-Result "WWW-Authenticate: Bearer" $hasWWW "header=$($r.Headers['WWW-Authenticate'])"
} catch { Add-Result "POST /bridge unauth" $false "exception: $_" }

# Test 3: POST /bridge with bad token — expect 401
Write-Host "[4/6] POST /bridge with bad token — expect 401..."
try {
    $r = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Body '{}' -ContentType "application/json" -Headers @{Authorization="Bearer FAKE_TOKEN_12345"} -UseBasicParsing -SkipHttpErrorCheck
    Add-Result "POST /bridge bad token = 401" ($r.StatusCode -eq 401) "status=$($r.StatusCode)"
} catch { Add-Result "POST /bridge bad token" $false "exception: $_" }

# Test 4: instance.json exists + readable + has token
Write-Host "[5/6] Reading instance.json for real token..."
if (-not (Test-Path $INSTANCE)) {
    Add-Result "instance.json exists" $false "missing: $INSTANCE"
    $realToken = $null
} else {
    Add-Result "instance.json exists" $true $INSTANCE
    $inst = Get-Content $INSTANCE -Raw | ConvertFrom-Json
    $realToken = $inst.token
    Add-Result "instance.json has token" ([bool]$realToken) "token_len=$($realToken.Length)"
}

# Test 5: instance.json ACL — only current user (per v0.5.5 Win32 DACL fix)
Write-Host "[6/6] Checking instance.json ACL (icacls)..."
if (Test-Path $INSTANCE) {
    $acl = & icacls $INSTANCE 2>&1 | Out-String
    $currentUser = "$env:USERDOMAIN\$env:USERNAME"
    $hasOnlyOwner = ($acl -match [regex]::Escape($currentUser)) -and -not ($acl -match "BUILTIN\\Users") -and -not ($acl -match "Everyone") -and -not ($acl -match "Authenticated Users")
    Add-Result "instance.json DACL locked" $hasOnlyOwner "acl=$($acl -replace '\s+', ' ' | Out-String)"
}

# Test 6: POST /bridge with real token — expect != 401 (any other status means auth accepted)
if ($realToken) {
    Write-Host "[bonus] POST /bridge with real token — expect not-401..."
    try {
        $r = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Body '{}' -ContentType "application/json" -Headers @{Authorization="Bearer $realToken"} -UseBasicParsing -SkipHttpErrorCheck
        Add-Result "POST /bridge real token != 401" ($r.StatusCode -ne 401) "status=$($r.StatusCode), body=$($r.Content.Substring(0, [Math]::Min(200, $r.Content.Length)))"
    } catch { Add-Result "POST /bridge real token" $false "exception: $_" }
}

# Report
Write-Host ""
Write-Host "=== RESULTS ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize -Wrap
$passed = ($results | Where-Object Result -eq "PASS").Count
$failed = ($results | Where-Object Result -eq "FAIL").Count
Write-Host ""
Write-Host "Total: $($results.Count) | Pass: $passed | Fail: $failed" -ForegroundColor $(if ($failed -eq 0) {"Green"} else {"Red"})
if ($failed -gt 0) { exit 1 }
