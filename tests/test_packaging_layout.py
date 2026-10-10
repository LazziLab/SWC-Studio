from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from swcstudio.core import model_paths, updater


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackagingLayoutTests(unittest.TestCase):
    def test_stage_payload_separates_code_and_models(self) -> None:
        staging = _load_script(
            "stage_modular_payload_test",
            "packaging/stage_modular_payload.py",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            runtime = root / "runtime"
            package = source / "swcstudio"
            models = package / "data" / "models"
            configs = package / "tools" / "validation" / "configs"
            models.mkdir(parents=True)
            configs.mkdir(parents=True)
            runtime.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (models / "labeler-1.0.0").mkdir()
            (models / "labeler-1.0.0" / "flag_model.joblib").write_bytes(b"model")
            (configs / "default.json").write_text("{}", encoding="utf-8")

            staging.stage_payload(source, runtime, "1.2.3")

            self.assertTrue(
                (runtime / "app/swcstudio/tools/validation/configs/default.json").is_file()
            )
            self.assertFalse(
                (runtime / "app/swcstudio/data/models/labeler-1.0.0/flag_model.joblib").exists()
            )
            self.assertEqual(
                (runtime / "models/labeler-1.0.0/flag_model.joblib").read_bytes(),
                b"model",
            )
            self.assertEqual(
                (runtime / "app/VERSION").read_text(encoding="utf-8").strip(),
                "1.2.3",
            )

    def test_bootstrap_finds_shared_runtime_layout(self) -> None:
        bootstrap = _load_script(
            "swcstudio_bootstrap_test",
            "packaging/swcstudio_bootstrap.py",
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / "app/swcstudio").mkdir(parents=True)
            (runtime / "app/swcstudio/__init__.py").write_text("", encoding="utf-8")
            (runtime / "models").mkdir()
            with mock.patch.object(bootstrap.sys, "_MEIPASS", str(runtime), create=True):
                self.assertEqual(bootstrap._bundled_app_dir(), runtime / "app")
                self.assertEqual(bootstrap._bundled_models_dir(), runtime / "models")

    def test_bootstrap_finds_macos_contents_resources_layout(self) -> None:
        bootstrap = _load_script(
            "swcstudio_bootstrap_macos_resources_test",
            "packaging/swcstudio_bootstrap.py",
        )
        with tempfile.TemporaryDirectory() as tmp:
            contents = Path(tmp) / "SWC-Studio.app" / "Contents"
            executable = contents / "MacOS" / "SWC-Studio"
            app_dir = contents / "Resources" / "app"
            models_dir = contents / "Resources" / "models"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            (app_dir / "swcstudio").mkdir(parents=True)
            (app_dir / "swcstudio" / "__init__.py").write_text("", encoding="utf-8")
            models_dir.mkdir(parents=True)
            with (
                mock.patch.object(bootstrap.sys, "frozen", True, create=True),
                mock.patch.object(bootstrap.sys, "platform", "darwin"),
                mock.patch.object(bootstrap.sys, "executable", str(executable)),
                mock.patch.object(bootstrap.sys, "_MEIPASS", None, create=True),
            ):
                self.assertEqual(bootstrap._bundled_app_dir(), app_dir.resolve())
                self.assertEqual(bootstrap._bundled_models_dir(), models_dir.resolve())

    def test_bootstrap_marks_modular_process_and_exports_models(self) -> None:
        bootstrap = _load_script(
            "swcstudio_bootstrap_main_test",
            "packaging/swcstudio_bootstrap.py",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / "app"
            models_dir = root / "models"
            app_dir.mkdir()
            models_dir.mkdir()
            fake_main = mock.Mock()
            fake_module = types.SimpleNamespace(main=fake_main)
            real_import = __import__

            def _fake_import(name, *args, **kwargs):
                if name == "swcstudio.gui.main":
                    return fake_module
                return real_import(name, *args, **kwargs)

            old_path = list(sys.path)
            try:
                with (
                    mock.patch.object(bootstrap, "_find_app_dir", return_value=app_dir),
                    mock.patch.object(
                        bootstrap,
                        "_bundled_models_dir",
                        return_value=models_dir,
                    ),
                    mock.patch("builtins.__import__", side_effect=_fake_import),
                    mock.patch.dict(os.environ, {}, clear=False),
                ):
                    self.assertEqual(bootstrap.main(), 0)
                    self.assertEqual(
                        os.environ[bootstrap.BUNDLE_FLAVOR_ENV],
                        "modular",
                    )
                    self.assertEqual(
                        os.environ[bootstrap.BUNDLED_MODELS_ENV],
                        str(models_dir),
                    )
                    fake_main.assert_called_once_with()
            finally:
                sys.path[:] = old_path

    def test_model_search_uses_bootstrap_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            folder = bundled / model_paths.labeler_models_dirname()
            folder.mkdir(parents=True)
            for fname in model_paths.MODEL_FILES.values():
                (folder / fname).write_bytes(b"model")
            user = Path(tmp) / "user"
            user.mkdir()
            with mock.patch.dict(
                os.environ,
                {model_paths.BUNDLED_ENV_VAR: str(bundled)},
                clear=False,
            ), mock.patch.object(model_paths, "user_model_dir", return_value=user):
                os.environ.pop(model_paths.ENV_VAR, None)
                found = model_paths.resolve_model_dir(auto_download=False)
            self.assertEqual(found, folder)

    def test_app_updates_are_only_offered_to_modular_bundles(self) -> None:
        manifest = updater.UpdateManifest(
            release_version="9.0.0",
            released_utc="",
            app=updater.ModulePackage("9.0.0", "https://example/app.zip", 1, None),
            models=None,
            runtime_url_macos=None,
            runtime_url_windows=None,
        )
        with mock.patch.dict(
            os.environ,
            {updater.BUNDLE_FLAVOR_ENV: ""},
            clear=False,
        ):
            self.assertNotIn("app", updater.available_updates(manifest))
        with mock.patch.dict(
            os.environ,
            {updater.BUNDLE_FLAVOR_ENV: "modular"},
            clear=False,
        ):
            self.assertEqual(updater.available_updates(manifest)["app"], "9.0.0")

    def test_release_manifest_supports_cross_runner_runtime_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            code = work / "swcstudio-code-v1.2.3.zip"
            models = work / "swcstudio-models-v1.2.3.zip"
            output = work / "update_manifest.json"
            code.write_bytes(b"code")
            models.write_bytes(b"models")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/generate_release_manifest.py"),
                    "--version",
                    "1.2.3",
                    "--code-zip",
                    str(code),
                    "--models-zip",
                    str(models),
                    "--runtime-asset-macos",
                    "SWC-Studio-v1.2.3-macOS.zip",
                    "--runtime-asset-windows",
                    "SWC-Studio-v1.2.3-Windows.zip",
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(
                payload["runtime"]["url_windows"].endswith(
                    "/SWC-Studio-v1.2.3-Windows.zip"
                )
            )


if __name__ == "__main__":
    unittest.main()
