# Run from any directory; stop immediately when a tool fails.
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & uv run black --check aside_patcher tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & uv run flake8 aside_patcher tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & uv run pylint aside_patcher tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $docFiles = @(Get-ChildItem -LiteralPath aside_patcher -Recurse -Filter '*.py' |
        ForEach-Object { $_.FullName })
    & uv run python -m numpydoc lint @docFiles
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & uv run mollify audit
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
