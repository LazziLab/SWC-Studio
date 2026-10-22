from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
SINGLE_SOMA = FIXTURE_DIR / "single-soma.swc"
MULTI_SOMA = FIXTURE_DIR / "multi-soma.swc"


def _read_swc_rows(path: Path) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        rows.append((int(float(parts[0])), int(float(parts[6]))))
    return rows


def _leaf_node_id(path: Path) -> int:
    rows = _read_swc_rows(path)
    ids = {node_id for node_id, _ in rows}
    parents = {parent_id for _, parent_id in rows}
    leaves = sorted(ids - parents)
    if not leaves:
        raise AssertionError("No leaf nodes found in sample SWC.")
    return leaves[0]


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    if start < 0:
        raise AssertionError(f"No JSON object found in stdout:\n{stdout}")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object in stdout, got: {type(payload)!r}")
    return payload


class CLISmokeTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-cli-tests-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _copy_file_fixture(self, subdir: str, source: Path = SINGLE_SOMA) -> Path:
        dest_dir = self.tmp / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / source.name
        shutil.copy2(source, dest_path)
        return dest_path

    def _copy_folder_fixture(self, subdir: str) -> Path:
        dest_dir = self.tmp / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SINGLE_SOMA, dest_dir / SINGLE_SOMA.name)
        shutil.copy2(MULTI_SOMA, dest_dir / MULTI_SOMA.name)
        return dest_dir

    def _run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "swcstudio.cli.cli", *args]
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=run_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Command failed: {' '.join(cmd)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )
        return result

    def test_version_flag(self) -> None:
        from swcstudio import __version__

        result = self._run_cli("--version")
        self.assertEqual(result.stdout.strip(), f"swcstudio {__version__}")

    def test_check_and_batch_commands(self) -> None:
        file_path = self._copy_file_fixture("check")
        folder_validate = self._copy_folder_fixture("batch_validate")
        folder_split = self._copy_folder_fixture("batch_split")
        folder_auto = self._copy_folder_fixture("batch_auto")
        file_radii = self._copy_file_fixture("batch_radii")
        folder_simplify = self._copy_folder_fixture("batch_simplify")
        folder_index = self._copy_folder_fixture("batch_index")

        self._run_cli("check", str(file_path))
        self._run_cli("batch", "validate", str(folder_validate))
        self._run_cli("batch", "split", str(folder_split))
        self._run_cli("batch", "auto-typing", str(folder_auto))

        radii_result = self._run_cli("batch", "radii-clean", str(file_radii))
        self.assertNotIn('"change_lines"', radii_result.stdout)
        self.assertNotIn('"change_details"', radii_result.stdout)
        self.assertNotIn('"dataframe"', radii_result.stdout)
        self.assertNotIn('"bytes"', radii_result.stdout)
        radii_payload = _extract_json(radii_result.stdout)
        self.assertIn("radius_changes", radii_payload)
        self.assertIn("output_path", radii_payload)

        self._run_cli("batch", "simplify", str(folder_simplify))
        self._run_cli("batch", "index-clean", str(folder_index))

    def test_validation_and_visualization_commands(self) -> None:
        auto_fix = self._copy_file_fixture("val_auto_fix")
        run_file = self._copy_file_fixture("val_run")
        auto_label = self._copy_file_fixture("val_auto_label")
        radii_file = self._copy_file_fixture("val_radii")
        index_file = self._copy_file_fixture("val_index")
        viz_file = self._copy_file_fixture("viz")

        self._run_cli("validation", "auto-fix", str(auto_fix))
        self._run_cli("validation", "rule-guide")
        self._run_cli("validation", "run", str(run_file))
        self._run_cli("validation", "auto-label", str(auto_label))
        self._run_cli("validation", "radii-clean", str(radii_file))
        self._run_cli("validation", "index-clean", str(index_file))
        self._run_cli("visualization", "mesh-editing", str(viz_file))

    def test_morphology_and_geometry_commands(self) -> None:
        dendrogram_file = self._copy_file_fixture("morph_d")
        radius_file = self._copy_file_fixture("morph_radius")
        type_file = self._copy_file_fixture("morph_type")
        geom_simplify = self._copy_file_fixture("geom_simplify")
        geom_move_node = self._copy_file_fixture("geom_move_node")
        geom_move_subtree = self._copy_file_fixture("geom_move_subtree")
        geom_connect = self._copy_file_fixture("geom_connect")
        geom_disconnect = self._copy_file_fixture("geom_disconnect")
        geom_delete_leaf = self._copy_file_fixture("geom_delete_leaf")
        geom_delete_reconnect = self._copy_file_fixture("geom_delete_reconnect")
        geom_delete_subtree = self._copy_file_fixture("geom_delete_subtree")
        geom_insert = self._copy_file_fixture("geom_insert")

        dendrogram_result = self._run_cli(
            "morphology",
            "dendrogram-edit",
            str(dendrogram_file),
            "--node-id",
            "2",
            "--new-type",
            "3",
        )
        self.assertNotIn('"changed_node_ids"', dendrogram_result.stdout)
        dendrogram_payload = _extract_json(dendrogram_result.stdout)
        self.assertIn("changed_node_count", dendrogram_payload)
        self.assertIn("output_path", dendrogram_payload)

        self._run_cli("morphology", "set-radius", str(radius_file), "--node-id", "2", "--radius", "1.23")
        self._run_cli("morphology", "set-type", str(type_file), "--node-id", "2", "--new-type", "3")

        self._run_cli("geometry", "simplify", str(geom_simplify))
        self._run_cli("geometry", "move-node", str(geom_move_node), "--node-id", "2", "--x", "9382.54", "--y", "2781.59", "--z", "8958.86")
        self._run_cli("geometry", "move-subtree", str(geom_move_subtree), "--root-id", "14", "--x", "9382.99", "--y", "2785.20", "--z", "8958.38")
        self._run_cli("geometry", "connect", str(geom_connect), "--start-id", "2", "--end-id", "14")
        self._run_cli("geometry", "disconnect", str(geom_disconnect), "--start-id", "2", "--end-id", "4")
        self._run_cli("geometry", "delete-node", str(geom_delete_leaf), "--node-id", str(_leaf_node_id(geom_delete_leaf)))
        self._run_cli("geometry", "delete-node", str(geom_delete_reconnect), "--node-id", "13", "--reconnect-children")
        self._run_cli("geometry", "delete-subtree", str(geom_delete_subtree), "--root-id", "14")
        self._run_cli("geometry", "insert", str(geom_insert), "--start-id", "2", "--end-id", "3", "--x", "9383.0", "--y", "2780.8", "--z", "8959.1")

    def test_plugin_commands(self) -> None:
        plugin_dir = self.tmp / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin_path = plugin_dir / "swcstudio_tmp_plugin.py"
        plugin_path.write_text(
            "\n".join(
                [
                    'PLUGIN_MANIFEST = {"plugin_id": "tmp.smoke.plugin", "name": "Temp Smoke Plugin", "version": "0.1.0", "api_version": "1"}',
                    "",
                    "def register_plugin(registrar):",
                    "    return None",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        env = {"PYTHONPATH": str(plugin_dir)}

        self._run_cli("plugins", "list", env=env)
        self._run_cli("plugins", "list-loaded", env=env)
        self._run_cli("plugins", "load", "swcstudio_tmp_plugin", env=env)

    def test_installation_doctor_command(self) -> None:
        result = self._run_cli("doctor", "--quick", "--json")
        payload = _extract_json(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["model_loading_checked"])


if __name__ == "__main__":
    unittest.main()
