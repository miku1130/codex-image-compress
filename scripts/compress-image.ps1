param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "image_compress.py"

$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    & $launcher.Source -3.12 $script @Arguments
    if ($LASTEXITCODE -eq 0) { exit 0 }
    & $launcher.Source -3 $script @Arguments
    exit $LASTEXITCODE
}

& python $script @Arguments
exit $LASTEXITCODE
