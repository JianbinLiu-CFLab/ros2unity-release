# ros2-windows

Local entry points for Windows ROS 2 distributions used by this release
workspace.

This directory should contain local directory junctions or symlinks, not the ROS
2 distributions themselves:

```text
ros2_humble  -> C:\ros2_humble\ros2-windows
ros2_jazzy   -> C:\ros2_jazzy\ros2-windows
ros2_lyrical -> C:\ros2_lyrical\ros2-windows
```

The junction targets are ignored by git. They are machine-local installs used by
the scripts in `Scripts/env/` and the higher-level release commands in
`Scripts/`.

## Official Windows Archives

Use stable GitHub release URLs in documentation and scripts. Browser download
links from `release-assets.githubusercontent.com` are temporary signed URLs and
should not be checked in.

### Humble

For ROS 2 Humble Hawksbill Patch Release 14, the official ROS 2 release tag is:

```text
release-humble-20260220
```

The Windows binary archive follows the official ROS 2 Windows release install
pattern:

```text
https://github.com/ros2/ros2/releases/download/release-humble-20260220/ros2-humble-20260220-windows-release-amd64.zip
```

Install or extract it outside this repository, for example under:

```text
C:\ros2_humble\ros2-windows
```

Then create or refresh the local junction:

```powershell
New-Item -ItemType Junction `
  -Path .\ros2-windows\ros2_humble `
  -Target C:\ros2_humble\ros2-windows
```

### Jazzy

For the tested ROS 2 Jazzy Jalisco Windows archive, use Patch Release 7:

```text
release-jazzy-20260128
```

Stable download URL:

```text
https://github.com/ros2/ros2/releases/download/release-jazzy-20260128/ros2-jazzy-20260128-windows-release-amd64.zip
```

Install or extract it outside this repository, for example under:

```text
C:\ros2_jazzy\ros2-windows
```

Then create or refresh the local junction:

```powershell
New-Item -ItemType Junction `
  -Path .\ros2-windows\ros2_jazzy `
  -Target C:\ros2_jazzy\ros2-windows
```

### Lyrical

For the tested ROS 2 Lyrical Windows archive, use the 2026-05-22 release:

```text
release-lyrical-20260522
```

Stable download URL:

```text
https://github.com/ros2/ros2/releases/download/release-lyrical-20260522/ros2-lyrical-2026-05-22-windows-AMD64.zip
```

Install or extract it outside this repository, for example under:

```text
C:\ros2_lyrical\ros2-windows
```

Then create or refresh the local junction:

```powershell
New-Item -ItemType Junction `
  -Path .\ros2-windows\ros2_lyrical `
  -Target C:\ros2_lyrical\ros2-windows
```
