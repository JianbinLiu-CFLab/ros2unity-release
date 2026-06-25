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
Runs the Windows full validation ladder for Ros2ForUnity.

.DESCRIPTION
This wrapper derives all paths from its own location. It does not hard-code a
machine-specific workspace path, and it keeps build/log/temp/report output
inside the workspace .build directory.

Default ladder:
1. Check the selected ROS 2 Windows build environment.
2. Build the Ros2ForUnity standalone asset with ros2cs tests enabled.
3. Run ros2cs_tests.
4. Collect verbose colcon test results.
5. Check required managed and native plugin files in the staged Unity asset.

.EXAMPLE
.\Scripts\rebuild\run_r2fu_windows_validation.ps1

.EXAMPLE
.\Scripts\rebuild\run_r2fu_windows_validation.ps1 -Clean -ParallelWorkers 8

.EXAMPLE
.\Scripts\rebuild\run_r2fu_windows_validation.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$DryRun,
    [switch]$SkipBuild,
    [switch]$SkipTests,
    [switch]$SkipAssetSanity,
    [switch]$ConsoleDirect,
    [switch]$QuietBuild,
    [ValidateSet("humble", "jazzy", "lyrical")]
    [string]$RosDistro = "jazzy",
    [ValidateRange(1, 256)]
    [int]$ParallelWorkers = [System.Environment]::ProcessorCount
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path -Path $scriptDir -ChildPath "..\.."))
$buildRoot = Join-Path -Path $workspaceRoot -ChildPath ".build"
$pathSuffix = if ($RosDistro -eq "jazzy") { "" } else { "_$RosDistro" }
$buildBase = Join-Path -Path $buildRoot -ChildPath "b$pathSuffix"
$logBase = Join-Path -Path $buildRoot -ChildPath "l$pathSuffix"
$testLogBase = Join-Path -Path $buildRoot -ChildPath "tl$pathSuffix"
$tempRoot = Join-Path -Path $buildRoot -ChildPath "tmp"
$reportRoot = Join-Path -Path $buildRoot -ChildPath "reports"
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$summaryPath = Join-Path -Path $reportRoot -ChildPath "r2fu-$RosDistro-windows-full-validation-$runId.json"

function Resolve-SourceRepo {
    param([Parameter(Mandatory = $true)][string]$Name)

    $thirdPartyPath = Join-Path -Path $workspaceRoot -ChildPath "third-party\$Name"
    if (Test-Path -LiteralPath $thirdPartyPath) {
        return $thirdPartyPath
    }
    return (Join-Path -Path $workspaceRoot -ChildPath $Name)
}

$r2fuRoot = Resolve-SourceRepo "ros2-for-unity"
$ros2csRoot = Resolve-SourceRepo "ros2cs"
$envScriptByDistro = @{
    humble = "Enter-Ros2HumbleEnv.py"
    jazzy = "Enter-Ros2JazzyEnv.py"
    lyrical = "Enter-Ros2LyricalEnv.py"
}
$envScriptName = $envScriptByDistro[$RosDistro]
$envScript = Join-Path -Path $workspaceRoot -ChildPath "Scripts\env\$envScriptName"
$r2fuBuildScript = Join-Path -Path $r2fuRoot -ChildPath "build.ps1"
$ros2csInstall = Join-Path -Path $ros2csRoot -ChildPath "install-$RosDistro"
$assetRoot = Join-Path -Path $r2fuRoot -ChildPath "install\asset\Ros2ForUnity"
$pluginRoot = Join-Path -Path $assetRoot -ChildPath "Plugins"

$script:Rows = New-Object System.Collections.Generic.List[object]

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-PathUnder {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $fullPath = Get-FullPath $Path
    $fullRoot = (Get-FullPath $Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $rootPrefix = $fullRoot + [System.IO.Path]::DirectorySeparatorChar

    if ($fullPath -ne $fullRoot -and -not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must stay under '$fullRoot'. Got '$fullPath'."
    }
}

function Require-Path {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
}

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Hint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required command '$Name' was not found. $Hint"
    }
    return $command.Source
}

