# ros2unity-release

Reproducible release workspace for the JianbinLiu-CFLab `ros2cs` and
`ros2-for-unity` forks.

This repository owns the release orchestration layer: workspace scripts, ROS 2
environment launchers, artifact packaging, smoke validation, and evidence notes.
The source repositories remain independent nested checkouts and are intentionally
ignored by this repository.

## Workspace Layout

```text
<workspace>
  Scripts\          release/build/validation orchestration
    rebuild\        one-command rebuild entrypoints and validation ladder
    env\            ROS 2 environment launchers
    package\        zip packaging implementation and tests
    smoke\          Unity Player and WSL2 smoke helpers
  third-party\      optional local research/vendor checkouts
    ros2cs\         independent git checkout (ignored)
    ros2-for-unity\ independent git checkout (ignored)
  artifacts\        generated zip outputs (ignored)
  .build\           build/test scratch data (ignored)
  ros2-windows\     local ROS 2 root links (ignored)
```

## Current Policy

- Do not track generated artifacts, build outputs, or nested repository contents.
- Keep `ros2cs` and `ros2-for-unity` commits, PRs, and releases in their own repos.
- Use this repo to make the release process reproducible for Jazzy/Lyrical
  Windows artifacts and Ubuntu WSL2 packaging experiments.
