param(
    [string]$UnityEditor = "C:\Program Files\Unity\Hub\Editor\6000.3.14f1\Editor\Unity.exe",
    [string]$WorkspaceRoot = "",
    [string]$ProjectPath = "",
    [string]$WorkRoot = "",
    [string]$DomainId = "133",
    [string]$ServerPort = "11811",
    [switch]$SkipRuntime,
    [switch]$SkipPlayStop
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
    $WorkRoot = Join-Path $WorkspaceRoot ".build\wsl2-r2fu-runtime-smoke"
}

$PublishTopic = "r2fu_wsl2_fastdds_r2fu_to_wsl"
$SubscribeTopic = "r2fu_wsl2_fastdds_wsl_to_r2fu"
$PublishMarker = "r2fu_to_wsl_discovery_server"
$SubscribeMarker = "wsl_to_r2fu_discovery_server"
$WslRosEnvWindows = Join-Path $WorkspaceRoot ".build\wsl2-discovery-test\wsl_ros_env.sh"
$WslRosEnv = ""

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

function Invoke-WslRos2 {
    param([string[]]$Arguments)
    $output = & wsl.exe -d Ubuntu -- env `
        "ROS_DOMAIN_ID=$DomainId" `
        "RMW_IMPLEMENTATION=rmw_fastrtps_cpp" `
        "ROS_DISCOVERY_SERVER=127.0.0.1:$ServerPort" `
        bash $WslRosEnv @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        $output | ForEach-Object { Write-Host $_ }
    }
    return $exitCode
}

function Start-FastDdsServer {
    param([string]$OutLog, [string]$ErrLog)
    return Start-Process -FilePath "wsl.exe" `
        -ArgumentList @(
            "-d", "Ubuntu", "--",
            "env",
            "ROS_DOMAIN_ID=$DomainId",
            "RMW_IMPLEMENTATION=rmw_fastrtps_cpp",
            "ROS_DISCOVERY_SERVER=127.0.0.1:$ServerPort",
            "bash", $WslRosEnv,
            "fastdds", "discovery", "-i", "0", "-l", "0.0.0.0", "-p", $ServerPort
        ) `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -WindowStyle Hidden `
        -PassThru
}

function Set-UnityDiscoveryEnv {
    param([string]$WslIp)
    $env:ROS_DOMAIN_ID = $DomainId
    $env:RMW_IMPLEMENTATION = "rmw_fastrtps_cpp"
    $env:ROS_DISCOVERY_SERVER = "${WslIp}:$ServerPort"
    $env:ROS_AUTOMATIC_DISCOVERY_RANGE = "SUBNET"
}

function Assert-LogContains {
    param([string]$Path, [string]$Marker)
    $text = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($text -notmatch [regex]::Escape($Marker)) {
        throw "Missing marker '$Marker' in $Path"
    }
}

function Wait-LogContains {
    param([string]$Path, [string]$Marker, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $text = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
        if ($text -match [regex]::Escape($Marker)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for marker '$Marker' in $Path"
}

function Assert-NoCrashMarkers {
    param([string]$Path)
    $text = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
    $patterns = @(
        "Access violation",
        "mono_gc_run_finalize",
        "Bug Reporter",
        "Fatal error",
        "Crash!!!",
        "Segmentation fault"
    )
    foreach ($pattern in $patterns) {
        if ($text -match [regex]::Escape($pattern)) {
            throw "Crash marker '$pattern' found in $Path"
        }
    }
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

Require-Path $UnityEditor "Unity editor"
Require-Path $ProjectPath "Unity smoke project"
Require-Path $WslRosEnvWindows "WSL ROS env helper"
$WslRosEnv = (& wsl.exe -d Ubuntu -- wslpath -a $WslRosEnvWindows).Trim()
if ([string]::IsNullOrWhiteSpace($WslRosEnv)) {
    throw "Could not convert WSL ROS env helper path: $WslRosEnvWindows"
}
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

$WslIp = (wsl.exe -d Ubuntu -- bash -lc "hostname -I | awk '{print `$1}'").Trim()
if ([string]::IsNullOrWhiteSpace($WslIp)) {
    throw "Could not determine WSL IP"
}

$serverOut = Join-Path $WorkRoot "fastdds-server.out.log"
$serverErr = Join-Path $WorkRoot "fastdds-server.err.log"
$server = Start-FastDdsServer -OutLog $serverOut -ErrLog $serverErr
$unityRuntime = $null
$unityPlayStop = $null

