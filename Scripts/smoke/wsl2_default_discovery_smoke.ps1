param(
    [string]$UnityEditor = "C:\Program Files\Unity\Hub\Editor\6000.3.14f1\Editor\Unity.exe",
    [string]$WorkspaceRoot = "",
    [string]$ProjectPath = "",
    [string]$WorkRoot = "",
    [string]$DomainId = "134",
    [switch]$FailOnUnityCrash
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
} else {
    $WorkspaceRoot = [System.IO.Path]::GetFullPath($WorkspaceRoot)
}
if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $ProjectPath = Join-Path $WorkspaceRoot "R2FUUnityLoadSmoke"
}
if ([string]::IsNullOrWhiteSpace($WorkRoot)) {
    $WorkRoot = Join-Path $WorkspaceRoot ".build\wsl2-r2fu-default-discovery-smoke"
}

$PublishTopic = "r2fu_local_default_r2fu_to_win"
$SubscribeTopic = "r2fu_local_default_win_to_r2fu"
$PublishMarker = "r2fu_to_win_local_default"
$SubscribeMarker = "win_to_r2fu_local_default"

function Require-Path {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
}

function Wait-ProcessOrKill {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds,
        [string]$Label
    )
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
        try { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue } catch {}
        throw "$Label timed out after $TimeoutSeconds seconds"
    }
    $Process.Refresh()
    return $Process.ExitCode
}

