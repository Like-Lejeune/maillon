# Installe l'agent Maillon pour l'utilisateur courant (Windows), sans droits administrateur.
# À lancer depuis le dossier agent\ du dépôt, dans PowerShell :
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# L'agent est installé dans %LOCALAPPDATA%\Maillon\agent\venv.

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Prefix = Join-Path $env:LOCALAPPDATA "Maillon\agent"
$Venv = Join-Path $Prefix "venv"

# Le lanceur « py » est installé avec Python depuis python.org.
$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = @("py", "-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = @("python")
} else {
    Write-Error "Python est introuvable. Installez Python 3.10 ou plus récent depuis https://www.python.org/downloads/ (cochez « Add python.exe to PATH »)."
}

$Exe = $Python[0]
$PyArgs = @()
if ($Python.Count -gt 1) { $PyArgs = $Python[1..($Python.Count - 1)] }

& $Exe @PyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python 3.10 ou plus récent est requis."
}

Write-Host "Installation dans $Prefix"
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
& $Exe @PyArgs -m venv $Venv
if ($LASTEXITCODE -ne 0) { Write-Error "Création de l'environnement Python impossible." }

$Pip = Join-Path $Venv "Scripts\pip.exe"
& $Pip install --quiet --upgrade pip
& $Pip install --quiet $Here
if ($LASTEXITCODE -ne 0) { Write-Error "Installation de l'agent impossible." }

$Agent = Join-Path $Venv "Scripts\maillon-agent.exe"
& $Agent --version

$Smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $Smi -and -not (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")) {
    Write-Warning "nvidia-smi est introuvable. Installez le pilote NVIDIA, sinon la machine sera déclarée sans GPU."
}

Write-Host ""
Write-Host "Commande de l'agent : $Agent"
Write-Host "Étape suivante : & `"$Agent`" info, puis & `"$Agent`" enroll --server https://<adresse du serveur>"
