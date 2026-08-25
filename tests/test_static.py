import ast
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

from herdr.guards import (
    install_git_guard,
    push_approval_path,
    push_approval_valid,
    push_identity,
    simple_git_push,
)
from herdr.initialize import (
    ensure_local_herd_exclude,
    local_exclude_path,
)
from herdr.runtime import find_agent_status

R = Path(__file__).resolve().parents[1]

herdctl_src = (
    R / 'herdctl.py'
).read_text()

package_sources = [
    path.read_text()
    for path in sorted(
        (R / 'herdr').glob('*.py')
    )
]

# Syntax-check the CLI entrypoint directly.
ast.parse(herdctl_src)

# Static architecture assertions should search the complete harness,
# because functionality now lives in package-owned modules rather than
# only in the compatibility CLI.
src = '\n'.join(
    [
        herdctl_src,
        *package_sources,
    ]
)
config = json.loads((R / 'herd.config.example.json').read_text())
assert config['version'] == 4
assert config['preset'] == 'max-quality'
assert config['context']['clear_before_new_task'] is True
assert config['context']['reset_commands']['codex'] == '/new'
assert config['roles']['reviewer']['kind'] == 'codex'
assert 'gpt-5.6-sol' in config['roles']['reviewer']['args']

for n in ['supervisor', 'lead', 'executor', 'reviewer']:
    assert (R / 'roles' / f'{n}.md').exists()
assert (R / 'memory' / 'task-history.md').exists()

for required in [
    'approve-commit', 'approve-push', 'reference-transaction', 'pre-push', '_guard-pretool',
    'resolve_repo_ref', 'task-complete', 'clear-contexts', 'restart-heartbeat',
    'rejection-drill', 'review-decision', 'gpt-5.6-sol', 'max-quality', '/new',
]:
    assert required in src, required

