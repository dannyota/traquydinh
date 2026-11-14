import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT/'.github/workflows/ci.yml'


class DeployCITest(unittest.TestCase):
    def preflight(self, paths, event='pull_request'):
        script = re.search(r'      - id: paths\n.*?        run: \|\n((?:          [^\n]*\n|\n)+)',
                           WORKFLOW.read_text(), re.S).group(1)
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            git = work/'git'
            git.write_text('''#!/usr/bin/env python3
import json, os, sys
assert sys.argv[1:7] == ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z']
for path in json.loads(os.environ['CHANGED_PATHS']): sys.stdout.buffer.write(path.encode()+b'\\0')
''')
            git.chmod(0o755)
            output = work/'output'
            env = dict(os.environ, PATH=str(work)+os.pathsep+os.environ['PATH'], TMPDIR=str(work),
                       EVENT_NAME=event, BASE_SHA='a'*40, HEAD_SHA='b'*40,
                       CHANGED_PATHS=json.dumps(paths), GITHUB_OUTPUT=str(output))
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', textwrap.dedent(script)], env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def test_deploy_changes_select_static_gate_on_pull_requests(self):
        for path in ['deploy/host/backup.py', 'deploy/images/check-image.py',
                     'deploy/github/manifest.py', 'deploy/github/settings.json',
                     'deploy/host/render-units-test.sh', '.github/workflows/images.yml',
                     '.github/workflows/rollback.yml', '.github/workflows/ci.yml']:
            with self.subTest(path=path):
                self.assertEqual(self.preflight([path])['go'], 'true')

    def test_docs_only_skips_static_but_pushes_run_it(self):
        self.assertEqual(self.preflight(['docs/design/backups.md'])['go'], 'false')
        self.assertEqual(self.preflight([], event='push')['go'], 'true')

    def test_agenteval_paths_select_the_harness_job(self):
        for path in ['apps/agenteval/src/trachuan_agenteval/agent.py',
                     'apps/agenteval/tests/fixtures/matcher_ranges.json',
                     'apps/agenteval/uv.lock', '.github/workflows/ci.yml']:
            with self.subTest(path=path):
                self.assertEqual(self.preflight([path])['agenteval'], 'true')
        for path in ['docs/runbooks/agent-eval.md', 'apps/docembed/pyproject.toml',
                     'apps/web/app/app.vue']:
            with self.subTest(path=path):
                self.assertEqual(self.preflight([path])['agenteval'], 'false')
        self.assertEqual(self.preflight([], event='push')['agenteval'], 'true')

    def test_agenteval_job_uses_python_pins_and_the_required_gate(self):
        workflow = WORKFLOW.read_text()
        job = workflow.split('\n  agenteval:\n', 1)[1].split('\n  queryembed:\n', 1)[0]
        self.assertIn("if: needs.preflight.outputs.agenteval == 'true'", job)
        self.assertIn('working-directory: apps/agenteval', job)
        for command in ['uv sync --frozen', 'uv run --frozen pytest',
                        'uv run --frozen ruff check', 'uv run --frozen ruff format --check']:
            self.assertIn('      - run: ' + command + '\n', job)
        docembed = workflow.split('\n  docembed:\n', 1)[1].split('\n  agenteval:\n', 1)[0]
        self.assertEqual(re.findall(r'      - uses: (\S+)', job),
                         re.findall(r'      - uses: (\S+)', docembed))
        aggregate = workflow.split('\n  ci:\n', 1)[1]
        needs = re.search(r'    needs:\s*\[([^\]]+)\]', aggregate).group(1)
        self.assertIn('agenteval', [name.strip() for name in needs.split(',')])
        self.assertIn('AGENTEVAL_NEEDED: ${{ needs.preflight.outputs.agenteval }}', aggregate)
        self.assertIn('AGENTEVAL: ${{ needs.agenteval.result }}', aggregate)
        self.assertIn('test "$AGENTEVAL" = success', aggregate)
        self.assertIn('test "$AGENTEVAL" = skipped', aggregate)

    def test_required_static_job_runs_all_deploy_checks(self):
        workflow = WORKFLOW.read_text()
        static = workflow.split('\n  static:\n', 1)[1].split('\n  integration:\n', 1)[0]
        commands = re.findall(r'      - run: (.+)', static)
        for command in ["PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/host -p '*_test.py'",
                        "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/images -p '*_test.py'",
                        "PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/github -p '*_test.py'",
                        'bash deploy/github/actionlint.sh',
                        'bash deploy/host/render-units-test.sh',
                        'python3 deploy/host/check-mounts.py']:
            self.assertIn(command, commands)
        self.assertIn("if: needs.preflight.outputs.go == 'true'", static)
        self.assertIn('test "$STATIC" = success', workflow.split('\n  ci:\n', 1)[1])


if __name__ == '__main__':
    unittest.main()