try {
    Start-Sleep -Seconds 5
    Write-Host "R2FU WSL2 Fast DDS smoke"
    Write-Host "WSL_IP=$WslIp"
    Write-Host "ROS_DOMAIN_ID=$DomainId"
    Write-Host "UNITY_ROS_DISCOVERY_SERVER=${WslIp}:$ServerPort"

    if (-not $SkipRuntime) {
        $runtimeLog = Join-Path $WorkRoot "unity-runtime-smoke.log"
        $wslEchoOut = Join-Path $WorkRoot "wsl-echo-r2fu-to-wsl.out.log"
        $wslEchoErr = Join-Path $WorkRoot "wsl-echo-r2fu-to-wsl.err.log"

        $wslEcho = Start-Process -FilePath "wsl.exe" `
            -ArgumentList @(
                "-d", "Ubuntu", "--",
                "env",
                "ROS_DOMAIN_ID=$DomainId",
                "RMW_IMPLEMENTATION=rmw_fastrtps_cpp",
                "ROS_DISCOVERY_SERVER=127.0.0.1:$ServerPort",
                "bash", $WslRosEnv,
                "timeout", "120",
                "ros2", "topic", "echo", "--once", "/$PublishTopic", "std_msgs/msg/String"
            ) `
            -RedirectStandardOutput $wslEchoOut `
            -RedirectStandardError $wslEchoErr `
            -WindowStyle Hidden `
            -PassThru

        Set-UnityDiscoveryEnv -WslIp $WslIp
        $unityRuntime = Start-Process -FilePath $UnityEditor `
            -ArgumentList @(
                "-projectPath", $ProjectPath,
                "-batchmode",
                "-nographics",
                "-quit",
                "-executeMethod", "R2FUWsl2DiscoverySmoke.RunRuntime",
                "-logFile", $runtimeLog
            ) `
            -WindowStyle Hidden `
            -PassThru

        Wait-LogContains -Path $runtimeLog -Marker "R2FU_WSL2_DISCOVERY_SUBSCRIBER_READY" -TimeoutSeconds 180
        $wslPubExit = Invoke-WslRos2 @(
            "timeout", "120",
            "ros2", "topic", "pub", "-w", "0", "--times", "20", "-r", "5",
            "/$SubscribeTopic",
            "std_msgs/msg/String",
            "{data: $SubscribeMarker}"
        )
        if ($wslPubExit -ne 0) {
            throw "WSL publisher failed with exit code $wslPubExit"
        }

        $unityExit = Wait-ProcessOrKill -Process $unityRuntime -TimeoutSeconds 240 -Label "Unity runtime smoke"
        if ($unityExit -ne 0) {
            throw "Unity runtime smoke failed with exit code $unityExit"
        }

        Wait-ProcessOrKill -Process $wslEcho -TimeoutSeconds 30 -Label "WSL echo" | Out-Null
        $wslEchoText = (Get-Content -Raw -LiteralPath $wslEchoOut -ErrorAction SilentlyContinue) +
            (Get-Content -Raw -LiteralPath $wslEchoErr -ErrorAction SilentlyContinue)
        if ($wslEchoText -notmatch [regex]::Escape($PublishMarker)) {
            throw "WSL echo did not receive $PublishMarker"
        }

        Assert-LogContains -Path $runtimeLog -Marker "R2FU_WSL2_DISCOVERY_SMOKE_PASS"
        Assert-NoCrashMarkers -Path $runtimeLog
        Write-Host "R2FU_WSL2_DISCOVERY_BATCHMODE_GREEN"
    }

    if (-not $SkipPlayStop) {
        $playStopLog = Join-Path $WorkRoot "unity-play-stop-smoke.log"
        Set-UnityDiscoveryEnv -WslIp $WslIp
        $unityPlayStop = Start-Process -FilePath $UnityEditor `
            -ArgumentList @(
                "-projectPath", $ProjectPath,
                "-executeMethod", "R2FUWsl2DiscoverySmoke.RunPlayStop",
                "-logFile", $playStopLog
            ) `
            -PassThru

        $playStopExit = Wait-ProcessOrKill -Process $unityPlayStop -TimeoutSeconds 240 -Label "Unity Play/Stop smoke"
        if ($playStopExit -ne 0) {
            throw "Unity Play/Stop smoke failed with exit code $playStopExit"
        }

        Assert-LogContains -Path $playStopLog -Marker "R2FU_WSL2_PLAY_STOP_SMOKE_PASS"
        Assert-NoCrashMarkers -Path $playStopLog
        Write-Host "R2FU_WSL2_PLAY_STOP_GREEN"
    }

    Write-Host "R2FU_WSL2_FASTDDS_RUNTIME_SMOKE_GREEN"
}
finally {
    if ($unityRuntime -ne $null -and -not $unityRuntime.HasExited) {
        try { Stop-Process -Id $unityRuntime.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if ($unityPlayStop -ne $null -and -not $unityPlayStop.HasExited) {
        try { Stop-Process -Id $unityPlayStop.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    try { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue } catch {}
    Write-Host "--- fastdds server stdout ---"
    Get-Content -Raw -LiteralPath $serverOut -ErrorAction SilentlyContinue | Write-Host
    Write-Host "--- fastdds server stderr ---"
    Get-Content -Raw -LiteralPath $serverErr -ErrorAction SilentlyContinue | Write-Host
}
