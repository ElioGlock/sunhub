# Desfaz tudo o que o Sunhub configurou no Windows (skills, MCP, tarefa agendada, atalho, PATH).
# Use -Purge para apagar também o hub (%USERPROFILE%\sunhub com clones e índice).
param([switch]$Purge)
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Cli  = Join-Path $Root ".venv\Scripts\sunhub.exe"
$args_ = @("uninstall", "--yes")
if ($Purge) { $args_ += "--purge" }
if (Test-Path $Cli) { & $Cli @args_ }
elseif (Get-Command sunhub -ErrorAction SilentlyContinue) { sunhub @args_ }
Remove-Item (Join-Path $Root ".venv\.sunhub-installed") -ErrorAction SilentlyContinue
Write-Host "OK Sunhub removido do sistema. Para apagar o app, apague esta pasta: $Root" -ForegroundColor Green
