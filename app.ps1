# Sunhub no Windows: prepara (na primeira vez) e abre o app.
#
#   .\app.cmd             (ou duplo clique no app.cmd)
#   .\app.cmd --install   só instala/atualiza, sem abrir
#
# Não precisa de administrador. Precisa do Git (winget install Git.Git).
# O Python é baixado automaticamente pelo uv se você não tiver.
param([switch]$Install)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv   = Join-Path $Root ".venv"
$Py     = Join-Path $Venv "Scripts\python.exe"
$Pyw    = Join-Path $Venv "Scripts\pythonw.exe"
$Cli    = Join-Path $Venv "Scripts\sunhub.exe"
$Marker = Join-Path $Venv ".sunhub-installed"

function Say($m)  { Write-Host "-> $m" -ForegroundColor Yellow }
function Good($m) { Write-Host "OK $m" -ForegroundColor Green }
function Fail($m) { Write-Host "ERRO $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Fail "Git não encontrado. Instale com: winget install Git.Git  (depois abra um novo terminal)"
}

# uv: cria o ambiente e, se preciso, baixa o próprio Python
$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) {
  $Candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (Test-Path $Candidate) { $Uv = $Candidate }
}
if (-not $Uv) {
  Say "instalando o uv (gerenciador de Python) no seu usuário"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
  $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (-not (Test-Path $Uv)) { Fail "não consegui instalar o uv. Veja https://docs.astral.sh/uv/" }
}

# ambiente quebrado ou pasta movida/renomeada: recria
if (Test-Path $Venv) {
  $broken = -not (Test-Path $Py)
  if (-not $broken) { & $Py -c "import sys" 2>$null; if ($LASTEXITCODE -ne 0) { $broken = $true } }
  $moved = (Test-Path $Marker) -and ((Get-Content $Marker -Raw) -ne $Root)
  if ($broken -or $moved) { Say "a pasta do app mudou de lugar; recriando o ambiente"; Remove-Item -Recurse -Force $Venv }
}

$need = (-not (Test-Path $Py)) -or (-not (Test-Path $Marker))
if (-not $need) { $need = (Get-Item (Join-Path $Root "pyproject.toml")).LastWriteTime -gt (Get-Item $Marker).LastWriteTime }
if (-not $need) { & $Py -c "import sunhub, PySide6" 2>$null; $need = ($LASTEXITCODE -ne 0) }

if ($need) {
  Say "preparando o Sunhub (só na primeira vez; baixa ~150 MB)..."
  if (-not (Test-Path $Py)) { & $Uv venv -q --python 3.12 $Venv; if ($LASTEXITCODE -ne 0) { Fail "uv venv falhou" } }
  & $Uv pip install -q --python $Py -e "$Root[app]"
  if ($LASTEXITCODE -ne 0) { Fail "a instalação das dependências falhou" }
  Set-Content -Path $Marker -Value $Root -NoNewline
  & $Cli setup --quiet
  if ($LASTEXITCODE -ne 0) { Fail "sunhub setup falhou" }
  Good "instalado: 'Sunhub' no Menu Iniciar e o comando 'sunhub' em terminais novos"
}

if ($Install) { exit 0 }

$Ready = Join-Path $env:TEMP "sunhub-app.ready"
$Log   = Join-Path $env:TEMP "sunhub-app.log"
Remove-Item $Ready -ErrorAction SilentlyContinue
$env:SUNHUB_READY_FILE = $Ready
$proc = Start-Process -FilePath $Pyw -ArgumentList "-m", "sunhub", "app" -PassThru -RedirectStandardError $Log
for ($i = 0; $i -lt 100; $i++) {
  if (Test-Path $Ready) { break }
  if ($proc.HasExited) { break }
  Start-Sleep -Milliseconds 200
}
if (Test-Path $Ready) { Good "Sunhub aberto. Depois é só abrir pelo Menu Iniciar (ou: sunhub app)."; exit 0 }
if (-not $proc.HasExited) { Say "o Sunhub está demorando para abrir; log em $Log"; exit 0 }
Write-Host "ERRO a janela não abriu. Últimas linhas do log ($Log):" -ForegroundColor Red
Get-Content $Log -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object { "    $_" }
exit 1
