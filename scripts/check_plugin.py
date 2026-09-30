"""Check packaged files, versions and both bundled CLIs after clean extraction."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile


archive_path = Path(sys.argv[1]).resolve()
with TemporaryDirectory(prefix="anchor-plugin-check-") as raw:
    root = Path(raw)
    plugin = root / "plugin"
    project = root / "project"
    project.mkdir()
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in names)
        assert all(".git/" not in n and "__pycache__" not in n and ".egg-info" not in n for n in names)
        assert json.loads(archive.read(".claude-plugin/plugin.json"))["version"] == "0.4.0"
        archive.extractall(plugin)
    scripts = plugin / "skills/anchor-orchestrator/scripts"
    output = subprocess.check_output([sys.executable, str(scripts / "anchor-plan.py"),
        str(plugin / "examples/workflow.json")], cwd=project, text=True)
    assert [n["node_id"] for n in json.loads(output)["nodes"]] == ["read", "render", "publish", "verify"]
    output = subprocess.check_output([sys.executable, str(scripts / "anchor.py"), "--root", str(project),
        "init", "--template", "dashboard-refresh"], cwd=project, text=True)
    assert json.loads(output)["status"] == "ok"
    assert (project / "workflows/dashboard-refresh.md").is_file()
print("Extracted plugin: safe archive, manifest, pure compiler and legacy scaffolding passed.")
