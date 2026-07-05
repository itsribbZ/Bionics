# Bionics EventGraph v0.5.11 live smoke-test
# Verifies: 5 new EventGraph tools register on the C++ bridge after UE5 rebuild + restart.
# Each tool round-trips through tools/list and (where safe) tools/call against a test BP.
#
# Prerequisites:
#   1. Rebuild done: cd C:/Users/jbro1/Documents/Sworder721/MyProject; .\Rebuild.bat MyProjectEditor Win64 Development
#   2. UE5 + Sworder721 launched (bridge auto-starts on subsystem init)
#   3. A throwaway test BP exists at /Game/Tests/BP_EventGraphSmoke (Actor BP, default content)
#
# What it verifies:
#   - Bridge alive
#   - tools/list returns at least 5 names matching "*eventgraph*"
#   - query_eventgraph returns ubergraph_pages >= 1 against the test BP
#   - add_eventgraph_event with engine ReceiveBeginPlay creates a node
#   - add_eventgraph_call_function with KismetSystemLibrary::PrintString creates a node
#   - wire_eventgraph_pins connects ReceiveBeginPlay.then -> PrintString.execute and auto-compiles cleanly

$ErrorActionPreference = "Stop"
$BRIDGE = "http://127.0.0.1:8090"
$INSTANCE = "$env:USERPROFILE\Documents\Sworder721\MyProject\.bionics-bridge\instance.json"
$TEST_BP = "/Game/Tests/BP_EventGraphSmoke"
$results = @()

function Add-Result($name, $pass, $detail) {
    $script:results += [pscustomobject]@{ Test = $name; Result = if ($pass) {"PASS"} else {"FAIL"}; Detail = $detail }
}

