import importlib.util
import json
import pathlib
import tempfile
import unittest
import zipfile


SCRIPT_PATH = pathlib.Path(__file__).with_name("package_r2fu_windows_zip.py")


def write_required_asset_files(asset_dir: pathlib.Path):
    plugin_dir = asset_dir / "Plugins"
    native_dir = plugin_dir / "Windows" / "x86_64"
    native_dir.mkdir(parents=True)
    (asset_dir / "Scripts").mkdir(parents=True)
    for relative in [
        "Plugins/ros2cs_common.dll",
        "Plugins/ros2cs_core.dll",
        "Plugins/Windows/x86_64/rcl.dll",
        "Plugins/Windows/x86_64/rcutils.dll",
        "Plugins/Windows/x86_64/rmw_implementation.dll",
        "Plugins/Windows/x86_64/yaml.dll",
        "Plugins/Windows/x86_64/yaml-cpp.dll",
        "Plugins/Windows/x86_64/spdlog.dll",
        "Plugins/Windows/x86_64/fmt.dll",
        "Plugins/Windows/x86_64/libssl-3-x64.dll",
        "Plugins/Windows/x86_64/libcrypto-3-x64.dll",
    ]:
        (asset_dir / pathlib.PurePosixPath(relative)).write_text(relative, encoding="utf-8")
    (asset_dir / "metadata_ros2cs.xml").write_text("<ros2cs />", encoding="utf-8")
    (asset_dir / "Scripts" / "ROS2ForUnity.cs").write_text("script", encoding="utf-8")


def load_module():
    spec = importlib.util.spec_from_file_location("package_r2fu_windows_zip", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageR2FUWindowsArtifactZipTest(unittest.TestCase):
    def test_default_paths_are_workspace_relative(self):
        module = load_module()
        workspace_root = SCRIPT_PATH.resolve().parents[2]

        self.assertEqual(
            module.default_asset_dir(),
            workspace_root / "third-party" / "ros2-for-unity" / "install" / "asset" / "Ros2ForUnity",
        )
        self.assertEqual(
            module.default_output_dir(),
            workspace_root / "artifacts" / "ros2-for-unity" / "jazzy" / "windows_x86_64",
        )

    def test_package_asset_writes_zip_sha256_and_manifest(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            asset_dir = root / "Ros2ForUnity"
            write_required_asset_files(asset_dir)
            validation_summary = root / "validation-summary.json"
            validation_summary.write_text("{}", encoding="utf-8")

            output_dir = root / "out"
            result = module.package_asset(
                asset_dir=asset_dir,
                output_dir=output_dir,
                check_required=False,
                validation_summary_path=validation_summary,
            )

            self.assertTrue(result.zip_path.exists())
            self.assertTrue(result.sha256_path.exists())
            self.assertTrue(result.manifest_path.exists())

            with zipfile.ZipFile(result.zip_path) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    sorted([
                        "Ros2ForUnity/Plugins/Windows/x86_64/fmt.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/libcrypto-3-x64.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/libssl-3-x64.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/rcl.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/rcutils.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/rmw_implementation.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/spdlog.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/yaml-cpp.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/yaml.dll",
                        "Ros2ForUnity/Plugins/ros2cs_common.dll",
                        "Ros2ForUnity/Plugins/ros2cs_core.dll",
                        "Ros2ForUnity/Scripts/ROS2ForUnity.cs",
                        "Ros2ForUnity/metadata_ros2cs.xml",
                    ]),
                )

            sha_text = result.sha256_path.read_text(encoding="utf-8").strip()
            self.assertIn(result.zip_path.name, sha_text)
            self.assertIn(result.sha256, sha_text)

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifactName"], result.zip_path.name)
            self.assertEqual(manifest["assetFileCount"], 13)
            self.assertEqual(manifest["zipEntryCount"], 13)
            self.assertEqual(manifest["managedPluginFileCount"], 2)
            self.assertEqual(manifest["nativePluginFileCount"], 9)
            self.assertEqual(manifest["resourceIndexFileCount"], 0)
            self.assertEqual(manifest["metadataFileCount"], 1)
            self.assertEqual(manifest["sha256"], result.sha256)
            self.assertIn("commit", manifest["ros2_for_unity"])
            self.assertIn("dirty", manifest["ros2_for_unity"])
            self.assertIn("commit", manifest["ros2cs"])
            self.assertIn("dirty", manifest["ros2cs"])
            self.assertEqual(manifest["validation"]["summaryPath"], str(validation_summary))

    def test_required_check_reports_missing_closure_files(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            asset_dir = root / "Ros2ForUnity"
            (asset_dir / "Plugins").mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "ros2cs_common.dll"):
                module.package_asset(
                    asset_dir=asset_dir,
                    output_dir=root / "out",
                    check_required=True,
                    backup_existing=False,
                )


if __name__ == "__main__":
    unittest.main()
