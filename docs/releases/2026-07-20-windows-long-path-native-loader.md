# Windows Long-Path Native Loader Release Evidence

**Date:** 2026-07-20

**Status:** Windows standalone artifact validation complete

**Scope:** Humble, Jazzy, and Lyrical `windows_x86_64` Ros2ForUnity standalone packages rebuilt after the ros2cs Windows long-path native-loader repair.

This repository intentionally does not track generated ZIPs, manifests, build
trees, or nested source repositories. This record preserves the source and
validation identity of the generated artifacts without committing binaries.

## Source Lineage

| Component | Commit | Release relationship |
| --- | --- | --- |
| `ros2unity-release` baseline | `d04c0d22ae175d918ac91598dc866dd3e1e5fe6d` | Base commit before this evidence record. |
| `ros2cs` | `be0cfe44fb3ce38dd83e855d46581393766b3953` | Merged PR [#23](https://github.com/JianbinLiu-CFLab/ros2cs/pull/23), following the Windows long-path native-loader implementation in [#22](https://github.com/JianbinLiu-CFLab/ros2cs/pull/22). |
| `ros2-for-unity` | `1471758608cfeea41155f0fa2e9969444b67fe3b` | Merged PR [#18](https://github.com/JianbinLiu-CFLab/ros2-for-unity/pull/18); `ros2cs.repos` pins `ros2cs` to `be0cfe44fb3ce38dd83e855d46581393766b3953`. |

The source fields in all three generated manifests record clean `main`
checkouts at those exact commits.

## Artifact Integrity

| ROS 2 distro | ZIP | SHA-256 | Validation report |
| --- | --- | --- | --- |
| Humble | `artifacts/ros2-for-unity/humble/windows_x86_64/Ros2ForUnity_humble_standalone_windows_x86_64.zip` | `39939ad21192f947df2188e2559c2adc7741ae114075a53e0c4cbba8d0da4128` | `.build/reports/r2fu-humble-windows-full-validation-20260720-164339.json` |
| Jazzy | `artifacts/ros2-for-unity/jazzy/windows_x86_64/Ros2ForUnity_jazzy_standalone_windows_x86_64.zip` | `805349837691378a1ad30661a382b77bdb88eac3fb905ce45494b3adda924ed3` | `.build/reports/r2fu-jazzy-windows-full-validation-20260720-175640.json` |
| Lyrical | `artifacts/ros2-for-unity/lyrical/windows_x86_64/Ros2ForUnity_lyrical_standalone_windows_x86_64.zip` | `26ba1529e450230ca8b3a673e88c38c37da8cd2184a1ea382e397f9004f016ca` | `.build/reports/r2fu-lyrical-windows-full-validation-20260720-182815.json` |

For every row, the ZIP checksum matches its adjacent `.sha256.txt` file and
generated manifest. The manifests also confirm a non-empty
`Ros2ForUnity/Plugins/ros2cs_common.dll` entry in each package.

## Validation Performed

Each distro-specific clean rebuild completed with exit code `0` for all six
steps:

1. ROS 2 environment check.
2. ros2cs dependency import.
3. R2FU standalone build with `-clean_install`.
4. `ros2cs_tests`.
5. `colcon test-result --verbose --all`.
6. Required managed/native runtime and resource-index asset sanity checks.

The validation reports and manifests remain local generated evidence under
`.build/` and `artifacts/`; they are intentionally not Git-tracked.

## Validation Boundary

This record establishes source lineage, package integrity, standalone build,
ros2cs test-command success, and runtime-closure spot checks. It does **not**
claim a Unity Editor or Unity Player Play/Stop runtime smoke result. That
separate validation is required before making a product-runtime claim.

## Delivery Policy

- Do not commit ZIPs, manifests, or build logs to this repository.
- Publish the three ZIPs as release assets only through a separately approved
  GitHub release process.
- Keep future artifact refreshes traceable by adding a similarly scoped evidence
  record with the exact source commits and checksums.