# Step 1 — Bridge liveness
Write-Host "[1/8] Polling $BRIDGE for liveness (60s timeout)..."
$alive = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method GET -UseBasicParsing -TimeoutSec 2 -SkipHttpErrorCheck
        if ($r.StatusCode -in 200, 401) { $alive = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
Add-Result "bridge.alive" $alive "GET /bridge returned status code"
if (-not $alive) { Write-Host "Bridge not alive after 60s. Is UE5 + Sworder721 loaded?" -ForegroundColor Red; exit 1 }

# Step 2 — Read bearer token
$inst = Get-Content $INSTANCE | ConvertFrom-Json
$token = $inst.token
$hasToken = $token -and $token.Length -ge 32
Add-Result "instance.token" $hasToken "instance.json token length=$($token.Length)"
if (-not $hasToken) { Write-Host "Cannot read bearer token from $INSTANCE" -ForegroundColor Red; exit 1 }

$headers = @{ "Authorization" = "Bearer $token"; "Content-Type" = "application/json" }

# Step 3 — tools/list must show 5 EventGraph tools
$listBody = @{ jsonrpc = "2.0"; id = 1; method = "tools/list"; params = @{} } | ConvertTo-Json -Compress
$listResp = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Headers $headers -Body $listBody -UseBasicParsing
$listJson = $listResp.Content | ConvertFrom-Json
$egTools = @($listJson.result.tools | Where-Object { $_.name -like "*eventgraph*" -or $_.name -like "*event_graph*" })
Add-Result "tools.list.eventgraph_count" ($egTools.Count -ge 5) "expected >=5 eventgraph tools, got $($egTools.Count)"

# Step 4 — query_eventgraph
$queryBody = @{
    jsonrpc = "2.0"; id = 2; method = "tools/call"
    params = @{ name = "query_eventgraph"; arguments = @{ asset_path = $TEST_BP } }
} | ConvertTo-Json -Compress -Depth 5
$queryResp = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Headers $headers -Body $queryBody -UseBasicParsing
$queryJson = $queryResp.Content | ConvertFrom-Json
$pages = if ($queryJson.result.content[0].text) { ($queryJson.result.content[0].text | ConvertFrom-Json).ubergraph_pages } else { 0 }
Add-Result "query_eventgraph" ($pages -ge 1) "ubergraph_pages=$pages"

# Step 5 — add engine event ReceiveBeginPlay
$addEventBody = @{
    jsonrpc = "2.0"; id = 3; method = "tools/call"
    params = @{ name = "add_eventgraph_event"; arguments = @{
        asset_path = $TEST_BP; event_type = "engine"; event_name = "ReceiveBeginPlay"; pos_x = 0; pos_y = 0
    }}
} | ConvertTo-Json -Compress -Depth 6
$evResp = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Headers $headers -Body $addEventBody -UseBasicParsing
$evJson = $evResp.Content | ConvertFrom-Json
$evCreated = $evJson.result -and -not $evJson.error
Add-Result "add_eventgraph_event.engine" $evCreated "ReceiveBeginPlay node added: $evCreated"
$eventNode = if ($evCreated) { ($evJson.result.content[0].text | ConvertFrom-Json).name } else { "" }

# Step 6 — add call function (PrintString)
$addCallBody = @{
    jsonrpc = "2.0"; id = 4; method = "tools/call"
    params = @{ name = "add_eventgraph_call_function"; arguments = @{
        asset_path = $TEST_BP; target_class = "KismetSystemLibrary"; function_name = "PrintString"
        pos_x = 400; pos_y = 0
    }}
} | ConvertTo-Json -Compress -Depth 6
$callResp = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Headers $headers -Body $addCallBody -UseBasicParsing
$callJson = $callResp.Content | ConvertFrom-Json
$callCreated = $callJson.result -and -not $callJson.error
Add-Result "add_eventgraph_call_function" $callCreated "KismetSystemLibrary::PrintString node added: $callCreated"
$callNode = if ($callCreated) { ($callJson.result.content[0].text | ConvertFrom-Json).name } else { "" }

# Step 7 — wire pins (BeginPlay.then -> PrintString.execute) + auto-compile
if ($eventNode -and $callNode) {
    $wireBody = @{
        jsonrpc = "2.0"; id = 5; method = "tools/call"
        params = @{ name = "wire_eventgraph_pins"; arguments = @{
            asset_path = $TEST_BP
            source_node = $eventNode; source_pin = "then"
            target_node = $callNode; target_pin = "execute"
            auto_compile = $true
        }}
    } | ConvertTo-Json -Compress -Depth 6
    $wireResp = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Headers $headers -Body $wireBody -UseBasicParsing
    $wireJson = $wireResp.Content | ConvertFrom-Json
    $payload = if ($wireJson.result.content[0].text) { $wireJson.result.content[0].text | ConvertFrom-Json } else { $null }
    $wired = $payload -and $payload.connected -and ($payload.compile_errors -eq 0)
    Add-Result "wire_eventgraph_pins.compile" $wired "connected=$($payload.connected) compile_errors=$($payload.compile_errors)"
} else {
    Add-Result "wire_eventgraph_pins.compile" $false "skipped (event or call node missing)"
}

# Step 8 — Variable get (assumes test BP has a default 'bIsActive' or similar; expect graceful failure if missing)
$varBody = @{
    jsonrpc = "2.0"; id = 6; method = "tools/call"
    params = @{ name = "add_eventgraph_variable_node"; arguments = @{
        asset_path = $TEST_BP; variable_name = "bIsActive"; operation = "get"; pos_x = 800; pos_y = 0
    }}
} | ConvertTo-Json -Compress -Depth 6
$varResp = Invoke-WebRequest -Uri "$BRIDGE/bridge" -Method POST -Headers $headers -Body $varBody -UseBasicParsing
$varJson = $varResp.Content | ConvertFrom-Json
# Accept either success OR explicit "Variable not found" error — we're testing the path executes
$varOk = $varJson.result -or ($varJson.error -and $varJson.error.message -match "Variable")
Add-Result "add_eventgraph_variable_node" $varOk "graceful path (success OR explicit not-found)"

# Final report
Write-Host ""
Write-Host "=== EventGraph v0.5.11 Smoke Results ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$pass = ($results | Where-Object Result -eq "PASS").Count
$total = $results.Count
Write-Host "$pass/$total passed"
if ($pass -eq $total) {
    Write-Host "EventGraph C++ tool surface verified live — promote to v0.6.0 when ready." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Failures present — review C++ EventGraphTools.cpp + module registration." -ForegroundColor Red
    exit 1
}