spec = importlib.util.spec_from_file_location('herdctl_test', R / 'herdctl.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

# Config migration preserves user settings and adds new defaults.
cur = {'project': {'test_command': 'custom test'}, 'version': 3}
h.deep_merge_defaults(cur, h.DEFAULT)
assert cur['project']['test_command'] == 'custom test'
assert cur['context']['clear_before_new_task'] is True
assert cur['context']['reset_commands']['codex'] == '/new'

# Presets are exact and copy rather than alias mutable dicts.
d = json.loads(json.dumps(h.DEFAULT))
h.apply_preset_to_config(d, 'max-quality')
assert d['roles']['reviewer']['kind'] == 'codex'
assert 'gpt-5.6-sol' in d['roles']['reviewer']['args']
assert h.preset_name_from_config(d) == 'max-quality'

# Strict review protocol accepts only canonical tokens; last token wins.
assert h.parse_review_decision('HERD_DECISION: APPROVE') == ('APPROVE', 'APPROVE')
assert h.parse_review_decision('HERD_DECISION: REJECT') == ('REJECT', 'REJECT')
assert h.parse_review_decision('HERD_DECISION: ACCEPT') == (None, 'ACCEPT')
assert h.parse_review_decision('HERD_DECISION: REJECT\n...\nHERD_DECISION: APPROVE') == ('APPROVE', 'APPROVE')
old_contract = 'HERD_DECISION: REJECT\n' + ('old line\n' * 100) + 'review completed without a terminal token'
assert h.parse_review_decision(old_contract) == (None, None)
assert simple_git_push('git push --no-verify')[0] is False
assert simple_git_push('git push --force')[0] is False

# Herdr status extraction tolerates schema movement.
assert find_agent_status({'result': {'agent': {'agent_status': 'working'}}}) == 'working'
assert find_agent_status({'result': {'something': {'state': 'blocked'}}}) == 'blocked'

# Local Herd exclusion must not mutate tracked .gitignore; init installs all Git guards.
with tempfile.TemporaryDirectory() as td:
    repo = Path(td) / 'repo'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    (repo / '.gitignore').write_text('node_modules/\n')
    (repo / 'README.md').write_text('hello\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'README.md', '.gitignore'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)

    ensure_local_herd_exclude(repo)
    assert (repo / '.gitignore').read_text() == 'node_modules/\n'
    assert '.herd/' in local_exclude_path(repo).read_text()

    # Minimal harness config needed by guards.
    (repo / '.herd' / 'state').mkdir(parents=True)
    (repo / '.herd' / 'herd.config.json').write_text(json.dumps(h.DEFAULT))
    install_git_guard(repo)
    hooks = repo / '.git' / 'hooks'
    assert 'HERD COMMIT GUARD' in (hooks / 'pre-commit').read_text()
    assert 'HERD REFERENCE GUARD' in (hooks / 'reference-transaction').read_text()
    assert 'HERD PUSH GUARD' in (hooks / 'pre-push').read_text()

# Push approval binds exact repo/branch/HEAD/remote/target.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    bare = base / 'remote.git'
    repo = base / 'repo'
    subprocess.run(['git', 'init', '--bare', '-q', str(bare)], check=True)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', str(bare)], check=True)
    (repo / 'a.txt').write_text('one\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'a.txt'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'one'], check=True)
    branch = subprocess.check_output(['git', '-C', str(repo), 'branch', '--show-current'], text=True).strip()
    subprocess.run(['git', '-C', str(repo), 'push', '-q', '-u', 'origin', branch], check=True)
    (repo / '.herd' / 'state').mkdir(parents=True)
    (repo / '.herd' / 'herd.config.json').write_text(json.dumps(h.DEFAULT))
    ident = push_identity(repo, 'origin', branch)
    token = dict(ident, approved_at=1, expires_at=2**31)
    push_approval_path(repo).write_text(json.dumps(token))
    ok, msg = push_approval_valid(repo)
    assert ok, msg
    (repo / 'a.txt').write_text('two\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'a.txt'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'two'], check=True)
    ok, msg = push_approval_valid(repo)
    assert not ok and 'head' in msg.lower()

# --- Codex Gateway / Telegram adapter architectural isolation ------------
# The gateway and the Telegram Remote Operator adapter must never import
# or invoke Herdr in any form. Three independent checks, all required:
# an AST walk, a token scan, and a behavioral import probe.

gateway_files = (
    sorted((R / 'codex_gateway').glob('*.py'))
    + [R / 'codexgw.py']
    + sorted((R / 'telegram_operator').glob('*.py'))
    + [R / 'tgop.py']
)
assert gateway_files, 'codex_gateway sources not found'
assert any('telegram_operator' in str(p) for p in gateway_files), (
    'telegram_operator sources not found'
)
FORBIDDEN_ROOTS = {'herdr', 'herdctl'}

# 1. AST: no Import/ImportFrom naming herdr/herdctl, and no dynamic-import
# machinery at all (stricter than forbidding only herdr-valued arguments:
# a dynamic import with a computed argument cannot be proven safe
# statically, so the gateway is not allowed any).
for path in gateway_files:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split('.')[0] not in FORBIDDEN_ROOTS, (path, alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or '').split('.')[0]
            assert root not in FORBIDDEN_ROOTS, (path, node.module)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, 'id', getattr(node.func, 'attr', None))
            assert name not in {'__import__', 'import_module'}, (path, name)
        elif isinstance(node, ast.Name):
            assert node.id != '__import__', path

# 2. Token scan: outside comments and docstrings, no identifier token and
# no string literal may reference herdr/herdctl — matched as a
# case-insensitive SUBSTRING, so embedded occurrences (for example a
# HerdrControlPlane-style identifier) are caught too. Docstring prose
# explaining the boundary is allowed and expected. String literals also
# must never name the repository-scoped orchestration-state directory
# ('.herd'): the adapter and gateway may not hold even a path string
# into it (its behavioral counterpart lives in the adapter test suite).
FORBIDDEN_SUBSTRINGS = ('herdr', 'herdctl')
FORBIDDEN_STRING_SUBSTRINGS = FORBIDDEN_SUBSTRINGS + ('.herd',)


def _contains_forbidden(token_text, words=FORBIDDEN_SUBSTRINGS):
    lowered = token_text.lower()
    return any(word in lowered for word in words)


for path in gateway_files:
    source = path.read_text()
    docstring_positions = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_positions.add((body[0].value.lineno, body[0].value.col_offset))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            assert not _contains_forbidden(token.string), (path, token.start, token.string)
        elif token.type == tokenize.STRING and token.start not in docstring_positions:
            assert not _contains_forbidden(
                token.string, FORBIDDEN_STRING_SUBSTRINGS
            ), (path, token.start, token.string)

# 3. Behavioral: importing the gateway, the Telegram adapter, and their
# entry scripts must not load any herdr/herdctl module.
probe = subprocess.run(
    [
        sys.executable,
        '-c',
        (
            'import sys\n'
            'import codex_gateway, codexgw\n'
            'import telegram_operator, tgop\n'
            'import telegram_operator.adapter, telegram_operator.cli\n'
            'import telegram_operator.launchagent\n'
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name == "herdctl" or name == "herdr" or name.startswith("herdr.")\n'
            ')\n'
            'print("\\n".join(bad))\n'
            'sys.exit(1 if bad else 0)\n'
        ),
    ],
    cwd=str(R),
    capture_output=True,
    text=True,
)
assert probe.returncode == 0, (probe.returncode, probe.stdout, probe.stderr)

print('static tests: OK')