function Remove-OwnedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-PathUnder -Path $Path -Root $buildRoot -Label "Clean target"
    if ((Get-FullPath $Path) -eq (Get-FullPath $buildRoot)) {
        throw "Refusing to remove the build root itself: $Path"
    }

    if (Test-Path -LiteralPath $Path) {
        Write-Host "Removing $Path" -ForegroundColor Yellow
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Add-ResultRow {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][TimeSpan]$Elapsed
    )

    $script:Rows.Add([pscustomobject]@{
        name = $Name
        command = $Command
        cwd = $WorkingDirectory
        exitCode = $ExitCode
        logPath = $LogPath
        elapsedSeconds = [Math]::Round($Elapsed.TotalSeconds, 3)
    }) | Out-Null
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Require-Path -Path $WorkingDirectory -Label "$Name working directory"
    $logPath = Join-Path -Path $reportRoot -ChildPath (($Name -replace "[^A-Za-z0-9_.-]", "_") + ".log")
    $commandText = ($Executable + " " + (($Arguments | ForEach-Object {
        if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
    }) -join " "))

    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    Write-Host "cwd: $WorkingDirectory"
    Write-Host "cmd: $commandText"
    Write-Host "log: $logPath"

    if ($DryRun) {
        Add-ResultRow -Name $Name -Command $commandText -WorkingDirectory $WorkingDirectory -ExitCode 0 -LogPath $logPath -Elapsed ([TimeSpan]::Zero)
        return
    }

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    Push-Location $WorkingDirectory
    $logWriter = $null
    $stdoutPath = $null
    $stderrPath = $null
    try {
        $logWriter = [System.IO.StreamWriter]::new($logPath, $false, [System.Text.UTF8Encoding]::new($false))
        $logWriter.WriteLine("==> $Name")
        $logWriter.WriteLine("cwd: $WorkingDirectory")
        $logWriter.WriteLine("cmd: $commandText")
        $logWriter.WriteLine("")

        $positions = @{
            stdout = [int64]0
            stderr = [int64]0
            lastOutput = Get-Date
            lastHeartbeat = Get-Date
        }

        function Write-CommandOutput {
            param(
                [Parameter(Mandatory = $true)][string]$Path,
                [Parameter(Mandatory = $true)][string]$Key,
                [Parameter(Mandatory = $true)][bool]$IsError
            )

            if (-not (Test-Path -LiteralPath $Path)) {
                return
            }

            $stream = $null
            $reader = $null
            try {
                $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                [void]$stream.Seek($positions[$Key], [System.IO.SeekOrigin]::Begin)
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8, $true)
                $text = $reader.ReadToEnd()
                $positions[$Key] = $stream.Position
            }
            finally {
                if ($null -ne $reader) {
                    $reader.Dispose()
                }
                elseif ($null -ne $stream) {
                    $stream.Dispose()
                }
            }

            if ([string]::IsNullOrEmpty($text)) {
                return
            }

            $positions["lastOutput"] = Get-Date
            foreach ($line in ($text -split "\r?\n")) {
                if ($line.Length -eq 0) {
                    continue
                }
                $logWriter.WriteLine($line)
                $logWriter.Flush()
                if ($IsError) {
                    if ($line -match "ERRORFailed to load RTI Connext DDS Micro" -or
                        $line -match "^Link libraries:" -or
                        $line -match "^--- stderr:") {
                        Write-Host $line -ForegroundColor DarkYellow
                    }
                    else {
                        Write-Host $line -ForegroundColor Yellow
                    }
                }
                else {
                    Write-Host $line
                }
            }
        }

        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $Executable
        $startInfo.Arguments = (($Arguments | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
        }) -join " ")
        $startInfo.WorkingDirectory = $WorkingDirectory
        $startInfo.UseShellExecute = $false
        $stdoutPath = Join-Path -Path $reportRoot -ChildPath (($Name -replace "[^A-Za-z0-9_.-]", "_") + ".stdout.tmp")
        $stderrPath = Join-Path -Path $reportRoot -ChildPath (($Name -replace "[^A-Za-z0-9_.-]", "_") + ".stderr.tmp")
        $startInfo.RedirectStandardOutput = $false
        $startInfo.RedirectStandardError = $false
        $startInfo.RedirectStandardInput = $false

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        $commandForCmd = '"' + ($Executable -replace '"', '\"') + '" ' + $startInfo.Arguments +
            ' > "' + $stdoutPath + '" 2> "' + $stderrPath + '"'
        $startInfo.FileName = "$env:ComSpec"
        $startInfo.Arguments = "/d /s /c `"$commandForCmd`""
        [void]$process.Start()
        while (-not $process.HasExited) {
            Write-CommandOutput -Path $stdoutPath -Key "stdout" -IsError $false
            Write-CommandOutput -Path $stderrPath -Key "stderr" -IsError $true

            $now = Get-Date
            if (($now - $positions["lastOutput"]).TotalSeconds -ge 15 -and
                ($now - $positions["lastHeartbeat"]).TotalSeconds -ge 15) {
                $heartbeat = "[still running] $Name elapsed=$([Math]::Round($watch.Elapsed.TotalSeconds, 1))s log=$logPath"
                $logWriter.WriteLine($heartbeat)
                $logWriter.Flush()
                Write-Host $heartbeat -ForegroundColor DarkGray
                $positions["lastHeartbeat"] = $now
            }

            Start-Sleep -Milliseconds 500
        }
        $process.WaitForExit()
        Write-CommandOutput -Path $stdoutPath -Key "stdout" -IsError $false
        Write-CommandOutput -Path $stderrPath -Key "stderr" -IsError $true
        $exitCode = $process.ExitCode

        $logWriter.WriteLine("")
        $logWriter.WriteLine("exitCode: $exitCode")
        $logWriter.WriteLine("elapsedSeconds: $([Math]::Round($watch.Elapsed.TotalSeconds, 3))")
    }
    finally {
        if ($null -ne $logWriter) {
            $logWriter.Dispose()
        }
        if ($stdoutPath -and (Test-Path -LiteralPath $stdoutPath)) {
            Remove-Item -LiteralPath $stdoutPath -Force
        }
        if ($stderrPath -and (Test-Path -LiteralPath $stderrPath)) {
            Remove-Item -LiteralPath $stderrPath -Force
        }
        Pop-Location
        $watch.Stop()
    }

    Add-ResultRow -Name $Name -Command $commandText -WorkingDirectory $WorkingDirectory -ExitCode $exitCode -LogPath $logPath -Elapsed $watch.Elapsed
    if ($exitCode -ne 0) {
        throw "$Name failed with exit code $exitCode. See $logPath"
    }
}

function Invoke-RosCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Command
    )

    $python = Resolve-RequiredCommand -Name "python" -Hint "Install Python or run from a terminal where Python is on PATH."
    $args = @($envScript, "--temp-root", $tempRoot, "--quiet", "--") + $Command
    Invoke-LoggedCommand -Name $Name -WorkingDirectory $WorkingDirectory -Executable $python -Arguments $args
}

function Invoke-AssetSanity {
    $name = "asset sanity"
    $logPath = Join-Path -Path $reportRoot -ChildPath "asset_sanity.log"
    Write-Host ""
    Write-Host "==> $name" -ForegroundColor Cyan
    Write-Host "root: $pluginRoot"
    Write-Host "log: $logPath"

    if ($DryRun) {
        Add-ResultRow -Name $name -Command "asset file sanity checks" -WorkingDirectory $workspaceRoot -ExitCode 0 -LogPath $logPath -Elapsed ([TimeSpan]::Zero)
        return
    }

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $requiredFiles = @(
        "ros2cs_common.dll",
        "ros2cs_core.dll",
        "composition_interfaces_assembly.dll",
        "lifecycle_msgs_assembly.dll",
        "statistics_msgs_assembly.dll",
        "stereo_msgs_assembly.dll",
        "Windows\x86_64\static_transform_broadcaster_node.dll",
        "Windows\x86_64\tf2.dll",
        "Windows\x86_64\tf2_ros.dll",
        "Windows\x86_64\metadata_ros2cs.xml",
        "Windows\x86_64\class_loader.dll",
        "Windows\x86_64\rcl.dll",
        "Windows\x86_64\rmw_implementation.dll",
        "Windows\x86_64\yaml.dll",
        "Windows\x86_64\yaml-cpp.dll",
        "Windows\x86_64\spdlog.dll",
        "Windows\x86_64\share\ament_index\resource_index\packages\rmw_implementation",
        "Windows\x86_64\share\ament_index\resource_index\rmw_typesupport\rmw_fastrtps_cpp",
        "StreamingAssets\Ros2ForUnity\share\ament_index\resource_index\packages\rmw_implementation",
        "StreamingAssets\Ros2ForUnity\share\ament_index\resource_index\rmw_typesupport\rmw_fastrtps_cpp"
    )
    if ($RosDistro -eq "lyrical") {
        $requiredFiles += @(
            "type_description_interfaces_assembly.dll",
            "Windows\x86_64\fmt.dll",
            "Windows\x86_64\fastdds-3.6.dll",
            "Windows\x86_64\libssl-3-x64.dll",
            "Windows\x86_64\libcrypto-3-x64.dll",
            "Windows\x86_64\rcl_logging_implementation.dll",
            "Windows\x86_64\rosidl_buffer_backend_registry.dll",
            "Windows\x86_64\rosidl_dynamic_typesupport_fastrtps.dll",
            "Windows\x86_64\share\ament_index\resource_index\packages\rosidl_buffer_backend",
            "Windows\x86_64\share\ament_index\resource_index\packages\rosidl_dynamic_typesupport_fastrtps",
            "StreamingAssets\Ros2ForUnity\share\ament_index\resource_index\packages\rosidl_buffer_backend",
            "StreamingAssets\Ros2ForUnity\share\ament_index\resource_index\packages\rosidl_dynamic_typesupport_fastrtps"
        )
    }
    elseif ($RosDistro -eq "humble") {
        $requiredFiles += @(
            "actionlib_msgs_assembly.dll",
            "Windows\x86_64\fastrtps-2.6.dll",
            "Windows\x86_64\libssl-1_1-x64.dll",
            "Windows\x86_64\libcrypto-1_1-x64.dll",
            "Windows\x86_64\rcl_logging_spdlog.dll"
        )
    }
    else {
        $requiredFiles += @(
            "type_description_interfaces_assembly.dll",
            "Windows\x86_64\fmt.dll",
            "Windows\x86_64\fastrtps-2.14.dll",
            "Windows\x86_64\libssl-3-x64.dll",
            "Windows\x86_64\libcrypto-3-x64.dll",
            "Windows\x86_64\rcl_logging_spdlog.dll"
        )
    }

    $lines = New-Object System.Collections.Generic.List[string]
    $missing = New-Object System.Collections.Generic.List[string]
    $assetInstallRoot = Split-Path -Parent $assetRoot
    foreach ($relativePath in $requiredFiles) {
        $checkRoot = if ($relativePath.StartsWith("StreamingAssets\")) { $assetInstallRoot } else { $pluginRoot }
        $candidate = Join-Path -Path $checkRoot -ChildPath $relativePath
        $exists = Test-Path -LiteralPath $candidate
        $lines.Add(("{0}: {1}" -f $relativePath, $exists)) | Out-Null
        if (-not $exists) {
            $missing.Add($relativePath) | Out-Null
        }
    }

    $fileCount = if (Test-Path -LiteralPath $assetRoot) {
        (Get-ChildItem -LiteralPath $assetRoot -Recurse -File | Measure-Object).Count
    } else {
        0
    }
    $lines.Add("assetFileCount: $fileCount") | Out-Null
    $lines | Set-Content -LiteralPath $logPath -Encoding UTF8
    $watch.Stop()

    $exitCode = if ($missing.Count -eq 0) { 0 } else { 1 }
    Add-ResultRow -Name $name -Command "asset file sanity checks" -WorkingDirectory $workspaceRoot -ExitCode $exitCode -LogPath $logPath -Elapsed $watch.Elapsed
    if ($missing.Count -gt 0) {
        throw "Asset sanity failed. Missing: $($missing -join ', '). See $logPath"
    }
}

foreach ($pathInfo in @(
    @{ Path = $buildRoot; Label = "build root" },
    @{ Path = $buildBase; Label = "build base" },
    @{ Path = $logBase; Label = "log base" },
    @{ Path = $testLogBase; Label = "test log base" },
    @{ Path = $tempRoot; Label = "temp root" },
    @{ Path = $reportRoot; Label = "report root" }
)) {
    Assert-PathUnder -Path $pathInfo.Path -Root $workspaceRoot -Label $pathInfo.Label
}

Require-Path -Path $r2fuRoot -Label "ros2-for-unity repository"
Require-Path -Path $ros2csRoot -Label "ros2cs repository"
Require-Path -Path $envScript -Label "$RosDistro environment wrapper"
Require-Path -Path $r2fuBuildScript -Label "Ros2ForUnity build script"

New-Item -ItemType Directory -Force -Path $buildRoot, $reportRoot, $tempRoot | Out-Null

if ($Clean) {
    Remove-OwnedPath -Path $buildBase
    Remove-OwnedPath -Path $logBase
    Remove-OwnedPath -Path $testLogBase
    Remove-OwnedPath -Path $tempRoot
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
}

$env:R2FU_ROS2CS_BUILD_BASE = $buildBase
$env:R2FU_ROS2CS_LOG_BASE = $logBase
$env:R2FU_ROS2CS_INSTALL_BASE = $ros2csInstall
$env:ROS2CS_PARALLEL_WORKERS = [string]$ParallelWorkers

try {
        Invoke-LoggedCommand `
        -Name "$RosDistro environment check" `
        -WorkingDirectory $workspaceRoot `
        -Executable (Resolve-RequiredCommand -Name "python" -Hint "Install Python or run from a terminal where Python is on PATH.") `
        -Arguments @($envScript, "--temp-root", $tempRoot, "--check")

    if (-not $SkipBuild) {
        Invoke-RosCommand `
            -Name "ros2cs dependency import" `
            -WorkingDirectory $ros2csRoot `
            -Command @(
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path -Path $ros2csRoot -ChildPath "get_repos.ps1")
            )

        $buildArgs = @(
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $r2fuBuildScript,
            "-standalone",
            "-with_tests"
        )
        if ($Clean) {
            $buildArgs += "-clean_install"
        }
        if ($QuietBuild -and -not $ConsoleDirect) {
            $buildArgs += "-quiet"
        }
        if ($ConsoleDirect) {
            $buildArgs += "-console_direct"
        }
        Invoke-RosCommand -Name "r2fu standalone build" -WorkingDirectory $r2fuRoot -Command $buildArgs
    }

    if (-not $SkipTests) {
        Invoke-RosCommand `
            -Name "ros2cs_tests" `
            -WorkingDirectory $ros2csRoot `
            -Command @(
                "colcon",
                "--log-base", $testLogBase,
                "test",
                "--build-base", $buildBase,
                "--install-base", $ros2csInstall,
                "--merge-install",
                "--packages-select", "ros2cs_tests"
            )

        Invoke-RosCommand `
            -Name "ros2cs test-result" `
            -WorkingDirectory $ros2csRoot `
            -Command @(
                "colcon",
                "--log-base", $testLogBase,
                "test-result",
                "--test-result-base", $buildBase,
                "--verbose",
                "--all"
            )
    }

    if (-not $SkipAssetSanity) {
        Invoke-AssetSanity
    }
}
finally {
    $summary = [pscustomobject]@{
        runId = $runId
        dryRun = [bool]$DryRun
        rosDistro = $RosDistro
        workspaceRoot = $workspaceRoot
        buildRoot = $buildRoot
        ros2csBuildBase = $buildBase
        ros2csInstallBase = $ros2csInstall
        ros2csLogBase = $logBase
        testLogBase = $testLogBase
        reportRoot = $reportRoot
        parallelWorkers = $ParallelWorkers
        rows = $script:Rows
    }
    $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host ""
    Write-Host "Validation summary written to: $summaryPath" -ForegroundColor Green
}
