$ErrorActionPreference = "Stop"

$demoRoot = "C:\Users\vinayakram.r\codex-demo-repos"
$repos = @(
    @{
        Name = "multiagent-order-orchestrator"
        Path = Join-Path $demoRoot "multiagent-order-orchestrator"
        BaseBranch = "main"
    },
    @{
        Name = "multiagent-support-copilot"
        Path = Join-Path $demoRoot "multiagent-support-copilot"
        BaseBranch = "main"
    }
)

Write-Host "Resetting demo repos for happy-path remediation..."

Get-Process git -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

foreach ($repo in $repos) {
    $repoPath = $repo.Path
    $baseBranch = $repo.BaseBranch

    if (-not (Test-Path $repoPath)) {
        throw "Demo repo not found: $repoPath"
    }

    Write-Host ""
    Write-Host "Preparing $($repo.Name) at $repoPath"

    git -C $repoPath checkout $baseBranch
    git -C $repoPath fetch origin $baseBranch
    git -C $repoPath reset --hard "origin/$baseBranch"
    git -C $repoPath clean -fd

    $branches = git -C $repoPath branch --list "codex/*"
    foreach ($branchLine in $branches) {
        $branchName = $branchLine.Trim().TrimStart("*").Trim()
        if ($branchName) {
            git -C $repoPath branch -D $branchName
        }
    }

    Write-Host "Ready: $($repo.Name)"
}

Write-Host ""
Write-Host "Demo repositories are reset to latest origin/main and stale codex branches are removed."
