<# 
Copyright (c) 2026 Jianbin Liu-CFLab.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

.SYNOPSIS
Creates, removes, or inspects Windows Firewall rules for Unity Editor ROS 2 / Fast DDS tests.

.DESCRIPTION
This development helper opens the selected Unity Editor executable for inbound and outbound
traffic. It is intentionally broader than the final release hardening rule because ROS 2 DDS
uses dynamic UDP ports in addition to any configured Fast DDS Discovery Server port.

By default, Install also disables existing Unity Editor Block rules because Windows Firewall
block rules can override allow rules.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\Scripts\firewall\Set-UnityFirewallRules.ps1 -Mode Install

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\Scripts\firewall\Set-UnityFirewallRules.ps1 -Mode Status

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\Scripts\firewall\Set-UnityFirewallRules.ps1 -Mode Remove
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("Install", "Remove", "Status")]
    [string]$Mode = "Install",

    [string]$UnityPath = "",

    [string]$RulePrefix = "R2FU WSL2 FastDDS Unity",

    [ValidateSet("Any", "Domain", "Private", "Public")]
    [string[]]$Profile = @("Any"),

    [switch]$KeepExistingBlockRules
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-SelfElevated {
    $scriptPath = $PSCommandPath
    if ([string]::IsNullOrWhiteSpace($scriptPath)) {
        throw "Cannot restart elevated because the script path is unavailable."
    }

    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$scriptPath`"",
        "-Mode", $Mode,
        "-RulePrefix", "`"$RulePrefix`""
    )
    if (-not [string]::IsNullOrWhiteSpace($UnityPath)) {
        $argumentList += @("-UnityPath", "`"$UnityPath`"")
    }
    foreach ($profileName in $Profile) {
        $argumentList += @("-Profile", $profileName)
    }
    if ($KeepExistingBlockRules) {
        $argumentList += "-KeepExistingBlockRules"
    }
    if ($WhatIfPreference) {
        $argumentList += "-WhatIf"
    }

    Write-Host "Restarting elevated PowerShell for firewall $Mode..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argumentList
}

function Resolve-UnityPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        $unityHubEditorRoot = Join-Path -Path $Env:ProgramFiles -ChildPath "Unity\Hub\Editor"
        $candidates = @()
        if (Test-Path -LiteralPath $unityHubEditorRoot -PathType Container) {
            $candidates = Get-ChildItem -LiteralPath $unityHubEditorRoot -Directory |
                ForEach-Object { Join-Path -Path $_.FullName -ChildPath "Editor\Unity.exe" } |
                Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
                Sort-Object -Descending
        }

        if ($candidates.Count -eq 0) {
            throw "Unity executable not found. Pass -UnityPath or install Unity through Unity Hub."
        }
        $Path = $candidates[0]
    }

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Unity executable not found: $Path"
    }

    return (Resolve-Path -LiteralPath $Path).ProviderPath
}

function ConvertTo-NormalizedProgramPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim('"'))
    $expanded = $expanded.Replace("/", "\")

    try {
        if (Test-Path -LiteralPath $expanded -PathType Leaf) {
            $expanded = (Resolve-Path -LiteralPath $expanded).ProviderPath
        }
    }
    catch {
        # Firewall rules may contain stale application paths. Normalize the text form anyway.
    }

    return $expanded.TrimEnd("\").ToLowerInvariant()
}

function Test-IsRequestedUnityProgram {
    param(
        [string]$ProgramPath,
        [string]$ResolvedUnityPath
    )

    $program = ConvertTo-NormalizedProgramPath -Path $ProgramPath
    $target = ConvertTo-NormalizedProgramPath -Path $ResolvedUnityPath
    return $program -eq $target
}

function Test-LooksLikeUnityEditorProgram {
    param([string]$ProgramPath)

    $program = ConvertTo-NormalizedProgramPath -Path $ProgramPath
    if ([string]::IsNullOrEmpty($program)) {
        return $false
    }

    $fileName = [System.IO.Path]::GetFileName($program)
    if ($fileName -ne "unity.exe") {
        return $false
    }

    return ($program -like "*\unity\hub\editor\*\editor\unity.exe" -or
            $program -like "*\editor\unity.exe")
}

function Get-UnityFirewallRuleInfo {
    param([string]$ResolvedUnityPath)

    foreach ($rule in (Get-NetFirewallRule -ErrorAction Stop)) {
        $app = $rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
        if ($null -eq $app -or [string]::IsNullOrEmpty($app.Program)) {
            continue
        }

        $matchesRequestedUnity = Test-IsRequestedUnityProgram -ProgramPath $app.Program -ResolvedUnityPath $ResolvedUnityPath
        $looksLikeUnityEditor = Test-LooksLikeUnityEditorProgram -ProgramPath $app.Program
        $displayNameLooksLikeUnity = $rule.DisplayName -like "*Unity*Editor*"

        if ($matchesRequestedUnity -or $looksLikeUnityEditor -or $displayNameLooksLikeUnity) {
            [pscustomobject]@{
                Name                  = $rule.Name
                DisplayName           = $rule.DisplayName
                Enabled               = $rule.Enabled
                Direction             = $rule.Direction
                Action                = $rule.Action
                Profile               = $rule.Profile
                Program               = $app.Program
                MatchesRequestedUnity = $matchesRequestedUnity
                LooksLikeUnityEditor  = $looksLikeUnityEditor
            }
        }
    }
}

function Invoke-LoggedNativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    Write-Host ">> $FilePath $($Arguments -join ' ')" -ForegroundColor DarkGray
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function ConvertTo-NetshProfile {
    param([string[]]$Profiles)

    if ($Profiles -contains "Any") {
        return "any"
    }

    return (($Profiles | ForEach-Object { $_.ToLowerInvariant() }) -join ",")
}

function Get-UnityEditorBlockRuleCandidates {
    param([string]$ResolvedUnityPath)

    $rules = @()
    $patterns = @("*Unity*Editor*", "*Unity*")
    foreach ($pattern in $patterns) {
        Write-Host "Scanning enabled Block rules with display-name pattern '$pattern'..." -ForegroundColor DarkGray
        $rules += @(Get-NetFirewallRule -DisplayName $pattern -ErrorAction SilentlyContinue |
            Where-Object { $_.Action -eq "Block" -and $_.Enabled -eq "True" })
    }

    $rules |
        Sort-Object Name -Unique |
        Where-Object {
            $app = $_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
            if ($null -eq $app -or [string]::IsNullOrEmpty($app.Program)) {
                return $false
            }

            return ((Test-IsRequestedUnityProgram -ProgramPath $app.Program -ResolvedUnityPath $ResolvedUnityPath) -or
                    (Test-LooksLikeUnityEditorProgram -ProgramPath $app.Program))
        }
}

function Remove-R2FURules {
    param([string]$Prefix)

    foreach ($displayName in @("$Prefix Inbound Allow", "$Prefix Outbound Allow")) {
        Write-Host "Checking existing R2FU rule: $displayName" -ForegroundColor DarkGray
        $existing = @(Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue)
        if ($existing.Count -eq 0) {
            Write-Host "  not present" -ForegroundColor DarkGray
            continue
        }

        foreach ($rule in $existing) {
            if ($PSCmdlet.ShouldProcess($rule.DisplayName, "Remove firewall rule")) {
                Write-Host "Removing existing rule: $($rule.DisplayName)" -ForegroundColor DarkGray
                Remove-NetFirewallRule -Name $rule.Name | Out-Null
            }
        }
    }
}

function Disable-UnityBlockRules {
    param([string]$ResolvedUnityPath)

    $blockRules = @(Get-UnityEditorBlockRuleCandidates -ResolvedUnityPath $ResolvedUnityPath)

    foreach ($entry in $blockRules) {
        if ($PSCmdlet.ShouldProcess($entry.DisplayName, "Disable existing Unity Editor Block rule")) {
            Write-Host "Disabling Block rule: $($entry.DisplayName)" -ForegroundColor Yellow
            Disable-NetFirewallRule -Name $entry.Name | Out-Null
        }
    }

    return @($blockRules).Count
}

function Install-R2FURules {
    param(
        [string]$ResolvedUnityPath,
        [string]$Prefix,
        [string[]]$Profiles
    )

    Remove-R2FURules -Prefix $Prefix

    $netshProfile = ConvertTo-NetshProfile -Profiles $Profiles

    if ($PSCmdlet.ShouldProcess("$Prefix Inbound Allow", "Create firewall rule")) {
        Invoke-LoggedNativeCommand -FilePath "netsh.exe" -Arguments @(
            "advfirewall", "firewall", "add", "rule",
            "name=$Prefix Inbound Allow",
            "dir=in",
            "action=allow",
            "program=$ResolvedUnityPath",
            "enable=yes",
            "profile=$netshProfile"
        )
    }

    if ($PSCmdlet.ShouldProcess("$Prefix Outbound Allow", "Create firewall rule")) {
        Invoke-LoggedNativeCommand -FilePath "netsh.exe" -Arguments @(
            "advfirewall", "firewall", "add", "rule",
            "name=$Prefix Outbound Allow",
            "dir=out",
            "action=allow",
            "program=$ResolvedUnityPath",
            "enable=yes",
            "profile=$netshProfile"
        )
    }
}

$resolvedUnityPath = Resolve-UnityPath -Path $UnityPath

if ($Mode -ne "Status" -and -not (Test-IsAdministrator)) {
    Invoke-SelfElevated
    return
}

switch ($Mode) {
    "Install" {
        Write-Host "R2FU Unity firewall setup starting..." -ForegroundColor Cyan
        Write-Host "Unity executable: $resolvedUnityPath"
        Write-Host "Profiles: $($Profile -join ', ')"
        Write-Host "Protocol: Any"
        Write-Host "Direction: Inbound and Outbound"
        Write-Host ""

        Install-R2FURules -ResolvedUnityPath $resolvedUnityPath -Prefix $RulePrefix -Profiles $Profile

        $disabledBlockRuleCount = 0
        if (-not $KeepExistingBlockRules) {
            Write-Host ""
            Write-Host "Checking Unity Editor Block rules to disable..." -ForegroundColor Cyan
            $disabledBlockRuleCount = Disable-UnityBlockRules -ResolvedUnityPath $resolvedUnityPath
        }

        Write-Host "Installed firewall allow rules for:" -ForegroundColor Green
        Write-Host "  $resolvedUnityPath"
        Write-Host "Profiles: $($Profile -join ', ')"
        Write-Host "Protocol: Any"
        Write-Host "Direction: Inbound and Outbound"
        if (-not $KeepExistingBlockRules) {
            Write-Host "Disabled Unity Editor Block rules: $disabledBlockRuleCount" -ForegroundColor Yellow
        }

        $remainingBlocks = @(Get-UnityEditorBlockRuleCandidates -ResolvedUnityPath $resolvedUnityPath)
        if ($remainingBlocks.Count -gt 0) {
            Write-Host ""
            Write-Host "WARNING: enabled Unity-related Block rules still exist:" -ForegroundColor Red
            $remainingBlocks | Sort-Object Direction, DisplayName |
                Format-Table DisplayName, Direction, Profile -AutoSize
        }
    }

    "Remove" {
        Remove-R2FURules -Prefix $RulePrefix
        Write-Host "Removed firewall rules matching '$RulePrefix *'." -ForegroundColor Green
        Write-Host "Note: previously disabled Unity block rules are not re-enabled automatically."
    }

    "Status" {
        Write-Host "Unity executable:" -ForegroundColor Cyan
        Write-Host "  $resolvedUnityPath"
        Write-Host ""
        Write-Host "R2FU allow rules:" -ForegroundColor Cyan
        $r2fuRules = @()
        foreach ($displayName in @("$RulePrefix Inbound Allow", "$RulePrefix Outbound Allow")) {
            $r2fuRules += @(Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue)
        }
        if ($r2fuRules.Count -eq 0) {
            Write-Host "  none" -ForegroundColor Yellow
        }
        else {
            $r2fuRules | Sort-Object Direction, DisplayName |
                Format-Table DisplayName, Enabled, Direction, Action, Profile -AutoSize
        }

        Write-Host ""
        Write-Host "Enabled Unity Editor Block rule candidates:" -ForegroundColor Cyan
        $blockRules = @(Get-UnityEditorBlockRuleCandidates -ResolvedUnityPath $resolvedUnityPath)
        if ($blockRules.Count -eq 0) {
            Write-Host "  none" -ForegroundColor Green
        }
        else {
            $blockRules | Sort-Object Direction, DisplayName |
                Format-Table DisplayName, Enabled, Direction, Action, Profile -AutoSize
        }
    }
}
