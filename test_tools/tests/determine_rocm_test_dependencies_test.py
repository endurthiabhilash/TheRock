# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

THEROCK_DIR = Path(__file__).parent.parent.parent
SCRIPT = Path(__file__).parent.parent / "determine_rocm_test_dependencies.py"

sys.path.insert(0, str(THEROCK_DIR / "test_tools"))

from determine_rocm_test_dependencies import (
    _load_consumer_graph,
    get_subprojects_to_test,
    list_subprojects,
)

# ---------------------------------------------------------------------------
# Minimal in-memory graph for unit tests that don't need the full repo graph.
# Mirrors a small slice of the real dependency chain:
#   rocblas -> hipblas, rocsolver   (consumers; same stage math-libs)
#   rocsparse -> hipsparse, rocsolver, hipsolver  (consumers; same stage)
# ---------------------------------------------------------------------------
_MINI_GRAPH = {
    "rocblas": {"consumers": ["hipblas", "rocsolver"]},
    "hipblas": {"consumers": []},
    "rocsolver": {"consumers": ["hipsolver"]},
    "hipsolver": {"consumers": []},
    "rocsparse": {"consumers": ["hipsparse", "rocsolver", "hipsolver"]},
    "hipsparse": {"consumers": []},
    "rocwmma": {"consumers": []},
    "amd-dbgapi": {
        "consumers": ["rocgdb", "rocr-debug-agent", "rocr-debug-agent-tests"],
    },
    "rocr-debug-agent": {"consumers": ["rocr-debug-agent-tests"]},
    "rocr-debug-agent-tests": {"consumers": []},
    "rocgdb": {"consumers": []},
}

# The subproject -> build-stage mapping is derived from committed files
# (artifact-*.toml + BUILD_TOPOLOGY.toml), so the fixture writes a minimal
# version of both. This keeps the same-stage cut exercised end to end without
# depending on the real repo. All math-libs subprojects share one stage; the
# debug-tools ones share another.
_MINI_STAGE_OF = {
    "math-libs": ["rocblas", "hipblas", "rocsolver", "hipsolver", "rocsparse",
                  "hipsparse", "rocwmma"],
    "debug-tools": ["amd-dbgapi", "rocgdb", "rocr-debug-agent",
                    "rocr-debug-agent-tests"],
}


def _write_topology_fixtures(tmp_dir: Path) -> None:
    """Write a minimal BUILD_TOPOLOGY.toml + artifact-*.toml pair per stage.

    Artifact name == stage name here for simplicity (one group/artifact per
    stage). The artifact-*.toml component keys use the "<path>/<sub>/stage"
    shape that _build_stage_of_map() parses.
    """
    topo_lines = []
    for stage in _MINI_STAGE_OF:
        topo_lines += [
            f"[build_stages.{stage}]",
            f'artifact_groups = ["{stage}"]',
            f"[artifact_groups.{stage}]",
            f"[artifacts.{stage}]",
            f'artifact_group = "{stage}"',
            "",
        ]
    (tmp_dir / "BUILD_TOPOLOGY.toml").write_text("\n".join(topo_lines))

    for stage, subs in _MINI_STAGE_OF.items():
        art_lines = [f'[components.lib."libs/{sub}/stage"]' for sub in subs]
        (tmp_dir / f"artifact-{stage}.toml").write_text("\n".join(art_lines) + "\n")


def _write_mini_graph(tmp_dir: Path) -> Path:
    """Write the mini graph + topology fixtures to a temp directory."""
    tools_dir = tmp_dir / "test_tools"
    tools_dir.mkdir(exist_ok=True)
    graph_file = tools_dir / "therock_consumer_graph.json"
    graph_file.write_text(json.dumps(_MINI_GRAPH, indent=2))
    _write_topology_fixtures(tmp_dir)
    return tmp_dir


class TestConsumerGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_mini_graph(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- graph loading ---

    def test_load_committed_graph(self):
        graph = _load_consumer_graph(self.tmp)
        self.assertIn("rocblas", graph)
        self.assertIn("hipblas", graph["rocblas"]["consumers"])

    def test_load_graph_missing_raises(self):
        empty_dir = self.tmp / "empty"
        empty_dir.mkdir()
        with self.assertRaises(FileNotFoundError):
            _load_consumer_graph(empty_dir)

    # --- get_subprojects_to_test (unit tests via mini graph) ---

    def test_rocblas_consumers(self):
        result = get_subprojects_to_test(["rocblas"], self.tmp)
        self.assertIn("rocblas", result)
        # Same-stage consumers
        self.assertIn("hipblas", result)
        self.assertIn("rocsolver", result)

    def test_case_insensitive(self):
        result = get_subprojects_to_test(["rocBLAS"], self.tmp)
        self.assertIn("rocblas", result)
        self.assertIn("hipblas", result)

    def test_rocsparse_consumers(self):
        result = get_subprojects_to_test(["rocsparse"], self.tmp)
        self.assertIn("rocsparse", result)
        self.assertIn("hipsparse", result)
        self.assertIn("rocsolver", result)
        self.assertIn("hipsolver", result)

    def test_leaf_node_returns_itself(self):
        result = get_subprojects_to_test(["rocwmma"], self.tmp)
        self.assertEqual(result, {"rocwmma"})

    def test_hyphenated_names(self):
        result = get_subprojects_to_test(["amd-dbgapi"], self.tmp)
        self.assertIn("amd-dbgapi", result)
        self.assertIn("rocgdb", result)
        self.assertIn("rocr-debug-agent", result)
        self.assertIn("rocr-debug-agent-tests", result)

    def test_unknown_project_not_in_result(self):
        result = get_subprojects_to_test(["nonexistent-lib"], self.tmp)
        # Unknown project; nothing selected for it (warning is emitted to stderr)
        self.assertEqual(result, {"nonexistent-lib"})

    # --- overrides ---

    def test_include_override(self):
        overrides_file = self.tmp / "test_tools" / "test_subprojects_overrides.json"
        overrides_file.write_text(
            json.dumps(
                {
                    "rocwmma": {
                        "include": ["hipcub", "rocthrust"],
                        "exclude": [],
                    }
                }
            )
        )
        result = get_subprojects_to_test(["rocwmma"], self.tmp)
        self.assertIn("hipcub", result)
        self.assertIn("rocthrust", result)

    def test_exclude_override(self):
        overrides_file = self.tmp / "test_tools" / "test_subprojects_overrides.json"
        overrides_file.write_text(
            json.dumps(
                {
                    "rocblas": {
                        "include": [],
                        "exclude": ["hipblas"],
                    }
                }
            )
        )
        result = get_subprojects_to_test(["rocblas"], self.tmp)
        self.assertIn("rocblas", result)
        self.assertNotIn("hipblas", result)
        self.assertIn("rocsolver", result)

    def test_mixed_case_override_key_matches(self):
        # Regression for finding #1: a mixed-case override key must match a
        # changed project regardless of the casing the user passes.
        overrides_file = self.tmp / "test_tools" / "test_subprojects_overrides.json"
        overrides_file.write_text(
            json.dumps(
                {
                    "RocBLAS": {
                        "include": ["Extra-Test"],
                        "exclude": [],
                    }
                }
            )
        )
        # Changed project passed in yet another casing.
        result = get_subprojects_to_test(["ROCBLAS"], self.tmp)
        self.assertIn("extra-test", result)

    def test_stageless_project_uses_include_override(self):
        # Regression for finding #4/#15: a project with no derivable stage
        # (proj_stage is None) selects zero same-stage consumers, so its
        # dependents must come from an explicit include override.
        overrides_file = self.tmp / "test_tools" / "test_subprojects_overrides.json"
        overrides_file.write_text(
            json.dumps({"rocm-core": {"include": ["amdsmi", "ocl-clr"], "exclude": []}})
        )
        # rocm-core is not in the mini graph and has no stage -> only the
        # override includes (plus itself) should be selected.
        result = get_subprojects_to_test(["rocm-core"], self.tmp)
        self.assertIn("amdsmi", result)
        self.assertIn("ocl-clr", result)

    def test_exclude_order_independent_multi_project(self):
        # Regression for finding #7: when multiple projects change, an exclude
        # for one must not be undone by another project adding that same
        # consumer. rocsparse has rocsolver as a same-stage consumer; rocblas
        # excludes rocsolver. rocsolver must be excluded regardless of order.
        overrides_file = self.tmp / "test_tools" / "test_subprojects_overrides.json"
        overrides_file.write_text(
            json.dumps({"rocblas": {"include": [], "exclude": ["rocsolver"]}})
        )
        result_ab = get_subprojects_to_test(["rocblas", "rocsparse"], self.tmp)
        result_ba = get_subprojects_to_test(["rocsparse", "rocblas"], self.tmp)
        self.assertNotIn("rocsolver", result_ab)
        self.assertNotIn("rocsolver", result_ba)
        self.assertEqual(result_ab, result_ba)


class TestListSubprojects(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_mini_graph(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_names(self):
        names = list_subprojects(self.tmp, show_deps=False)
        self.assertIn("rocblas", names)
        self.assertIn("rocwmma", names)

    def test_list_with_deps(self):
        deps = list_subprojects(self.tmp, show_deps=True)
        self.assertIn("hipblas", deps["rocblas"])
        self.assertIn("rocsolver", deps["rocblas"])
        self.assertEqual(deps["rocwmma"], "empty")


class TestRealGraph(unittest.TestCase):
    """Integration tests using the committed consumer graph from the repo."""

    def test_committed_graph_exists(self):
        graph_file = THEROCK_DIR / "test_tools" / "therock_consumer_graph.json"
        self.assertTrue(
            graph_file.exists(),
            "test_tools/therock_consumer_graph.json must be committed to the repo",
        )

    def test_rocblas_in_real_graph(self):
        graph = _load_consumer_graph(THEROCK_DIR)
        self.assertIn("rocblas", graph)
        self.assertIn("consumers", graph["rocblas"])

    def test_get_subprojects_real_graph_rocblas(self):
        result = get_subprojects_to_test(["rocBLAS"], THEROCK_DIR)
        self.assertIn("rocblas", result)
        self.assertIn("hipblas", result)
        self.assertIn("rocsolver", result)

    def test_unknown_project_warning_real_graph(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--changed-projects", "rocblass"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Warning: unrecognized project", result.stderr)
        self.assertIn("rocblass", result.stderr)

    def test_empty_changed_projects_outputs_wildcard(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(result.stdout.strip(), "*")

    def test_empty_flag_outputs_wildcard(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--changed-projects"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "*")

    def test_comma_separated_input(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--changed-projects", "rocblas,hipblas"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        projects = json.loads(result.stdout.strip())
        self.assertIn("rocblas", projects)
        self.assertIn("hipblas", projects)
        self.assertIn("rocsolver", projects)

    def test_projects_prefix_normalization(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--changed-projects", "projects/rocblas"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        projects = json.loads(result.stdout.strip())
        self.assertIn("rocblas", projects)
        self.assertIn("hipblas", projects)

    def test_gha_output_format(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            output_file = f.name
        try:
            env = os.environ.copy()
            env["GITHUB_OUTPUT"] = output_file
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--changed-projects",
                    "rocblas",
                    "--gha-output",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0)
            content = Path(output_file).read_text()
            self.assertIn("projects_to_test=", content)
            self.assertIn(",", content)
            self.assertIn("rocblas", content)
        finally:
            os.unlink(output_file)


if __name__ == "__main__":
    unittest.main()
