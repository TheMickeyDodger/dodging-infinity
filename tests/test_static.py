import ast
import importlib.util
import json
import os
import subprocess
import tempfile
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

print('static tests: OK')
