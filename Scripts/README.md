# Workspace Scripts

Run commands from the workspace root. The common path is:

```powershell
python .\Scripts\rebuild\rebuild_r2fu_jazzy_windows_zip.py --parallel-workers 8
```

## Directory Map

```text
Scripts\
  rebuild\   user-facing rebuild commands plus the Windows validation ladder
  package\   zip packaging implementation and unit tests
  smoke\     Unity Player and WSL2 smoke validation helpers
  env\       ROS 2 Humble/Jazzy/Lyrical environment launchers
  firewall\  Unity firewall rule helper
  release\   version bump helper
```

## Rebuild Entry Points

Use the distro-specific wrappers for normal work:

```powershell
python .\Scripts\rebuild\rebuild_r2fu_humble_windows_zip.py --parallel-workers 8
python .\Scripts\rebuild\rebuild_r2fu_jazzy_windows_zip.py --parallel-workers 8
python .\Scripts\rebuild\rebuild_r2fu_lyrical_windows_zip.py --parallel-workers 8
```

These wrappers call the shared implementation:

```text
Scripts\rebuild\rebuild_r2fu_windows_zip.py
```

`rebuild_r2fu_windows_zip.py` is the generic rebuild-and-package orchestrator.
It parses common options such as `--clean`, `--dry-run`, `--parallel-workers`,
and `--ros-distro`; then it runs the validation ladder and packages the staged
asset into a zip, `.sha256.txt`, and `.manifest.json`.

You can call the generic implementation directly when scripting:

```powershell
python .\Scripts\rebuild\rebuild_r2fu_windows_zip.py --ros-distro humble --parallel-workers 8
python .\Scripts\rebuild\rebuild_r2fu_windows_zip.py --ros-distro lyrical --parallel-workers 8
```

## Release Gate

Pass `--release-tag` only for a publishable artifact, after both source
repositories have been merged and tagged. The gate fails before a build unless
both checked-out `HEAD`s are exactly that tag, both worktrees are clean, and
`ros2-for-unity\ros2cs.repos` pins the same `ros2cs` commit. After the normal
validation ladder, it also requires all four packaged metadata files to agree
on the selected ROS distro, their source SHA, and the release tag. The passed
identity is recorded in the output manifest.

```powershell
python .\Scripts\rebuild\rebuild_r2fu_humble_windows_zip.py --clean --release-tag v0.8.0 --parallel-workers 8
python .\Scripts\rebuild\rebuild_r2fu_jazzy_windows_zip.py --clean --release-tag v0.8.0 --parallel-workers 8
python .\Scripts\rebuild\rebuild_r2fu_lyrical_windows_zip.py --clean --release-tag v0.8.0 --parallel-workers 8
```

Without `--release-tag`, the same entry points remain available for ordinary
development snapshots and do not require a release tag.

## Release Publishing

Use the publisher only after all three `--release-tag` rebuilds have passed and
the R2FU tag already exists on GitHub. It rejects any ZIP whose sidecar,
manifest provenance, or packaged metadata is inconsistent, and rejects a mix
of artifacts built from different `ros2cs` or R2FU commits. It uploads the
three ZIPs plus their matching SHA256 and manifest files as one release.

```powershell
python .\Scripts\release\publish_r2fu_windows_release.py --release-tag v0.8.0 --dry-run
python .\Scripts\release\publish_r2fu_windows_release.py --release-tag v0.8.0
```

The publisher refuses an existing release rather than overwriting assets.

## Validation Ladder

The rebuild orchestrator calls:

```text
Scripts\rebuild\run_r2fu_windows_validation.ps1
```

That PowerShell script does not create a zip. It only runs the Windows validation
ladder:

- checks the selected ROS 2 Windows environment
- rebuilds the Ros2ForUnity standalone asset
- runs `ros2cs_tests`
- collects `colcon test-result`
- runs managed/native asset sanity checks
- writes a validation summary JSON under `.build\reports`

The zip step happens after validation, inside `rebuild_r2fu_windows_zip.py`, via:

```text
Scripts\package\package_r2fu_windows_zip.py
```

## Outputs

Windows outputs are written to:

```text
.\artifacts\ros2-for-unity\humble\windows_x86_64
.\artifacts\ros2-for-unity\jazzy\windows_x86_64
.\artifacts\ros2-for-unity\lyrical\windows_x86_64
```

Useful rebuild options:

```powershell
python .\Scripts\rebuild\rebuild_r2fu_jazzy_windows_zip.py --clean --parallel-workers 8
python .\Scripts\rebuild\rebuild_r2fu_jazzy_windows_zip.py --dry-run
```

`--clean` removes the script-owned short build/log/temp roots before rebuilding.
`--dry-run` verifies the command ladder without rebuilding or packaging.

## Jazzy Ubuntu WSL2 Zip

The WSL2 Linux asset builder is separate from the Windows rebuild ladder:

```powershell
python .\Scripts\rebuild\rebuild_r2fu_jazzy_ubuntu_wsl2_zip.py --parallel-workers 8
```

Outputs are written to:

```text
.\artifacts\ros2-for-unity\jazzy\ubuntu_wsl2_x86_64
```

Keep `ubuntu_wsl2_x86_64` in the artifact path/name for this script. It means
the Linux asset was built and closure-checked in WSL2; it does not claim native
Ubuntu runtime networking, DDS discovery, Foxglove connectivity, or product
readiness.

## Unity Player Smoke

After rebuilding a zip, run a Unity Player smoke:

```powershell
python .\Scripts\smoke\smoke_r2fu_windows_player.py --timeout-seconds 120
```

Useful options:

```powershell
python .\Scripts\smoke\smoke_r2fu_windows_player.py --ros-distro lyrical --timeout-seconds 180
python .\Scripts\smoke\smoke_r2fu_windows_player.py --artifact-zip .\artifacts\ros2-for-unity\jazzy\windows_x86_64\Ros2ForUnity_jazzy_standalone_windows_x86_64.zip
```

The Player is launched without `ROS_DISTRO`, `AMENT_PREFIX_PATH`,
`COLCON_PREFIX_PATH`, or `PYTHONPATH`. The external echo command is the only
part run through the selected ROS environment wrapper.

## Firewall Helper

For Unity Editor ROS 2 / DDS smoke tests:

```powershell
powershell -ExecutionPolicy Bypass -File .\Scripts\firewall\Set-UnityFirewallRules.ps1 -Mode Install
powershell -ExecutionPolicy Bypass -File .\Scripts\firewall\Set-UnityFirewallRules.ps1 -Mode Status
powershell -ExecutionPolicy Bypass -File .\Scripts\firewall\Set-UnityFirewallRules.ps1 -Mode Remove
```

`Install` and `Remove` auto-restart elevated when administrator access is
required. `Status` can run without elevation.

## Version Bump Helper

Preview release-reference edits:

```powershell
python .\Scripts\release\bump_ros2unity_versions.py --ros2cs-version v0.7.0 --r2fu-version v0.7.0 --dry-run
```

Apply them after the preview looks right:

```powershell
python .\Scripts\release\bump_ros2unity_versions.py --ros2cs-version v0.7.0 --r2fu-version v0.7.0
```

The script updates `ros2cs` release references, `ros2-for-unity` release
references, and the `ros2cs.repos` pin. It does not commit, tag, upload
artifacts, or create GitHub releases.