function Wait-LogContains {
    param([string]$Path, [string]$Marker, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $text = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
        if ($text -match [regex]::Escape($Marker)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Remove-StaleUnityLock {
    param([string]$ProjectPath)

    $lockFile = Join-Path $ProjectPath "Temp\UnityLockfile"
    if (-not (Test-Path -LiteralPath $lockFile)) {
        return
    }

    $escapedProjectPath = [regex]::Escape($ProjectPath)
    $activeUnity = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "Unity.exe" -and
            $_.CommandLine -match $escapedProjectPath
        }

    if ($activeUnity) {
        throw "Unity project is already open; refusing to remove active UnityLockfile: $lockFile"
    }

    Remove-Item -LiteralPath $lockFile -Force
}

function Set-UnityDefaultDiscoveryEnv {
    $env:ROS_DOMAIN_ID = $DomainId
    $env:RMW_IMPLEMENTATION = "rmw_fastrtps_cpp"
    $env:ROS_AUTOMATIC_DISCOVERY_RANGE = "SUBNET"
    Remove-Item Env:\ROS_DISCOVERY_SERVER -ErrorAction SilentlyContinue
    Remove-Item Env:\FASTRTPS_DEFAULT_PROFILES_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:\FASTDDS_DEFAULT_PROFILES_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:\SKIP_DEFAULT_XML -ErrorAction SilentlyContinue
}

function New-WslDefaultDiscoveryCommand {
    param([string]$Ros2Command)

    return @"
set -eo pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID='$DomainId'
export RMW_IMPLEMENTATION='rmw_fastrtps_cpp'
export ROS_AUTOMATIC_DISCOVERY_RANGE='SUBNET'
unset ROS_DISCOVERY_SERVER
unset FASTRTPS_DEFAULT_PROFILES_FILE
unset FASTDDS_DEFAULT_PROFILES_FILE
unset SKIP_DEFAULT_XML
$Ros2Command
"@
}

Require-Path $UnityEditor "Unity editor"
Require-Path $ProjectPath "Unity smoke project"
Remove-StaleUnityLock -ProjectPath $ProjectPath

$resolvedWork = [System.IO.Path]::GetFullPath($WorkRoot)
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $WorkspaceRoot ".build"))
if (-not $resolvedWork.StartsWith($buildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe WorkRoot: $resolvedWork"
}
if (Test-Path -LiteralPath $WorkRoot) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

$runtimeLog = Join-Path $WorkRoot "unity-default-discovery-smoke.log"
$wslEchoOut = Join-Path $WorkRoot "wsl-echo-r2fu-to-wsl.out.log"
$wslEchoErr = Join-Path $WorkRoot "wsl-echo-r2fu-to-wsl.err.log"
$wslPubOut = Join-Path $WorkRoot "wsl-pub-wsl-to-r2fu.out.log"
$wslPubErr = Join-Path $WorkRoot "wsl-pub-wsl-to-r2fu.err.log"

$unityRuntime = $null
$wslEcho = $null
$wslPub = $null

try {
    Write-Host "R2FU WSL2 default discovery smoke"
    Write-Host "ROS_DOMAIN_ID=$DomainId"
    Write-Host "RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
    Write-Host "ROS_DISCOVERY_SERVER=<unset>"
    Write-Host "ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET"

    $wslEchoCommand = New-WslDefaultDiscoveryCommand -Ros2Command `
        "timeout 120 ros2 topic echo --once /$PublishTopic std_msgs/msg/String"
    $wslEchoArgs = @("-d", "Ubuntu", "--", "bash", "-lc", $wslEchoCommand)
    $wslEcho = Start-Process -FilePath "wsl.exe" `
        -ArgumentList $wslEchoArgs `
        -RedirectStandardOutput $wslEchoOut `
        -RedirectStandardError $wslEchoErr `
        -WindowStyle Hidden `
        -PassThru

    Set-UnityDefaultDiscoveryEnv
    $unityRuntime = Start-Process -FilePath $UnityEditor `
        -ArgumentList @(
            "-projectPath", $ProjectPath,
            "-batchmode",
            "-nographics",
            "-quit",
            "-executeMethod", "R2FUWsl2DiscoverySmoke.RunRuntimeLocalDefault",
            "-logFile", $runtimeLog
        ) `
        -WindowStyle Hidden `
        -PassThru

    $subscriberReady = Wait-LogContains -Path $runtimeLog -Marker "R2FU_LOCAL_DEFAULT_SUBSCRIBER_READY" -TimeoutSeconds 180
    if (-not $subscriberReady) {
        throw "Timed out waiting for R2FU_LOCAL_DEFAULT_SUBSCRIBER_READY"
    }

    $wslPubCommand = New-WslDefaultDiscoveryCommand -Ros2Command `
        "timeout 120 ros2 topic pub -w 0 --times 20 -r 5 /$SubscribeTopic std_msgs/msg/String `"{data: $SubscribeMarker}`""
    $wslPubArgs = @("-d", "Ubuntu", "--", "bash", "-lc", $wslPubCommand)
    $wslPub = Start-Process -FilePath "wsl.exe" `
        -ArgumentList $wslPubArgs `
        -RedirectStandardOutput $wslPubOut `
        -RedirectStandardError $wslPubErr `
        -WindowStyle Hidden `
        -PassThru

    $unityExit = Wait-ProcessOrKill -Process $unityRuntime -TimeoutSeconds 240 -Label "Unity default discovery smoke"
    Wait-ProcessOrKill -Process $wslEcho -TimeoutSeconds 30 -Label "WSL echo" | Out-Null
    if ($wslPub -ne $null -and -not $wslPub.HasExited) {
        Wait-ProcessOrKill -Process $wslPub -TimeoutSeconds 30 -Label "WSL publisher" | Out-Null
    }

    $unityText = Get-Content -Raw -LiteralPath $runtimeLog -ErrorAction SilentlyContinue
    $wslEchoText = (Get-Content -Raw -LiteralPath $wslEchoOut -ErrorAction SilentlyContinue) +
        (Get-Content -Raw -LiteralPath $wslEchoErr -ErrorAction SilentlyContinue)

    $unityReceived = $unityText -match [regex]::Escape("R2FU_LOCAL_DEFAULT_RECEIVED=$SubscribeMarker")
    $unityPass = $unityText -match [regex]::Escape("R2FU_LOCAL_DEFAULT_SMOKE_PASS")
    $wslReceived = $wslEchoText -match [regex]::Escape($PublishMarker)

    Write-Host "Unity exit code: $unityExit"
    Write-Host "Unity received WSL marker: $unityReceived"
    Write-Host "Unity pass marker: $unityPass"
    Write-Host "WSL received R2FU marker: $wslReceived"

    if ($unityReceived -and $unityPass -and $wslReceived) {
        Write-Host "R2FU_WSL2_DEFAULT_DISCOVERY_COMMUNICATION_GREEN"
        if ($unityExit -eq 0) {
            Write-Host "R2FU_WSL2_DEFAULT_DISCOVERY_PROCESS_GREEN"
            exit 0
        }

        Write-Host "R2FU_WSL2_DEFAULT_DISCOVERY_PROCESS_RED_EXIT_$unityExit" -ForegroundColor Yellow
        if ($FailOnUnityCrash) {
            exit 1
        }
        exit 0
    }

    Write-Host "R2FU_WSL2_DEFAULT_DISCOVERY_COMMUNICATION_RED" -ForegroundColor Red
    exit 1
}
finally {
    if ($unityRuntime -ne $null -and -not $unityRuntime.HasExited) {
        try { Stop-Process -Id $unityRuntime.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if ($wslEcho -ne $null -and -not $wslEcho.HasExited) {
        try { Stop-Process -Id $wslEcho.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if ($wslPub -ne $null -and -not $wslPub.HasExited) {
        try { Stop-Process -Id $wslPub.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
}
