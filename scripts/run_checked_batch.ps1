param(
    [Parameter(Mandatory = $true)] [string] $SourceRoot,
    [Parameter(Mandatory = $true)] [string] $Template,
    [Parameter(Mandatory = $true)] [string] $Criteria,
    [Parameter(Mandatory = $true)] [string] $Approval,
    [Parameter(Mandatory = $true)] [string] $OutputRoot,
    [string] $Practice = '1',
    [string] $Model = 'qwen3.5:4b'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
foreach ($path in @($SourceRoot, $Template, $Criteria, $Approval, $python)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Path not found: $path" }
}
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $output) {
    $existing = Get-ChildItem -LiteralPath $output -Recurse -File -ErrorAction SilentlyContinue
    if ($existing) { throw "Output directory already contains files: $output" }
}
New-Item -ItemType Directory -Force -Path $output | Out-Null

# Conservative selection: only an explicit PR marker is selected.
$patterns = @{
    '1' = '(?i)(\u043F\u0440\u0430\u043A\u0442\u0438\u0447\u0435\u0441\u043A\w*\s*(?:\u0440\u0430\u0431\u043E\u0442\w*\s*)?(?:\u2116\s*)?1|(?:^|[^\p{L}\p{N}])\u043F\u0440\s*[._\u2116-]?\s*0?1(?:$|[^\p{L}\p{N}])|(?:^|[^\p{L}\p{N}])\u043F\s*1(?:$|[^\p{L}\p{N}]))'
}
if (-not $patterns.ContainsKey($Practice)) { throw "No safe practice pattern for: $Practice" }
$pattern = $patterns[$Practice]
$files = @(Get-ChildItem -LiteralPath $source -Recurse -File -Filter '*.docx' |
    Where-Object { $_.Name -match $pattern } | Sort-Object FullName)
if (-not $files) { throw "No explicitly named DOCX files found for PR$Practice" }

$manifestPath = Join-Path $output 'batch-manifest.json'
$records = [Collections.Generic.List[object]]::new()
$env:PYTHONUTF8 = '1'
for ($index = 0; $index -lt $files.Count; $index++) {
    $file = $files[$index]
    $relative = $file.FullName.Substring($source.Length).TrimStart('\')
    $group = Split-Path -Leaf (Split-Path -Parent $file.FullName)
    $groupDir = Join-Path $output $group
    New-Item -ItemType Directory -Force -Path $groupDir | Out-Null
    $stamp = '{0:D3}' -f ($index + 1)
    $resultPath = Join-Path $groupDir "$stamp.result.json"
    $stdoutPath = Join-Path $groupDir "$stamp.stdout.txt"
    $stderrPath = Join-Path $groupDir "$stamp.stderr.txt"
    Write-Host ("[{0}/{1}] {2}" -f ($index + 1), $files.Count, $relative)
    $resultRelative = $resultPath.Substring($output.Length).TrimStart('\')
    $record = [ordered]@{
        index = $index + 1; source_relative_path = $relative; group = $group
        source_name = $file.Name; practice = "PR$Practice"; status = 'failed'
        result_relative_path = $resultRelative
        proposed_total = $null; scored_points = $null; max_score = $null
        manual_review_required = $null; error = $null
    }
    try {
        & $python -m student_ai_checker.cli check `
            --document $file.FullName `
            --template $Template `
            --criteria $Criteria `
            --approval $Approval `
            --checked-scoring `
            --output $resultPath `
            --model $Model 1> $stdoutPath 2> $stderrPath
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0 -or -not (Test-Path -LiteralPath $resultPath)) {
            $record.error = "CLI exited with code $exitCode"
        } else {
            $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $record.status = 'completed'
            $record.proposed_total = $result.summary.proposed_total
            $record.scored_points = $result.summary.scored_points
            $record.max_score = $result.summary.max_score
            $record.manual_review_required = $result.summary.manual_review_required
        }
    } catch {
        $record.error = $_.Exception.Message
    }
    $records.Add([PSCustomObject]$record)
    [PSCustomObject]@{
        schema_version = 1; generated_at = [DateTime]::UtcNow.ToString('o')
        source_root = $source; practice = "PR$Practice"; model = $Model
        case_count = $records.Count; completed_count = @($records | Where-Object status -eq 'completed').Count
        failed_count = @($records | Where-Object status -eq 'failed').Count; cases = @($records)
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}
Write-Host ("Completed: {0}/{1}; failed: {2}" -f @($records | Where-Object status -eq 'completed').Count, $files.Count, @($records | Where-Object status -eq 'failed').Count)
