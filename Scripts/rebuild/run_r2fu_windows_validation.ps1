<#
Copyright (c) 2026 Jianbin Liu-CFLab.

Modifications by Jianbin Liu:
- Disabled shared compiler and MSBuild servers so clean distro rebuilds do not retain locks under the script-owned temporary root.
- Added a ros2cs overlay closure gate before ros2cs test execution.
- Routes child-process temporary output to the script-owned workspace root.
- Added isolated source and run-root parameters for parallel release matrix execution.
- Treats ParallelWorkers as the bounded native-job limit for one isolated R2FU child.
- Added owned subst-drive mappings so MSVC receives short logical paths while artifacts remain at long physical paths.

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

.PARAMETER ParallelWorkers
Maximum native Ninja jobs for this R2FU child. R2FU serializes colcon package
scheduling so this value remains a real per-child limit.

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
    [int]$ParallelWorkers = [System.Environment]::ProcessorCount,
    [string]$R2fuRoot,
    [string]$Ros2csRoot,
    [string]$RunRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path -Path $scriptDir -ChildPath "..\.."))
$workspaceBuildRoot = Join-Path -Path $workspaceRoot -ChildPath ".build"
$hasExplicitRunRoot = -not [string]::IsNullOrWhiteSpace($RunRoot)
if ($hasExplicitRunRoot) {
    $workspaceBuildRootFull = [System.IO.Path]::GetFullPath($workspaceBuildRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $buildRoot = [System.IO.Path]::GetFullPath($RunRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $workspaceBuildPrefix = $workspaceBuildRootFull + [System.IO.Path]::DirectorySeparatorChar
    if (-not $buildRoot.StartsWith($workspaceBuildPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "RunRoot must stay below '$workspaceBuildRootFull'. Got '$buildRoot'."
    }

    # A release matrix invocation owns every mutable build product below this run root.
    $buildBase = Join-Path -Path $buildRoot -ChildPath "build"
    $logBase = Join-Path -Path $buildRoot -ChildPath "log"
    $testLogBase = Join-Path -Path $buildRoot -ChildPath "test-log"
    $tempRoot = Join-Path -Path $buildRoot -ChildPath "tmp"
    $reportRoot = Join-Path -Path $buildRoot -ChildPath "reports"
} else {
    $buildRoot = $workspaceBuildRoot
    $pathSuffix = if ($RosDistro -eq "jazzy") { "" } else { "_$RosDistro" }
    $buildBase = Join-Path -Path $buildRoot -ChildPath "b$pathSuffix"
    $logBase = Join-Path -Path $buildRoot -ChildPath "l$pathSuffix"
    $testLogBase = Join-Path -Path $buildRoot -ChildPath "tl$pathSuffix"
    $tempRoot = Join-Path -Path $buildRoot -ChildPath "tmp"
    $reportRoot = Join-Path -Path $buildRoot -ChildPath "reports"
}
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$summaryPath = Join-Path -Path $reportRoot -ChildPath "r2fu-$RosDistro-windows-full-validation-$runId.json"

# Keep compiler-server state inside this invocation so -Clean can remove the temporary root on the next distro build.
$env:UseSharedCompilation = "false"
$env:DOTNET_CLI_DO_NOT_USE_MSBUILD_SERVER = "1"
$env:MSBUILDDISABLENODEREUSE = "1"

function Resolve-SourceRepo {
    param([Parameter(Mandatory = $true)][string]$Name)

    $thirdPartyPath = Join-Path -Path $workspaceRoot -ChildPath "third-party\$Name"
    if (Test-Path -LiteralPath $thirdPartyPath) {
        return $thirdPartyPath
    }
    return (Join-Path -Path $workspaceRoot -ChildPath $Name)
}

$r2fuRoot = if ([string]::IsNullOrWhiteSpace($R2fuRoot)) {
    Resolve-SourceRepo "ros2-for-unity"
} else {
    [System.IO.Path]::GetFullPath($R2fuRoot)
}
$ros2csRoot = if ([string]::IsNullOrWhiteSpace($Ros2csRoot)) {
    Resolve-SourceRepo "ros2cs"
} else {
    [System.IO.Path]::GetFullPath($Ros2csRoot)
}
$envScriptByDistro = @{
    humble = "Enter-Ros2HumbleEnv.py"
    jazzy = "Enter-Ros2JazzyEnv.py"
    lyrical = "Enter-Ros2LyricalEnv.py"
}
$envScriptName = $envScriptByDistro[$RosDistro]
$envScript = Join-Path -Path $workspaceRoot -ChildPath "Scripts\env\$envScriptName"
$r2fuBuildScript = Join-Path -Path $r2fuRoot -ChildPath "build.ps1"
$ros2csInstall = if ($hasExplicitRunRoot) {
    Join-Path -Path $buildRoot -ChildPath "install"
} else {
    Join-Path -Path $ros2csRoot -ChildPath "install-$RosDistro"
}
$ros2Root = Join-Path -Path $workspaceRoot -ChildPath "ros2-windows\ros2_$RosDistro"
$overlayValidationScript = Join-Path -Path $workspaceRoot -ChildPath "Scripts\rebuild\validate_ros2cs_overlay.py"
$nativePluginCompileSurfaceScript = Join-Path -Path $workspaceRoot -ChildPath "Scripts\rebuild\verify_r2fu_native_plugin_bootstrap.py"
$assetRoot = Join-Path -Path $r2fuRoot -ChildPath "install\asset\Ros2ForUnity"
$pluginRoot = Join-Path -Path $assetRoot -ChildPath "Plugins"

$script:Rows = New-Object System.Collections.Generic.List[object]
$script:SubstMappings = New-Object System.Collections.Generic.List[object]
$script:SubstExecutable = $null
$script:SubstDriveCandidates = @("R", "S", "T", "U", "V", "W", "X", "Y", "Z", "Q", "P", "O", "N", "M", "L", "K", "J", "I", "H", "G", "F")
$script:PathMapping = [ordered]@{
    enabled = $false
    mode = "subst"
    drives = [ordered]@{}
    physicalRunRoot = $buildRoot
    physicalR2fuRoot = $r2fuRoot
    physicalRos2csRoot = $ros2csRoot
    physicalRos2csBuildBase = $buildBase
    physicalRos2csInstallBase = $ros2csInstall
    mappedR2fuRoot = $null
    mappedRos2csRoot = $null
    mappedRos2csBuildBase = $null
    mappedRos2csLogBase = $null
    mappedRos2csInstallBase = $null
    mappedTempRoot = $null
}

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
    $args = @($envScript, "--temp-root", $mappedTempRoot, "--quiet", "--") + $Command
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

function Invoke-Ros2csOverlayClosure {
    Invoke-RosCommand `
        -Name "ros2cs overlay closure" `
        -WorkingDirectory $workspaceRoot `
        -Command @(
            "python",
            $overlayValidationScript,
            "--install-base", $mappedRos2csInstall,
            "--ros2-root", $ros2Root
        )
}

# Remove only a verified non-reparse directory below the explicitly owned physical root.
function Remove-ScopedOwnedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-PathUnder -Path $Path -Root $Root -Label $Label
    if ((Get-FullPath $Path) -eq (Get-FullPath $Root)) {
        throw "Refusing to remove the $Label root itself: $Path"
    }

    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing recursive cleanup through a reparse point for ${Label}: $Path"
        }
        Write-Host "Removing $Path" -ForegroundColor Yellow
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

# Create one short logical path while preserving the caller's physical target path.
# Return one stable full path for ownership checks without dereferencing a subst drive.
function Get-ComparablePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = Get-FullPath $Path
    if ($fullPath.Length -gt 3) {
        return $fullPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    }
    return $fullPath
}

# Resolve subst.exe only when this wrapper needs a logical Windows drive alias.
function Get-SubstExecutable {
    if ([string]::IsNullOrWhiteSpace($script:SubstExecutable)) {
        $script:SubstExecutable = Resolve-RequiredCommand -Name "subst.exe" -Hint "Windows subst.exe is required for transparent long-path builds."
    }
    return $script:SubstExecutable
}

# Return the target of one active subst mapping, or null when the drive is not subst-managed.
function Get-SubstDriveTarget {
    param([Parameter(Mandatory = $true)][string]$Drive)

    $normalizedDrive = $Drive.Trim().TrimEnd(':').ToUpperInvariant() + ":"
    $substExecutable = Get-SubstExecutable
    $lines = @(& $substExecutable 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "subst.exe could not list active mappings (exit code $LASTEXITCODE)."
    }

    $prefixPattern = [regex]::Escape($normalizedDrive + "\") + ":"
    foreach ($line in $lines) {
        $match = [regex]::Match([string]$line, "^\s*$prefixPattern\s*=>\s*(.+?)\s*$", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($match.Success) {
            return (Get-ComparablePath $match.Groups[1].Value)
        }
    }
    return $null
}

# Map a presently unused drive letter to an existing physical path and register only mappings created here.
function New-OwnedSubstDrive {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $fullTarget = Get-ComparablePath $Target
    if (-not (Test-Path -LiteralPath $fullTarget)) {
        throw "$Label subst target does not exist: $fullTarget"
    }

    $substExecutable = Get-SubstExecutable
    $attempts = New-Object System.Collections.Generic.List[string]
    foreach ($letter in $script:SubstDriveCandidates) {
        $drive = "$letter`:"
        if ($null -ne (Get-SubstDriveTarget -Drive $drive) -or (Test-Path -LiteralPath "$drive\")) {
            continue
        }

        $output = @(& $substExecutable $drive $fullTarget 2>&1)
        if ($LASTEXITCODE -ne 0) {
            $attempts.Add("$drive exit=$LASTEXITCODE $($output -join ' ')") | Out-Null
            continue
        }

        $actualTarget = Get-SubstDriveTarget -Drive $drive
        if ($null -eq $actualTarget) {
            throw "$Label subst mapping was not visible after creation: $drive"
        }
        if ((Get-ComparablePath $actualTarget) -ne $fullTarget) {
            throw "$Label subst mapping target changed during creation: $drive => $actualTarget"
        }

        $script:SubstMappings.Add([pscustomobject]@{
            drive = $drive
            target = $fullTarget
            label = $Label
        }) | Out-Null
        return "$drive\"
    }

    throw "No unused subst drive is available for $Label. Attempts: $($attempts -join '; ')"
}

# Remove only subst mappings created by this invocation and still pointing at their registered physical targets.
function Remove-OwnedSubstDrives {
    foreach ($mapping in @($script:SubstMappings | Sort-Object drive -Descending)) {
        try {
            $actualTarget = Get-SubstDriveTarget -Drive $mapping.drive
            if ($null -eq $actualTarget) {
                continue
            }
            if ((Get-ComparablePath $actualTarget) -ne $mapping.target) {
                Write-Warning "Refusing to remove changed subst mapping '$($mapping.drive)' for '$($mapping.label)': '$actualTarget'."
                continue
            }

            $output = @(& (Get-SubstExecutable) $mapping.drive "/D" 2>&1)
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Could not remove owned subst mapping '$($mapping.drive)': $($output -join ' ')"
            }
        } catch {
            Write-Warning "Could not inspect owned subst mapping '$($mapping.drive)': $($_.Exception.Message)"
        }
    }
    $script:SubstMappings.Clear()
}

# Translate an owned physical child path through a mapped root without resolving the logical drive spelling.
function Get-MappedChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$PhysicalRoot,
        [Parameter(Mandatory = $true)][string]$MappedRoot,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-PathUnder -Path $Path -Root $PhysicalRoot -Label $Label
    $fullPath = Get-ComparablePath $Path
    $fullRoot = Get-ComparablePath $PhysicalRoot
    if ($fullPath -eq $fullRoot) {
        return $MappedRoot
    }

    $relativePath = $fullPath.Substring($fullRoot.Length).TrimStart([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    return (Join-Path -Path $MappedRoot -ChildPath $relativePath)
}

function Invoke-R2fuNativePluginCompileSurface {
    $command = @(
        $nativePluginCompileSurfaceScript,
        "--r2fu-root", $mappedR2fuRoot,
        "--scratch-root", $mappedTempRoot
    )
    $python = Resolve-RequiredCommand -Name "python" -Hint "Install Python or run from a terminal where Python is on PATH."
    Invoke-LoggedCommand -Name "r2fu native plugin compile surface" -WorkingDirectory $workspaceRoot -Executable $python -Arguments $command
}

foreach ($pathInfo in @(
    @{ Path = $buildRoot; Label = "build root" },
    @{ Path = $buildBase; Label = "build base" },
    @{ Path = $logBase; Label = "log base" },
    @{ Path = $testLogBase; Label = "test log base" },
    @{ Path = $tempRoot; Label = "temp root" },
    @{ Path = $reportRoot; Label = "report root" },
    @{ Path = $r2fuRoot; Label = "ros2-for-unity repository" },
    @{ Path = $ros2csRoot; Label = "ros2cs repository" }
)) {
    Assert-PathUnder -Path $pathInfo.Path -Root $workspaceRoot -Label $pathInfo.Label
}

Require-Path -Path $r2fuRoot -Label "ros2-for-unity repository"
Require-Path -Path $ros2csRoot -Label "ros2cs repository"
Require-Path -Path $envScript -Label "$RosDistro environment wrapper"
Require-Path -Path $r2fuBuildScript -Label "Ros2ForUnity build script"
Require-Path -Path $ros2Root -Label "$RosDistro ROS 2 root"
Require-Path -Path $overlayValidationScript -Label "ros2cs overlay validation script"
Require-Path -Path $nativePluginCompileSurfaceScript -Label "R2FU native plugin compile-surface script"

New-Item -ItemType Directory -Force -Path $buildRoot, $reportRoot, $tempRoot | Out-Null

if ($Clean) {
    Remove-OwnedPath -Path $buildBase
    Remove-OwnedPath -Path $logBase
    Remove-OwnedPath -Path $testLogBase
    Remove-OwnedPath -Path $tempRoot
    if (-not $DryRun) {
        $ros2csInstallOwner = if ($hasExplicitRunRoot) { $buildRoot } else { $ros2csRoot }
        Remove-ScopedOwnedPath -Path $ros2csInstall -Root $ros2csInstallOwner -Label "ros2cs install"
    }
}

New-Item -ItemType Directory -Force -Path $buildBase, $logBase, $testLogBase, $tempRoot | Out-Null
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $ros2csInstall | Out-Null
}

$mappedBuildRoot = $null
$mappedR2fuRoot = $null
$mappedRos2csRoot = $null
$mappedBuildBase = $null
$mappedLogBase = $null
$mappedTestLogBase = $null
$mappedRos2csInstall = $null
$mappedTempRoot = $null
$mappedR2fuBuildScript = $null

try {
    # MSVC cannot reliably consume direct extended-length source paths. Keep physical paths intact and map three short logical drive roots.
    $mappedBuildRoot = New-OwnedSubstDrive -Target $buildRoot -Label "build root"
    $mappedR2fuRoot = New-OwnedSubstDrive -Target $r2fuRoot -Label "ros2-for-unity repository"
    $mappedRos2csRoot = New-OwnedSubstDrive -Target $ros2csRoot -Label "ros2cs repository"
    $mappedBuildBase = Get-MappedChildPath -Path $buildBase -PhysicalRoot $buildRoot -MappedRoot $mappedBuildRoot -Label "ros2cs build base"
    $mappedLogBase = Get-MappedChildPath -Path $logBase -PhysicalRoot $buildRoot -MappedRoot $mappedBuildRoot -Label "ros2cs log base"
    $mappedTestLogBase = Get-MappedChildPath -Path $testLogBase -PhysicalRoot $buildRoot -MappedRoot $mappedBuildRoot -Label "ros2cs test log base"
    $mappedTempRoot = Get-MappedChildPath -Path $tempRoot -PhysicalRoot $buildRoot -MappedRoot $mappedBuildRoot -Label "temporary output"
    $mappedRos2csInstall = if ($hasExplicitRunRoot) {
        Get-MappedChildPath -Path $ros2csInstall -PhysicalRoot $buildRoot -MappedRoot $mappedBuildRoot -Label "ros2cs install base"
    } else {
        Get-MappedChildPath -Path $ros2csInstall -PhysicalRoot $ros2csRoot -MappedRoot $mappedRos2csRoot -Label "ros2cs install base"
    }
    $mappedR2fuBuildScript = Join-Path -Path $mappedR2fuRoot -ChildPath "build.ps1"
    Require-Path -Path $mappedR2fuBuildScript -Label "mapped Ros2ForUnity build script"

    $script:PathMapping = [ordered]@{
        enabled = $true
        mode = "subst"
        drives = [ordered]@{
            build = Split-Path -Qualifier $mappedBuildRoot
            r2fu = Split-Path -Qualifier $mappedR2fuRoot
            ros2cs = Split-Path -Qualifier $mappedRos2csRoot
        }
        physicalRunRoot = $buildRoot
        physicalR2fuRoot = $r2fuRoot
        physicalRos2csRoot = $ros2csRoot
        physicalRos2csBuildBase = $buildBase
        physicalRos2csInstallBase = $ros2csInstall
        mappedRunRoot = $mappedBuildRoot
        mappedR2fuRoot = $mappedR2fuRoot
        mappedRos2csRoot = $mappedRos2csRoot
        mappedRos2csBuildBase = $mappedBuildBase
        mappedRos2csLogBase = $mappedLogBase
        mappedRos2csInstallBase = $mappedRos2csInstall
        mappedTempRoot = $mappedTempRoot
    }

    $env:TEMP = $mappedTempRoot
    $env:TMP = $mappedTempRoot
    $env:R2FU_ROS2CS_ROOT = $mappedRos2csRoot
    $env:R2FU_ROS2CS_BUILD_BASE = $mappedBuildBase
    $env:R2FU_ROS2CS_LOG_BASE = $mappedLogBase
    $env:R2FU_ROS2CS_INSTALL_BASE = $mappedRos2csInstall
    # R2FU consumes this as its native Ninja job bound while colcon schedules one package at a time.
    $env:ROS2CS_PARALLEL_WORKERS = [string]$ParallelWorkers

    Invoke-LoggedCommand `
        -Name "$RosDistro environment check" `
        -WorkingDirectory $workspaceRoot `
        -Executable (Resolve-RequiredCommand -Name "python" -Hint "Install Python or run from a terminal where Python is on PATH.") `
        -Arguments @($envScript, "--temp-root", $mappedTempRoot, "--check")

    if (-not $SkipBuild) {
        Invoke-RosCommand `
            -Name "ros2cs dependency import" `
            -WorkingDirectory $mappedRos2csRoot `
            -Command @(
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path -Path $mappedRos2csRoot -ChildPath "get_repos.ps1")
            )

        $buildArgs = @(
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $mappedR2fuBuildScript,
            "-standalone",
            "-with_tests"
        )
        if ($Clean) {
            $buildArgs += @("-clean_install", "-skip_ros2cs_clean")
        }
        if ($QuietBuild -and -not $ConsoleDirect) {
            $buildArgs += "-quiet"
        }
        if ($ConsoleDirect) {
            $buildArgs += "-console_direct"
        }
        Invoke-RosCommand -Name "r2fu standalone build" -WorkingDirectory $mappedR2fuRoot -Command $buildArgs
    }

    Invoke-R2fuNativePluginCompileSurface
    Invoke-Ros2csOverlayClosure

    if (-not $SkipTests) {
        Invoke-RosCommand `
            -Name "ros2cs_tests" `
            -WorkingDirectory $mappedRos2csRoot `
            -Command @(
                "colcon",
                "--log-base", $mappedTestLogBase,
                "test",
                "--build-base", $mappedBuildBase,
                "--install-base", $mappedRos2csInstall,
                "--merge-install",
                "--packages-select", "ros2cs_tests"
            )

        Invoke-RosCommand `
            -Name "ros2cs test-result" `
            -WorkingDirectory $mappedRos2csRoot `
            -Command @(
                "colcon",
                "--log-base", $mappedTestLogBase,
                "test-result",
                "--test-result-base", $mappedBuildBase,
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
        runRoot = $buildRoot
        r2fuRoot = $r2fuRoot
        ros2csRoot = $ros2csRoot
        buildRoot = $buildRoot
        ros2csBuildBase = $buildBase
        ros2csInstallBase = $ros2csInstall
        ros2csLogBase = $logBase
        testLogBase = $testLogBase
        reportRoot = $reportRoot
        parallelWorkers = $ParallelWorkers
        pathMapping = $script:PathMapping
        rows = $script:Rows
    }
    try {
        $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
        Write-Host ""
        Write-Host "Validation summary written to: $summaryPath" -ForegroundColor Green
    }
    finally {
        Remove-OwnedSubstDrives
    }
}
