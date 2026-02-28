# Deploy Training Tracker on the VPS by SSHing in and running deploy.sh.
# Run from repo root (or from deploy/). Pushes to GitHub first, then runs deploy on server.
#
# Prerequisites:
#   - SSH key auth to your server (e.g. ssh root@YOUR_SERVER_IP works without password)
#   - Server has app at /opt/training-tracker and deploy/deploy.sh
#
# Configure once (pick one):
#   A) Set env: $env:DEPLOY_HOST = "root@YOUR_SERVER_IP"
#   B) Or create deploy/deploy.config with one line: root@YOUR_SERVER_IP
#
# Usage:
#   .\deploy\deploy-via-ssh.ps1
#   .\deploy\deploy-via-ssh.ps1 -PushFirst   # default: push current branch then deploy
#   .\deploy\deploy-via-ssh.ps1 -NoPush      # deploy only (no git push)

param(
    [switch] $PushFirst = $true,
    [switch] $NoPush = $false
)

$ErrorActionPreference = "Stop"
# Script lives in deploy/; repo root is parent of deploy/
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path "$repoRoot/deploy/deploy.sh")) {
    Write-Error "Repo root not found (expected deploy/deploy.sh). Run from repo root or deploy/."
    exit 1
}

# Resolve deploy host from env or deploy/deploy.config
$hostLine = $env:DEPLOY_HOST
if (-not $hostLine) {
    $configPath = Join-Path $PSScriptRoot "deploy.config"
    if (Test-Path $configPath) {
        $hostLine = (Get-Content $configPath -Raw).Trim()
    }
}
if (-not $hostLine) {
    Write-Host "Set DEPLOY_HOST or create deploy/deploy.config with one line: user@host (e.g. root@1.2.3.4)" -ForegroundColor Yellow
    exit 1
}

Set-Location $repoRoot

if ($PushFirst -and -not $NoPush) {
    Write-Host "Pushing current branch to origin..." -ForegroundColor Cyan
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    if (-not $branch) { $branch = "multi-tenant-admin" }
    git push origin $branch
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Push failed. Fix and re-run, or use -NoPush to deploy without pushing." -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
}

Write-Host "SSH to $hostLine and running deploy..." -ForegroundColor Cyan
$remoteCmd = "cd /opt/training-tracker && sudo ./deploy/deploy.sh"
ssh $hostLine $remoteCmd
exit $LASTEXITCODE
