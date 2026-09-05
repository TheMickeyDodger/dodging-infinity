import ast
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

from _hermetic_git import run_git

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
    run_git('-C', str(repo), 'commit', '-qm', 'initial')

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
    run_git('-C', str(repo), 'commit', '-qm', 'one')
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
    run_git('-C', str(repo), 'commit', '-qm', 'two')
    ok, msg = push_approval_valid(repo)
    assert not ok and 'head' in msg.lower()

# --- Codex Gateway / Telegram adapter architectural isolation ------------
# The gateway, the Telegram Remote Operator adapter, and the workflow
# authority layer must never import or invoke Herdr in any form. Three
# independent checks, all required: an AST walk, a token scan, and a
# behavioral import probe.

# ONE shared derivation for every file-set-scanning check (round-09
# closing pass, the reviewer's I2->I4 recurring finding): the full
# product file set is DERIVED by walking the tree — the same helper
# the bound-constant registry and the carve-out caller-set pin use —
# and each scan FILTERS its scope from it, so a new file, nested
# submodule, or new entry point is inside every scan the moment it
# exists.
from test_workflow_authority import derive_product_python_files

product_files = derive_product_python_files(R)
assert product_files, 'derived product file set is empty'
for known in ('codex_gateway/role_turn.py', 'telegram_operator/adapter.py',
              'workflow_authority/record.py', 'target_runtime/broker.py',
              'codexgw.py', 'tgop.py', 'dirun.py', 'herdctl.py'):
    assert any(
        p.relative_to(R).as_posix() == known for p in product_files
    ), ('derived product set lost a known member', known)

_HERDR_FREE_ROOTS = (
    'codex_gateway', 'telegram_operator', 'workflow_authority',
    'operator_session', 'human_interaction', 'durable_execution',
    'capability', 'worker',
)
gateway_files = sorted(
    p for p in product_files
    if p.relative_to(R).parts[0] in _HERDR_FREE_ROOTS
    or p.name in ('codexgw.py', 'tgop.py')
)
assert gateway_files, 'codex_gateway sources not found'
assert any('telegram_operator' in str(p) for p in gateway_files), (
    'telegram_operator sources not found'
)
assert any('workflow_authority' in str(p) for p in gateway_files), (
    'workflow_authority sources not found'
)
assert any('operator_session' in str(p) for p in gateway_files), (
    'operator_session sources not found'
)
assert any('human_interaction' in str(p) for p in gateway_files), (
    'human_interaction sources not found'
)
assert any('durable_execution' in str(p) for p in gateway_files), (
    'durable_execution sources not found'
)
assert any(
    p.relative_to(R).parts[0] == 'capability' for p in gateway_files
), 'capability sources not found'
assert any(
    p.relative_to(R).parts[0] == 'worker' for p in gateway_files
), 'worker sources not found'
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

# 1b. Neutral import boundary for the durable-execution seam, STATIC.
# The shared scan above forbids only the orchestration roots; the
# neutral seam must additionally import NOTHING from the substrate,
# the control chain, or the transport seam. Two checks per import,
# both required: the root must be in an explicit ALLOWLIST (the
# contract is standard-library `abc` plus the package's own module and
# nothing else), and it must not be one of the roots named in the
# FORBIDDEN set, whose assertion message is what states the boundary.
# The allowlist is written out rather than derived from the
# interpreter's own stdlib table because the CI matrix includes an
# interpreter that has no such table, and an explicit set is stricter
# anyway. Relative imports (level > 0) carry no root name a root
# check could see, so they are forbidden outright. Filtered from the
# SAME shared derivation, so a new file under durable_execution/ is
# inside the pin the moment it exists. The check is a function of the
# (package name, allowlist, forbidden set) so every neutral seam runs
# the SAME scan rather than a copy of it.
#
# Each neutral seam's forbidden set is the shared non-seam set PLUS
# the OTHER neutral seams: the three neutral packages have no
# relationship, and none may name another. The sibling half is
# derived per package from ONE roster (`NEUTRAL_SEAM_ROOTS`) minus the
# package's own root, and each derived set is asserted to hold exactly
# the two siblings and never the package itself, so adding a fourth
# seam to the roster closes it against all three at once and a seam
# can never be made to forbid its own package.
NEUTRAL_SEAM_ROOTS = frozenset({
    'durable_execution', 'capability', 'worker',
})
NON_SEAM_FORBIDDEN_IMPORT_ROOTS = frozenset({
    'target_runtime', 'workflow_authority', 'telegram_operator',
    'codex_gateway', 'operator_session', 'human_interaction',
    'herdr', 'herdctl', 'git_transport',
})
assert not (NEUTRAL_SEAM_ROOTS & NON_SEAM_FORBIDDEN_IMPORT_ROOTS)


def _neutral_forbidden_import_roots(package):
    assert package in NEUTRAL_SEAM_ROOTS, package
    siblings = NEUTRAL_SEAM_ROOTS - {package}
    assert len(siblings) == 2, (package, siblings)
    forbidden = NON_SEAM_FORBIDDEN_IMPORT_ROOTS | siblings
    assert package not in forbidden, (package, 'forbids its own root')
    assert forbidden & NEUTRAL_SEAM_ROOTS == siblings, (package, forbidden)
    return forbidden


DURABLE_EXECUTION_ALLOWED_IMPORT_ROOTS = {'abc', 'durable_execution'}
DURABLE_EXECUTION_FORBIDDEN_IMPORT_ROOTS = (
    _neutral_forbidden_import_roots('durable_execution')
)


def _neutral_package_files(package):
    files = sorted(
        p for p in product_files
        if p.relative_to(R).parts[0] == package
    )
    assert files, '%s sources not found' % package
    return files


def _assert_neutral_import_boundary(package, files, allowed_roots,
                                    forbidden_roots):
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, (
                    path, node.level,
                    '%s may not use a relative import' % package,
                )
                imported = [node.module or '']
            else:
                continue
            for name in imported:
                root = name.split('.')[0]
                assert root not in forbidden_roots, (
                    path, name,
                    '%s must not import the substrate, the control'
                    ' chain, the orchestration engine, a sibling seam,'
                    ' or the transport seam' % package,
                )
                assert root in allowed_roots, (
                    path, name,
                    '%s imports only abc and its own package' % package,
                )


durable_execution_files = _neutral_package_files('durable_execution')
_assert_neutral_import_boundary(
    'durable_execution', durable_execution_files,
    DURABLE_EXECUTION_ALLOWED_IMPORT_ROOTS,
    DURABLE_EXECUTION_FORBIDDEN_IMPORT_ROOTS,
)

# 1c. The same neutral import boundary for the one-shot capability
# seam. The forbidden set is the shared non-seam set PLUS both sibling
# seams (`durable_execution`, `worker`), derived from the one roster
# above so it can never contain `capability` itself.
CAPABILITY_ALLOWED_IMPORT_ROOTS = {'abc', 'capability'}
CAPABILITY_FORBIDDEN_IMPORT_ROOTS = (
    _neutral_forbidden_import_roots('capability')
)
capability_files = _neutral_package_files('capability')
_assert_neutral_import_boundary(
    'capability', capability_files,
    CAPABILITY_ALLOWED_IMPORT_ROOTS, CAPABILITY_FORBIDDEN_IMPORT_ROOTS,
)

# 1d. The same neutral import boundary for the worker seam. The
# forbidden set is the shared non-seam set PLUS both sibling seams
# (`durable_execution`, `capability`), derived from the one roster so
# it can never contain `worker` itself. Filtered from the same shared
# derivation, so a new file under worker/ is inside the pin the moment
# it exists.
WORKER_ALLOWED_IMPORT_ROOTS = {'abc', 'worker'}
WORKER_FORBIDDEN_IMPORT_ROOTS = _neutral_forbidden_import_roots('worker')
worker_files = _neutral_package_files('worker')
_assert_neutral_import_boundary(
    'worker', worker_files,
    WORKER_ALLOWED_IMPORT_ROOTS, WORKER_FORBIDDEN_IMPORT_ROOTS,
)

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

# 2b. E-2 path-binding pin (round-04 finding B1; hardened per round-05
# N1/N2): the DI-REMOTE-2 role-turn carve-out guard is usable ONLY by
# codex_gateway/role_turn.py; its only other appearance anywhere in the
# product tree is its definition site in codex_gateway/codex_adapter.py.
# The scanned set is DERIVED by walking every .py in the tree (except
# tests/, herdr/, roles/, scripts/, caches and dot-directories), never
# enumerated — so a NEW product file (dirun.py, target_runtime/*.py, a
# nested codex_gateway submodule) is inside the pin the moment it
# exists. Both NAME tokens and non-docstring STRING tokens count, so
# getattr(module, "assert_role_turn_argv_allowed") is caught too — the
# same string-half mechanism the herdr-isolation scan above uses.
# STATED LIMIT: a computed/concatenated name is beyond ANY static pin;
# this check covers literal references only.
CARVE_OUT_GUARD_NAME = 'assert_role_turn_argv_allowed'
assert CARVE_OUT_GUARD_NAME not in src, (
    'carve-out guard referenced from the orchestration harness'
)
# The SAME shared derivation as every other file-set scan (round-09
# closing pass): one walk, filtered per scope, no inline duplicates.
pin_files = product_files
pin_names = {path.relative_to(R).as_posix() for path in pin_files}
# The derivation must never silently go empty or lose the known
# product files.
assert pin_names, 'derived path-binding pin set is empty'
for required_file in (
    'codex_gateway/role_turn.py',
    'codex_gateway/codex_adapter.py',
    'codex_gateway/gateway.py',
    'telegram_operator/adapter.py',
    'workflow_authority/record.py',
    'codexgw.py',
    'tgop.py',
    'herdctl.py',
):
    assert required_file in pin_names, (
        'derived pin set lost a known product file', required_file
    )
carve_out_counts = {}
for path in pin_files:
    source = path.read_text()
    doc_positions = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                doc_positions.add((body[0].value.lineno, body[0].value.col_offset))
    count = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string == CARVE_OUT_GUARD_NAME:
            count += 1
        elif (
            token.type == tokenize.STRING
            and token.start not in doc_positions
            and CARVE_OUT_GUARD_NAME in token.string
        ):
            count += 1
    carve_out_counts[path.relative_to(R).as_posix()] = count
assert carve_out_counts.get('codex_gateway/role_turn.py', 0) >= 1, carve_out_counts
assert carve_out_counts.get('codex_gateway/codex_adapter.py', 0) == 1, carve_out_counts
for file_name, count in sorted(carve_out_counts.items()):
    if file_name not in (
        'codex_gateway/role_turn.py', 'codex_gateway/codex_adapter.py'
    ):
        assert count == 0, (file_name, CARVE_OUT_GUARD_NAME)

# 2c. target_runtime structural guarantees (I4).
# (i) delivery_authority none is STRUCTURAL (plan D-2): the package
#     contains no shell=True, no os.system, no subprocess usage
#     outside the git transport seam, and no git delivery verb —
#     string literals are compared by EXACT VALUE so identifiers like
#     commit_sha stay legal while a "commit"/"push"/"tag"/"merge"/
#     "gh" argv element can never exist.
# (ii) the hermetic seam is UNREACHABLE in production (plan D-6): no
#     environment read anywhere in the package or its entry script,
#     no "transport"-named option string, and the real GitTransport
#     is constructed with ZERO arguments (nothing to override).
# Filtered from the SAME shared derivation (recursive, so a nested
# target_runtime submodule is scanned automatically).
target_runtime_files = sorted(
    p for p in product_files
    if p.relative_to(R).parts[0] == 'target_runtime'
)
assert target_runtime_files, 'target_runtime sources not found'
assert any(
    p.relative_to(R).as_posix() == 'target_runtime/git_transport.py'
    for p in target_runtime_files
), 'target_runtime scan lost the transport seam'
FORBIDDEN_DELIVERY_LITERALS = {
    '"commit"', "'commit'", '"push"', "'push'", '"tag"', "'tag'",
    '"merge"', "'merge'", '"gh"', "'gh'",
}
for path in target_runtime_files + [R / 'dirun.py']:
    source = path.read_text()
    assert 'shell=True' not in source, (path, 'shell=True')
    tree = ast.parse(source)
    docstring_positions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_positions.add((body[0].value.lineno, body[0].value.col_offset))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, 'id', getattr(node.func, 'attr', None))
            assert name not in {'system', '__import__', 'import_module'}, (path, name)
            if name == 'GitTransport':
                assert not node.args and not node.keywords, (
                    path, 'GitTransport must take no arguments'
                )
        if isinstance(node, ast.Attribute):
            assert node.attr not in {'environ', 'getenv'}, (path, node.attr)
        if isinstance(node, ast.Name):
            assert node.id not in {'environ', 'getenv'}, (path, node.id)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if 'subprocess' in names or getattr(node, 'module', None) == 'subprocess':
                # Two SPAWN seams, and one QUERY seam. The
                # distinction is the whole content of this rule.
                #
                # `git_transport.py` is the Git seam and
                # `process_ownership.py` is the OWNED-SPAWN construct
                # every other process start is required to route
                # through (R-14 E-3). A third SPAWN name here would
                # mean a third place that can start a process without
                # owning its tree, which is what this rule exists to
                # prevent.
                #
                # `spawn_stamp.py` is the third name and is NOT a
                # spawn seam. Added deliberately in I5 for R-54 AR-3,
                # which requires a recorded pgid to be corroborated
                # against its leader's START TIME — otherwise the OS
                # reuses the number and a recovery signals an
                # unrelated process. Its only subprocess use is
                # `ps -o lstart= -p <pid>`: a bounded, read-only
                # query about a pid the caller already holds. Within
                # this seam it starts no tree, so there is no tree to
                # own.
                #
                # Why `process_ownership.py` is not where it lives
                # instead: this module is executed BY PATH in the
                # child (`sys.executable <wrapper> <root> -- argv`),
                # so only its own directory is on the child's
                # sys.path and the package is not importable there.
                # Recording the start time in the PARENT instead
                # would reopen the window R-27 closed — a parent that
                # dies after `Popen` would leave an uncorroborated
                # record, and an uncorroborated record is reported
                # rather than recovered.
                #
                # The residual, stated with it: the `ps` child is a
                # process this repository starts outside the owned
                # construct. It is synchronous, bounded by `ps`'s own
                # exit, and started in the caller's own group, so it
                # leaves no tree to own.
                assert path.name in (
                    'git_transport.py', 'process_ownership.py',
                    'spawn_stamp.py',
                ), (path, 'subprocess only in the git transport seam,'
                    ' the owned-spawn construct, or the spawn_stamp'
                    ' start-time query')
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and token.start not in docstring_positions:
            assert token.string not in FORBIDDEN_DELIVERY_LITERALS, (path, token.start, token.string)
            if path.name in ('cli.py',) or path.name == 'dirun.py':
                assert 'transport' not in token.string.lower(), (
                    path, token.start, 'no transport-named option or config key'
                )

# 2d. Neutral vocabulary pin for the durable-execution seam, over ALL
# tokens INCLUDING docstrings and comments. Section 2 exempts
# docstring prose on purpose: the control-chain packages explain their
# boundary in terms of the thing they exclude. The neutral seam is
# different. Its contract has to read the same under any substrate, so
# no product, provider, sibling-seam, orchestration, or delivery term
# may appear anywhere in it, prose included. Two matchers, both
# required:
#   (a) WORD match: the token is lowercased and split into runs of
#       ASCII letters; a run equal to a forbidden word fails. So
#       `target_runtime` splits to `target` + `runtime` and `runtime`
#       catches it, while `stage` yields `stage` (not `tag`) and
#       `digit` yields `digit` (not `git`): ordinary words are not
#       false positives.
#   (b) NORMALIZED-PHRASE match: the token is lowercased and every
#       non-alphanumeric character deleted; a forbidden phrase found
#       as a substring fails. That catches a multi-word name written
#       any way at all: human_interaction, human-interaction,
#       `human interaction` and HumanInteraction all normalize to
#       humaninteraction.
# Three checks per file, all required, because a forbidden term need
# not be spelled inside any single source token:
#   1. STRUCTURAL: the file contains no f-string (`ast.JoinedStr`) at
#      all. The neutral package is docstrings and `abc`; it holds
#      plain literals only, so no term can be split across formatted
#      parts and nothing here has to evaluate one.
#   2. RAW TOKEN pass: every token's source text, both matchers. This
#      is the only view of COMMENT and NAME tokens, which have no AST
#      node.
#   3. DECODED VALUE pass: both matchers over the value of every `str`
#      `ast.Constant`. The parser folds implicit concatenation
#      (`"Her" "dr"`) into one Constant and decodes escapes
#      (`"He\x72dr"`) before the value is seen, so both are closed by
#      the plain value.
# There is NO carve-out. The package raises nothing. If a future error
# description genuinely needs one of these terms, that is a
# deliberate, reviewed change to this pin, not a pre-built exemption.
# STATED LIMIT: a value computed at run time (chr(), "".join(...), a
# name built by concatenating variables) is beyond ANY static pin;
# this closes literal spellings only.
# Anti-vacuity, all required: the scanned set is non-empty, contains
# both package files, and yields a nonzero token count and a nonzero
# string-value count, so an empty or mis-filtered scope can never pass
# silently.
# Set literal on purpose: the hermetic-git guard in tests/test_hermetic_git.py
# flattens list/tuple call arguments as a git argv (so the subcommand words
# below would read as an identity-requiring invocation) while a set literal is
# opaque to it; this matches the guard's own IDENTITY_SUBCOMMANDS =
# frozenset({...}) idiom. This is an unordered word list, not an argv.
DURABLE_EXECUTION_FORBIDDEN_WORDS = frozenset({
    'herdr', 'herdctl', 'codex', 'telegram', 'github', 'git', 'operator',
    'commit', 'push', 'tag', 'release', 'deploy', 'merge', 'dbos',
    'runtime', 'broker', 'workflow', 'mission',
})
DURABLE_EXECUTION_FORBIDDEN_PHRASES = (
    'targetruntime', 'workflowauthority', 'telegramoperator',
    'codexgateway', 'operatorsession', 'humaninteraction', 'gittransport',
)


def _neutral_vocabulary_violations(token_text, forbidden_words):
    lowered = token_text.lower()
    words = set(re.findall(r'[a-z]+', lowered))
    normalized = re.sub(r'[^a-z0-9]', '', lowered)
    return sorted(words & forbidden_words) + [
        phrase for phrase in DURABLE_EXECUTION_FORBIDDEN_PHRASES
        if phrase in normalized
    ]


def _scan_neutral_vocabulary(package, files, required_files,
                             forbidden_words):
    # One scan, parametrized by package and word set, so every neutral
    # seam runs the SAME three checks with the SAME anti-vacuity
    # counters rather than a copy that could drift.
    scan_names = {p.relative_to(R).as_posix() for p in files}
    assert scan_names, '%s vocabulary scan set is empty' % package
    for required_file in required_files:
        assert required_file in scan_names, (
            '%s vocabulary scan lost a package file' % package,
            required_file,
        )
    tokens_scanned = 0
    values_scanned = 0
    for path in files:
        source = path.read_text()
        tree = ast.parse(source)
        joined = [
            node for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
        ]
        assert not joined, (
            path, [(node.lineno, node.col_offset) for node in joined],
            '%s holds plain string literals only: no f-string, so no'
            ' forbidden term can be split across formatted parts' % package,
        )
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            tokens_scanned += 1
            violations = _neutral_vocabulary_violations(
                token.string, forbidden_words
            )
            assert not violations, (
                path, token.start, token.string, violations,
                'neutral seam vocabulary: no product, provider,'
                ' sibling-seam, orchestration, or delivery term anywhere'
                ' in %s, docstrings and comments included' % package,
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                values_scanned += 1
                violations = _neutral_vocabulary_violations(
                    node.value, forbidden_words
                )
                assert not violations, (
                    path, (node.lineno, node.col_offset), node.value,
                    violations,
                    'neutral seam vocabulary (decoded string value): no'
                    ' product, provider, sibling-seam, orchestration, or'
                    ' delivery term may be assembled by implicit'
                    ' concatenation or an escape sequence',
                )
    assert tokens_scanned > 0, (
        '%s vocabulary scan saw no tokens' % package
    )
    assert values_scanned > 0, (
        '%s vocabulary scan saw no string values' % package
    )


_scan_neutral_vocabulary(
    'durable_execution', durable_execution_files,
    ('durable_execution/contract.py', 'durable_execution/__init__.py'),
    DURABLE_EXECUTION_FORBIDDEN_WORDS,
)

# 2e. The same neutral vocabulary pin for the one-shot capability
# seam, with ONE word removed from the set: `workflow`. The seam's
# three calls are the production call graph with the bound directory
# removed, and `workflow_id` is the binding field every token is
# keyed on; the contract cannot state its own binding without naming
# it. Every other word stays forbidden, and so does every joined
# phrase (so `workflowauthority` is still caught).
CAPABILITY_FORBIDDEN_WORDS = DURABLE_EXECUTION_FORBIDDEN_WORDS - {'workflow'}
assert 'workflow' in DURABLE_EXECUTION_FORBIDDEN_WORDS
assert len(CAPABILITY_FORBIDDEN_WORDS) == len(DURABLE_EXECUTION_FORBIDDEN_WORDS) - 1
_scan_neutral_vocabulary(
    'capability', capability_files,
    ('capability/contract.py', 'capability/__init__.py'),
    CAPABILITY_FORBIDDEN_WORDS,
)

# 2g. The same neutral vocabulary pin for the worker seam, with NO
# word removed. The contract names its inputs `record`, `workspace`
# and `now`, and the lease-ending operation is `relinquish_workspace`
# rather than a delivery word, so the full durable-execution set
# applies unchanged. The equality is asserted explicitly so a later
# carve-out (`release` above all, which is delivery vocabulary the
# guard exists to keep out of a neutral seam) cannot be added
# silently.
WORKER_FORBIDDEN_WORDS = DURABLE_EXECUTION_FORBIDDEN_WORDS
assert WORKER_FORBIDDEN_WORDS == DURABLE_EXECUTION_FORBIDDEN_WORDS
assert 'release' in WORKER_FORBIDDEN_WORDS
_scan_neutral_vocabulary(
    'worker', worker_files,
    ('worker/contract.py', 'worker/__init__.py'),
    WORKER_FORBIDDEN_WORDS,
)

# 2f. Production wiring of the capability seam, STATIC. Inside
# target_runtime/, the persistence module `target_runtime.capability`
# is imported by exactly ONE production module, the adapter, and the
# three seam operations attributed to it (`mint`,
# `validate_and_consume`, `compact`) are called through that module
# object ONLY there. `broker.py` and `runtime.py` reach them solely
# through the Broker's bound seam instance. The import is matched on
# the AST (module name plus alias name), never on a substring, because
# `target_runtime.capability_authority` shares the prefix. Anti-vacuity:
# the adapter must carry exactly one such call per operation, and the
# two wired modules must carry exactly the production call counts.
CAPABILITY_ADAPTER_FILE = 'target_runtime/capability_authority.py'
CAPABILITY_SEAM_OPERATIONS = ('mint', 'validate_and_consume', 'compact')
capability_module_callers = {}
capability_seam_callers = {}
for path in target_runtime_files:
    relpath = path.relative_to(R).as_posix()
    tree = ast.parse(path.read_text())
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'target_runtime':
            for alias in node.names:
                if alias.name == 'capability':
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and (
            node.module == 'target_runtime.capability'
        ):
            aliases.add('<from-import>')
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'target_runtime.capability':
                    aliases.add(alias.asname or 'target_runtime')
    if relpath == 'target_runtime/capability.py':
        assert not aliases, (relpath, 'the persistence module imports itself')
        continue
    if aliases:
        capability_module_callers[relpath] = sorted(aliases)
    through_module = {}
    through_seam = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in CAPABILITY_SEAM_OPERATIONS
        ):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in aliases:
            through_module[node.func.attr] = (
                through_module.get(node.func.attr, 0) + 1
            )
        elif isinstance(owner, ast.Attribute) and (
            owner.attr == 'capability_authority'
        ):
            through_seam[node.func.attr] = through_seam.get(node.func.attr, 0) + 1
    if through_module:
        assert relpath == CAPABILITY_ADAPTER_FILE, (
            relpath, through_module,
            'a capability operation is called on the persistence module'
            ' outside the adapter',
        )
    if through_seam:
        capability_seam_callers[relpath] = through_seam
    if relpath == CAPABILITY_ADAPTER_FILE:
        assert through_module == {
            'mint': 1, 'validate_and_consume': 1, 'compact': 1,
        }, (relpath, through_module)
assert capability_module_callers == {
    CAPABILITY_ADAPTER_FILE: ['capability_module'],
}, capability_module_callers
assert capability_seam_callers == {
    'target_runtime/broker.py': {'validate_and_consume': 1},
    'target_runtime/runtime.py': {'mint': 2, 'compact': 1},
}, capability_seam_callers

# 2h. Production wiring of the worker seam, STATIC, on the AST. Call
# nodes only, never text occurrences, so an attribute READ of a
# `PROBLEM_*` constant on `workspace` or `workspace_trust` stays legal
# anywhere while a CALL of a host operation does not. Inside
# target_runtime/:
#   - the host operations behind the seam (`workspace.materialize`,
#     `.verify_leased_workspace`, `.release`; `workspace_trust
#     .establish`, `.revoke`, `.resolve_config_path`, `.is_trusted`)
#     are called ONLY in the adapter, exactly once each;
#   - `workspace_trust.default_config_path` is called in the adapter
#     and in `cli.py` (which resolves the production configuration
#     path for `_build_broker`; not a seam call), once each;
#   - `workspace_trust.trust_key` is deliberately NOT restricted: it
#     is a pure helper for the ownership predicate, not a seam
#     operation;
#   - `workspace_ownership.production_close` is never CALLED by name
#     anywhere, and `cli.py` remains the only module that references
#     it, exactly once (the hand-in to the Broker);
#   - the Broker reaches the nine seam operations and the two
#     presence facts through its bound `worker` attribute with EXACT
#     counts (`close_workspace` is handed on as `close_fn`, never
#     called in the Broker), and no other module reaches them at all;
#   - the adapter is the only module that defines the two production
#     host readers, and `broker.py` keeps no copy, no re-export, and
#     no `_trust_still_consumable`;
#   - the reference implementation is constructed exactly once, inside
#     the Broker constructor.
# Both module aliases (`from target_runtime import workspace as X`,
# `import target_runtime.workspace as X`) and from-imported names
# (`from target_runtime.workspace_trust import default_config_path`)
# are resolved per file on the AST, never by substring. Anti-vacuity:
# every expected count is non-zero and asserted by equality, and the
# adapter file must be in the scanned set.
WORKER_ADAPTER_FILE = 'target_runtime/worker.py'
WORKER_BROKER_FILE = 'target_runtime/broker.py'
WORKER_CLI_FILE = 'target_runtime/cli.py'
WORKER_SEAM_OPERATIONS = (
    'materialize_workspace', 'verify_workspace', 'relinquish_workspace',
    'establish_workspace_trust', 'workspace_trust_consumable',
    'revoke_workspace_trust', 'probe_readiness', 'live_workspaces',
    'close_workspace',
)
WORKER_SEAM_PRESENCE = ('observes_live_workspaces', 'closes_workspaces')
WORKER_HOST_OPERATIONS = {
    'workspace': ('materialize', 'verify_leased_workspace', 'release'),
    'workspace_trust': (
        'establish', 'revoke', 'resolve_config_path', 'is_trusted',
        'default_config_path',
    ),
}
WORKER_EXPECTED_HOST_CALLS = {
    WORKER_ADAPTER_FILE: {
        ('workspace', 'materialize'): 1,
        ('workspace', 'verify_leased_workspace'): 1,
        ('workspace', 'release'): 1,
        ('workspace_trust', 'establish'): 1,
        ('workspace_trust', 'revoke'): 1,
        ('workspace_trust', 'resolve_config_path'): 2,
        ('workspace_trust', 'is_trusted'): 1,
        ('workspace_trust', 'default_config_path'): 1,
    },
    WORKER_CLI_FILE: {('workspace_trust', 'default_config_path'): 1},
}
WORKER_EXPECTED_BROKER_REFERENCES = {
    'verify_workspace': 5, 'materialize_workspace': 1,
    'relinquish_workspace': 1, 'establish_workspace_trust': 1,
    'workspace_trust_consumable': 1, 'revoke_workspace_trust': 1,
    'probe_readiness': 1, 'live_workspaces': 3, 'close_workspace': 1,
    'observes_live_workspaces': 2, 'closes_workspaces': 2,
}
WORKER_MOVED_READERS = (
    '_production_readiness_probe', '_production_live_workspaces',
)
assert all(WORKER_EXPECTED_BROKER_REFERENCES.values())
assert all(
    count for counts in WORKER_EXPECTED_HOST_CALLS.values()
    for count in counts.values()
)
assert any(
    p.relative_to(R).as_posix() == WORKER_ADAPTER_FILE
    for p in target_runtime_files
), 'target_runtime scan lost the worker adapter'
worker_host_callers = {}
worker_seam_referrers = {}
worker_seam_calls_in_broker = {}
worker_close_references = {}
worker_close_calls = 0
worker_reader_definitions = {}
worker_constructions = {}
for path in target_runtime_files:
    relpath = path.relative_to(R).as_posix()
    tree = ast.parse(path.read_text())
    module_aliases = {}
    from_names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == 'target_runtime':
                for alias in node.names:
                    if alias.name in WORKER_HOST_OPERATIONS:
                        module_aliases[alias.asname or alias.name] = (
                            alias.name
                        )
            elif node.module and node.module.startswith('target_runtime.'):
                short = node.module.split('.', 1)[1]
                if short in WORKER_HOST_OPERATIONS:
                    for alias in node.names:
                        from_names[alias.asname or alias.name] = (
                            short, alias.name,
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith('target_runtime.'):
                    short = alias.name.split('.', 1)[1]
                    if short in WORKER_HOST_OPERATIONS and alias.asname:
                        module_aliases[alias.asname] = short
    if relpath in ('target_runtime/workspace.py',
                   'target_runtime/workspace_trust.py'):
        own = relpath.rsplit('/', 1)[1][:-3]
        assert own not in module_aliases.values(), (
            relpath, 'a host module imports itself'
        )
    host_calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name in WORKER_MOVED_READERS or (
                node.name == '_trust_still_consumable'
            ):
                worker_reader_definitions.setdefault(
                    relpath, []
                ).append(node.name)
            continue
        if isinstance(node, ast.Attribute):
            owner = node.value
            if isinstance(owner, ast.Attribute) and owner.attr == 'worker':
                if node.attr in WORKER_SEAM_OPERATIONS + WORKER_SEAM_PRESENCE:
                    counts = worker_seam_referrers.setdefault(relpath, {})
                    counts[node.attr] = counts.get(node.attr, 0) + 1
            if node.attr == 'production_close':
                worker_close_references[relpath] = (
                    worker_close_references.get(relpath, 0) + 1
                )
        if isinstance(node, ast.Name) and node.id == 'production_close':
            worker_close_references[relpath] = (
                worker_close_references.get(relpath, 0) + 1
            )
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id in module_aliases:
                module = module_aliases[owner.id]
                if func.attr in WORKER_HOST_OPERATIONS[module]:
                    key = (module, func.attr)
                    host_calls[key] = host_calls.get(key, 0) + 1
            if isinstance(owner, ast.Attribute) and owner.attr == 'worker':
                if relpath == WORKER_BROKER_FILE and (
                    func.attr in WORKER_SEAM_OPERATIONS
                ):
                    worker_seam_calls_in_broker[func.attr] = (
                        worker_seam_calls_in_broker.get(func.attr, 0) + 1
                    )
            if func.attr == 'production_close':
                worker_close_calls += 1
        elif isinstance(func, ast.Name):
            if func.id in from_names:
                module, name = from_names[func.id]
                if name in WORKER_HOST_OPERATIONS[module]:
                    key = (module, name)
                    host_calls[key] = host_calls.get(key, 0) + 1
            if func.id == 'production_close':
                worker_close_calls += 1
            if func.id == 'RuntimeWorker':
                worker_constructions[relpath] = (
                    worker_constructions.get(relpath, 0) + 1
                )
    if host_calls:
        worker_host_callers[relpath] = host_calls
assert worker_host_callers == WORKER_EXPECTED_HOST_CALLS, (
    'a host operation behind the worker seam is called outside the'
    ' adapter, or the adapter no longer carries exactly one delegation'
    ' per operation', worker_host_callers,
)
assert worker_seam_referrers == {
    WORKER_BROKER_FILE: WORKER_EXPECTED_BROKER_REFERENCES,
}, (
    'the Broker seam reference counts changed, or a module other than'
    ' the Broker reaches the worker seam', worker_seam_referrers,
)
assert 'close_workspace' not in worker_seam_calls_in_broker, (
    'the Broker calls close_workspace itself instead of handing it to'
    ' the proof as close_fn', worker_seam_calls_in_broker,
)
assert worker_close_calls == 0, (
    'production_close is called by name; it must only be handed in'
)
assert worker_close_references == {WORKER_CLI_FILE: 1}, (
    'production_close is referenced somewhere other than the one'
    ' hand-in in cli.py', worker_close_references,
)
assert worker_reader_definitions == {
    WORKER_ADAPTER_FILE: list(WORKER_MOVED_READERS),
}, (
    'the production host readers must be defined exactly once, in the'
    ' adapter, and _trust_still_consumable must not return to the'
    ' Broker', worker_reader_definitions,
)
broker_tree = ast.parse((R / WORKER_BROKER_FILE).read_text())
for node in ast.walk(broker_tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            assert alias.name not in WORKER_MOVED_READERS, (
                WORKER_BROKER_FILE, alias.name,
                'the Broker re-exports a moved host reader',
            )
broker_init = next(
    node for node in ast.walk(broker_tree)
    if isinstance(node, ast.FunctionDef) and node.name == '__init__'
    and any(arg.arg == 'store_directory' for arg in node.args.args)
)
assert worker_constructions == {WORKER_BROKER_FILE: 1}, worker_constructions
assert any(
    isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    and node.func.id == 'RuntimeWorker'
    for node in ast.walk(broker_init)
), 'RuntimeWorker is constructed outside the Broker constructor'

# 3. Behavioral: importing the gateway, the Telegram adapter, the
# workflow authority layer, and their entry scripts must not load any
# herdr/herdctl module. The same probe asserts that none of them loads
# any module named target_runtime (or a submodule of it): the Runtime
# must never be reachable from the control chain. target_runtime/
# EXISTS (I4), so this half of the probe is LOAD-BEARING: adding a
# target_runtime import anywhere in the control chain fails this
# suite (verified by mutant P1 in the I4 review).
probe = subprocess.run(
    [
        sys.executable,
        '-c',
        (
            'import sys\n'
            'import codex_gateway, codexgw\n'
            'import codex_gateway.role_turn\n'
            'import telegram_operator, tgop\n'
            'import telegram_operator.adapter, telegram_operator.cli\n'
            'import telegram_operator.launchagent\n'
            'import workflow_authority\n'
            'import workflow_authority.record, workflow_authority.store\n'
            'import workflow_authority.digest, workflow_authority.migrate\n'
            'import workflow_authority.authorization\n'
            'import workflow_authority.canonical\n'
            'import workflow_authority.rendering\n'
            'import operator_session\n'
            'import operator_session.session\n'
            'import operator_session.codex\n'
            'import human_interaction\n'
            'import human_interaction.contract\n'
            'import telegram_operator.interaction\n'
            'import durable_execution\n'
            'import durable_execution.contract\n'
            'import capability\n'
            'import capability.contract\n'
            'import worker\n'
            'import worker.contract\n'
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name == "herdctl" or name == "herdr" or name.startswith("herdr.")\n'
            '    or name == "target_runtime" or name.startswith("target_runtime.")\n'
            '    or name == "pr_delivery" or name.startswith("pr_delivery.")\n'
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

# 3b. Discriminating behavioral probe for the neutral seam. The probe
# above loads the whole control chain in ONE interpreter, so it cannot
# tell whether durable_execution pulled in a control-chain package
# itself: those packages are in sys.modules either way. This SECOND,
# SEPARATE interpreter imports ONLY durable_execution and its contract
# module, then asserts that no module whose root is any forbidden root
# (the static set from 1b, transport seam included) was loaded. The
# probe above stays as it is; this one is the discriminating half.
neutral_probe = subprocess.run(
    [
        sys.executable,
        '-c',
        (
            'import sys\n'
            'import durable_execution\n'
            'import durable_execution.contract\n'
            'roots = ' + repr(sorted(DURABLE_EXECUTION_FORBIDDEN_IMPORT_ROOTS)) + '\n'
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name.split(".")[0] in roots\n'
            ')\n'
            'print("\\n".join(bad))\n'
            'sys.exit(1 if bad else 0)\n'
        ),
    ],
    cwd=str(R),
    capture_output=True,
    text=True,
)
assert neutral_probe.returncode == 0, (
    'durable_execution alone loaded a forbidden root',
    neutral_probe.returncode, neutral_probe.stdout, neutral_probe.stderr,
)

# 3c. The same discriminating probe for the capability seam: a
# separate interpreter imports ONLY capability and its contract
# module, then asserts that no module whose root is any forbidden
# root (the static set from 1c, sibling seam and transport seam
# included) was loaded.
capability_probe = subprocess.run(
    [
        sys.executable,
        '-c',
        (
            'import sys\n'
            'import capability\n'
            'import capability.contract\n'
            'roots = ' + repr(sorted(CAPABILITY_FORBIDDEN_IMPORT_ROOTS)) + '\n'
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name.split(".")[0] in roots\n'
            ')\n'
            'print("\\n".join(bad))\n'
            'sys.exit(1 if bad else 0)\n'
        ),
    ],
    cwd=str(R),
    capture_output=True,
    text=True,
)
assert capability_probe.returncode == 0, (
    'capability alone loaded a forbidden root',
    capability_probe.returncode, capability_probe.stdout,
    capability_probe.stderr,
)

# 3d. The same discriminating probe for the worker seam: a separate
# interpreter imports ONLY worker and its contract module, then
# asserts that no module whose root is any forbidden root (the static
# set from 1d, both sibling seams and the transport seam included)
# was loaded.
worker_probe = subprocess.run(
    [
        sys.executable,
        '-c',
        (
            'import sys\n'
            'import worker\n'
            'import worker.contract\n'
            'roots = ' + repr(sorted(WORKER_FORBIDDEN_IMPORT_ROOTS)) + '\n'
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name.split(".")[0] in roots\n'
            ')\n'
            'print("\\n".join(bad))\n'
            'sys.exit(1 if bad else 0)\n'
        ),
    ],
    cwd=str(R),
    capture_output=True,
    text=True,
)
assert worker_probe.returncode == 0, (
    'worker alone loaded a forbidden root',
    worker_probe.returncode, worker_probe.stdout, worker_probe.stderr,
)

# --- 2i. P1-A6 Verified PR Delivery: the new authority path -------------
# Ten pins over the `pr_delivery/` package, the guard's second path, the
# Mission Authorization boundary, the test fakes, and the CI shape. Each
# is written with the anti-vacuity posture of the sections above: the
# scanned sets are derived, the expected counts are non-zero and asserted
# by equality, and a pin that finds nothing fails.
from pr_delivery import authorization as pr_authorization
from pr_delivery import receipts as pr_receipts
from pr_delivery import transport as pr_transport
from workflow_authority import authorization as wa_authorization
from workflow_authority import digest as wa_digest
from workflow_authority import record as wa_record

pr_delivery_files = sorted(
    p for p in product_files if p.relative_to(R).parts[0] == 'pr_delivery'
)
pr_delivery_names = {p.relative_to(R).as_posix() for p in pr_delivery_files}
PR_DELIVERY_REQUIRED_FILES = {
    'pr_delivery/__init__.py', 'pr_delivery/__main__.py',
    'pr_delivery/authorization.py', 'pr_delivery/boundary.py',
    'pr_delivery/candidate.py', 'pr_delivery/cli.py',
    'pr_delivery/errors.py', 'pr_delivery/machine.py',
    'pr_delivery/pr_text.py', 'pr_delivery/receipts.py',
    'pr_delivery/store.py', 'pr_delivery/transport.py',
}
assert PR_DELIVERY_REQUIRED_FILES <= pr_delivery_names, (
    'pr_delivery scan lost a package file',
    sorted(PR_DELIVERY_REQUIRED_FILES - pr_delivery_names),
)
PR_DELIVERY_TRANSPORT_FILE = 'pr_delivery/transport.py'
PR_DELIVERY_CLI_FILE = 'pr_delivery/cli.py'
PR_DELIVERY_MACHINE_FILE = 'pr_delivery/machine.py'
PR_DELIVERY_RECEIPTS_FILE = 'pr_delivery/receipts.py'
PR_DELIVERY_TEXT_FILE = 'pr_delivery/pr_text.py'


def _docstring_positions_of(tree):
    positions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                positions.add((body[0].value.lineno,
                               body[0].value.col_offset))
    return positions


# (1) Process confinement: `subprocess` only in the transport; no shell,
#     no dynamic import, no environment read, no network module anywhere.
# (2) The real transport is constructed with zero arguments exactly once,
#     in cli.py, and cli.py carries no transport-named option string.
# (3) Verb closure: no merge/tag/release/deploy/reset/rebase/checkout/
#     force/no-verify/delete/mirror literal anywhere in the package.
PR_DELIVERY_FORBIDDEN_LITERALS = {
    '"merge"', "'merge'", '"tag"', "'tag'", '"release"', "'release'",
    '"deploy"', "'deploy'", '"publish"', "'publish'", '"reset"', "'reset'",
    '"rebase"', "'rebase'", '"checkout"', "'checkout'", '"--force"',
    "'--force'", '"--force-with-lease"', "'--force-with-lease'", '"-f"',
    "'-f'", '"--no-verify"', "'--no-verify'", '"--delete"', "'--delete'",
    '"--mirror"', "'--mirror'", '"POST"', "'POST'", '"PUT"', "'PUT'",
    '"PATCH"', "'PATCH'", '"DELETE"', "'DELETE'",
}
PR_DELIVERY_FORBIDDEN_IMPORT_ROOTS = {
    'socket', 'urllib', 'http', 'requests', 'ssl', 'herdr', 'herdctl',
    'target_runtime', 'telegram_operator', 'codex_gateway',
    'operator_session', 'human_interaction', 'durable_execution',
    'capability', 'worker',
}
pr_transport_constructions = {}
pr_new_authorization_calls = {}
pr_reverification_calls = {}
for path in pr_delivery_files:
    relpath = path.relative_to(R).as_posix()
    source = path.read_text()
    assert 'shell=True' not in source, (relpath, 'shell=True')
    tree = ast.parse(source)
    docstring_positions = _docstring_positions_of(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, 'id', getattr(node.func, 'attr', None))
            assert name not in {'system', '__import__', 'import_module',
                                'popen', 'spawn', 'execv', 'execvp'}, (
                relpath, name,
            )
            if name == 'DeliveryTransport':
                assert not node.args and not node.keywords, (
                    relpath, 'DeliveryTransport must take no arguments'
                )
                pr_transport_constructions[relpath] = (
                    pr_transport_constructions.get(relpath, 0) + 1
                )
            if name == 'new_authorization':
                pr_new_authorization_calls[relpath] = (
                    pr_new_authorization_calls.get(relpath, 0) + 1
                )
            if name == 'run_reverification':
                pr_reverification_calls[relpath] = (
                    pr_reverification_calls.get(relpath, 0) + 1
                )
        if isinstance(node, ast.Attribute):
            assert node.attr not in {'environ', 'getenv', 'putenv'}, (
                relpath, node.attr,
            )
        if isinstance(node, ast.Name):
            assert node.id not in {'environ', 'getenv', 'putenv'}, (
                relpath, node.id,
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                roots = [alias.name.split('.')[0] for alias in node.names]
            else:
                assert node.level == 0, (relpath, 'no relative import')
                roots = [(node.module or '').split('.')[0]]
            for root in roots:
                assert root not in PR_DELIVERY_FORBIDDEN_IMPORT_ROOTS, (
                    relpath, root,
                    'pr_delivery imports no network module, no'
                    ' orchestration engine, no control-chain package, and'
                    ' no neutral seam',
                )
                if root == 'subprocess':
                    assert relpath == PR_DELIVERY_TRANSPORT_FILE, (
                        relpath, 'subprocess only in the delivery transport'
                    )
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and (
            token.start not in docstring_positions
        ):
            assert token.string not in PR_DELIVERY_FORBIDDEN_LITERALS, (
                relpath, token.start, token.string,
                'no merge, tag, release, deploy, publish, reset, rebase,'
                ' checkout, force, hook-bypass, or mutating HTTP verb'
                ' literal exists in the delivery package',
            )
            if relpath == PR_DELIVERY_CLI_FILE:
                assert 'transport' not in token.string.lower(), (
                    relpath, token.start,
                    'no transport-named option or config key',
                )
assert pr_transport_constructions == {PR_DELIVERY_CLI_FILE: 1}, (
    'the real delivery transport must be constructed exactly once, in'
    ' cli.py', pr_transport_constructions,
)
assert pr_new_authorization_calls == {PR_DELIVERY_CLI_FILE: 1}, (
    'a PR Delivery Authorization is minted only by the terminal ceremony',
    pr_new_authorization_calls,
)
# (M4) `run_reverification` has exactly one call site, in the machine.
assert pr_reverification_calls == {PR_DELIVERY_MACHINE_FILE: 1}, (
    pr_reverification_calls,
)
assert pr_transport.ALLOWED_GIT_VERBS == (
    'rev-parse', 'symbolic-ref', 'config', 'remote', 'status', 'diff',
    'diff-index', 'diff-tree', 'write-tree', 'ls-remote', 'fetch',
    'merge-base', 'update-index', 'read-tree', 'update-ref', 'commit',
    'push',
), pr_transport.ALLOWED_GIT_VERBS
# update-index is issued in exactly one form, the stat refresh: no
# --cacheinfo/--add/--remove/--force-remove literal exists in the package.
for _forbidden in ('"--cacheinfo"', '"--force-remove"', '"--add"',
                   '"--remove"', '"--index-info"'):
    assert _forbidden not in (R / PR_DELIVERY_TRANSPORT_FILE).read_text(), (
        _forbidden,
    )
# (N1) The verb set is enforced at call time: _git names ALLOWED_GIT_VERBS
# and raises.
_git_function = next(
    node for node in ast.walk(
        ast.parse((R / PR_DELIVERY_TRANSPORT_FILE).read_text())
    )
    if isinstance(node, ast.FunctionDef) and node.name == '_git'
)
assert any(
    isinstance(node, ast.Name) and node.id == 'ALLOWED_GIT_VERBS'
    for node in ast.walk(_git_function)
), '_git must check ALLOWED_GIT_VERBS at call time'
assert any(
    isinstance(node, ast.Raise) for node in ast.walk(_git_function)
), '_git must refuse a verb outside the set'
assert pr_transport.ALLOWED_GH_ARGV == (
    ('pr', 'list'), ('pr', 'create'), ('pr', 'view'),
    ('api', '--method', 'GET'),
), pr_transport.ALLOWED_GH_ARGV
assert pr_transport.CHECK_RUNS_ENDPOINT == 'repos/%s/%s/commits/%s/check-runs'

# (4) Receipt binding closure: the four tuples by exact value, the
#     guard-observable subsets inside them, and the machine constructing
#     each binding with EXACTLY those keys (anti-vacuity: all four found).
assert pr_authorization.BASE_REFRESH_RECEIPT_BINDING_FIELDS == (
    'repository_realpath', 'git_dir_realpath', 'remote_name',
    'remote_url_exact', 'remote_url_fetch', 'source_ref', 'base_ref',
    'old_base_oid', 'new_base_oid', 'fast_forward',
    'base_changed_paths_digest', 'candidate_identity_digest',
)
assert pr_authorization.COMMIT_RECEIPT_BINDING_FIELDS == (
    'repository_realpath', 'git_dir_realpath', 'branch', 'source_ref',
    'head_before', 'staged_sha256', 'candidate_identity_digest',
    'expected_tree_oid', 'committer_name', 'committer_email',
    'message_sha256',
)
assert pr_authorization.PUSH_RECEIPT_BINDING_FIELDS == (
    'repository_realpath', 'remote_name', 'remote_url_exact',
    'remote_url_push', 'source_ref', 'source_commit', 'destination_ref',
    'expected_remote_old_oid', 'candidate_identity_digest',
)
# (B2) The hook compares the URL git hands it against the BOUND push URL.
assert 'remote_url_push' in pr_receipts.LIVE_FACT_FIELDS['PUSH']
assert 'remote_url_exact' in pr_receipts.LIVE_FACT_FIELDS['PUSH']
assert pr_authorization.PR_CREATE_RECEIPT_BINDING_FIELDS == (
    'owner', 'repo', 'remote_url_exact', 'head_branch', 'head_sha',
    'base_branch', 'title_sha256', 'body_sha256',
    'candidate_identity_digest',
)
for step, fields in pr_receipts.LIVE_FACT_FIELDS.items():
    assert set(fields) <= set(pr_authorization.RECEIPT_BINDING_FIELDS[step])
    assert 'repository_realpath' in fields, step
assert set(pr_receipts.LIVE_FACT_FIELDS) == {
    'BASE_REFRESH', 'COMMIT', 'PUSH',
}
machine_tree = ast.parse((R / PR_DELIVERY_MACHINE_FILE).read_text())
binding_shapes_found = set()
for node in ast.walk(machine_tree):
    if not isinstance(node, ast.Dict):
        continue
    keys = {
        key.value for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    if 'candidate_identity_digest' not in keys:
        continue
    matched = [
        step for step, fields in
        pr_authorization.RECEIPT_BINDING_FIELDS.items()
        if keys == set(fields)
    ]
    assert len(matched) == 1, (
        'a binding dict in machine.py does not match exactly one closed'
        ' binding tuple', sorted(keys),
    )
    binding_shapes_found.add(matched[0])
assert binding_shapes_found == set(pr_authorization.STEPS), (
    binding_shapes_found,
)

# (5) Guard wiring: herdr/guards.py imports the receipt module LAZILY
#     inside `_delivery_receipt_decision`, inside a try that catches
#     Exception; `guard_decision` is called exactly once there; the
#     helper is consulted exactly four times (pre-commit 1,
#     reference-transaction 2, pre-push 1); the legacy validators keep
#     their call counts; the pre-tool guard never consults receipts.
guards_tree = ast.parse((R / 'herdr' / 'guards.py').read_text())
for node in guards_tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = [alias.name for alias in node.names]
        module = getattr(node, 'module', None) or ''
        assert 'pr_delivery' not in module and not any(
            name.startswith('pr_delivery') for name in names
        ), 'herdr.guards must not import pr_delivery at module scope'
guard_functions = {
    node.name: node for node in guards_tree.body
    if isinstance(node, ast.FunctionDef)
}
helper = guard_functions['_delivery_receipt_decision']
helper_try = [node for node in ast.walk(helper) if isinstance(node, ast.Try)]
assert len(helper_try) == 1
try_body_nodes = [
    inner for body_node in helper_try[0].body for inner in ast.walk(body_node)
]
assert any(
    isinstance(node, ast.ImportFrom) and node.module == 'pr_delivery'
    and [alias.name for alias in node.names] == ['receipts']
    for node in try_body_nodes
), 'the receipt import must be inside the try body'
assert any(
    isinstance(handler.type, ast.Name) and handler.type.id == 'Exception'
    for handler in helper_try[0].handlers
), 'the helper must catch Exception and return a refusal'


def _call_count(function_node, callee):
    return sum(
        1 for node in ast.walk(function_node)
        if isinstance(node, ast.Call)
        and getattr(node.func, 'id', getattr(node.func, 'attr', None))
        == callee
    )


assert _call_count(helper, 'guard_decision') == 1
assert _call_count(guard_functions['guard_precommit'],
                   '_delivery_receipt_decision') == 1
assert _call_count(guard_functions['guard_reference_transaction'],
                   '_delivery_receipt_decision') == 2
assert _call_count(guard_functions['guard_prepush'],
                   '_delivery_receipt_decision') == 1
assert _call_count(guard_functions['guard_pretool'],
                   '_delivery_receipt_decision') == 0
assert _call_count(guard_functions['guard_precommit'], 'approval_valid') == 1
assert _call_count(guard_functions['guard_reference_transaction'],
                   'approval_valid') == 1
assert _call_count(guard_functions['guard_pretool'], 'approval_valid') == 1
assert _call_count(guard_functions['guard_prepush'],
                   'push_approval_valid') == 1
assert _call_count(guard_functions['guard_pretool'],
                   'push_approval_valid') == 1
guards_calls_total = sum(
    _call_count(function, 'guard_decision')
    for function in guard_functions.values()
)
assert guards_calls_total == 1, guards_calls_total

# (6) Minting boundary: nothing outside pr_delivery/ imports pr_delivery
#     except herdr/guards.py (lazily, above) — not the CLI entry points,
#     not the control chain, not the Runtime, not the neutral seams.
pr_delivery_importers = {}
for path in product_files + sorted((R / 'herdr').glob('*.py')):
    relpath = path.relative_to(R).as_posix()
    if relpath.startswith('pr_delivery/'):
        continue
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        else:
            continue
        if any(name.split('.')[0] == 'pr_delivery' for name in names):
            pr_delivery_importers[relpath] = (
                pr_delivery_importers.get(relpath, 0) + 1
            )
assert pr_delivery_importers == {'herdr/guards.py': 1}, (
    'only the git guard may reach the delivery package',
    pr_delivery_importers,
)

# (7) Mission Authorization separation: the workflow record's key set,
#     the Mission Authorization's key set, and the policy record are
#     byte-for-byte what they were; pr_delivery takes only named
#     helpers from workflow_authority and never touches its store or
#     record mutators; workflow_authority never imports pr_delivery.
assert wa_record._TOP_LEVEL_KEYS == (
    'schema_version', 'workflow_id', 'human_intent', 'control_identity',
    'target', 'approved_baseline', 'mission_authorization', 'telegram',
    'approval', 'handoff', 'phase', 'workspace_lease', 'receipts',
    'codex_turns', 'ambiguity', 'target_engine', 'verified_result',
    'result_placeholder', 'result_delivery', 'last_observation',
    'delivery_authority',
), wa_record._TOP_LEVEL_KEYS
assert wa_authorization.ALLOWED_AUTHORIZATION_KEYS == frozenset((
    'objective', 'constraints', 'rules', 'desired_outcome', 'acceptance',
    'unresolved_questions', 'execution_scope', 'control', 'target',
    'issue_or_pr', 'baseline', 'handoff', 'telegram_approval',
    'workflow_id', 'human_intent', 'revision', 'delivery_authority',
))
assert wa_digest.EFFECTIVE_POLICY_RECORD == {
    'policy_version': 1,
    'delivery_authority': 'none',
    'policy_documents': ['AGENTS.md', 'OPERATOR_PROTOCOL.md'],
}
assert wa_record.DELIVERY_AUTHORITY_NONE == 'none'
PR_DELIVERY_ALLOWED_WA_IMPORTS = {
    'workflow_authority': {'canonical'},
    'workflow_authority.canonical': set(),
    'workflow_authority.digest': {
        'json_digest', 'framed_digest', 'sha256_hex', 'text_digest',
    },
    'workflow_authority.record': {
        'MAX_ID_CHARS', 'WORKFLOW_ID_ALPHABET',
        'baseline_ref_grammar_problem', 'path_character_problem',
    },
    'workflow_authority.store': {
        'atomic_write_json', 'default_store_dir', 'exclusive_store_lock',
    },
}
for path in pr_delivery_files:
    relpath = path.relative_to(R).as_posix()
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
            node.module or ''
        ).startswith('workflow_authority'):
            allowed = PR_DELIVERY_ALLOWED_WA_IMPORTS.get(node.module)
            assert allowed is not None, (relpath, node.module)
            for alias in node.names:
                assert alias.name in allowed, (
                    relpath, node.module, alias.name,
                    'pr_delivery takes only named, non-mutating helpers'
                    ' from workflow_authority',
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith('workflow_authority'), (
                    relpath, alias.name, 'import the named helpers only'
                )
    docstring_positions = _docstring_positions_of(tree)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            assert token.string not in {
                'WorkflowStore', 'WORKFLOWS_FILE_NAME', 'new_record',
                'add_workflow',
            }, (relpath, token.start, token.string)
        elif token.type == tokenize.STRING and (
            token.start not in docstring_positions
        ):
            # Docstring prose may name the file it must never open;
            # a non-docstring literal may not.
            assert 'workflows.json' not in token.string, (
                relpath, token.start,
            )
for path in product_files:
    relpath = path.relative_to(R).as_posix()
    if not relpath.startswith('workflow_authority/'):
        continue
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            module = getattr(node, 'module', None) or ''
            assert 'pr_delivery' not in module and not any(
                name.startswith('pr_delivery') for name in names
            ), (relpath, 'workflow_authority never imports pr_delivery')

# (8) Fake isolation: a separate interpreter importing the machine, the
#     receipts, and the boundary (everything but the transport and the
#     CLI) loads neither `subprocess` nor `pr_delivery.transport` nor any
#     orchestration/Runtime module; and the delivery test modules import
#     no process- or network-starting module themselves.
pr_delivery_probe = subprocess.run(
    [
        sys.executable,
        '-c',
        (
            'import sys\n'
            'import pr_delivery.authorization, pr_delivery.candidate\n'
            'import pr_delivery.store, pr_delivery.receipts\n'
            'import pr_delivery.machine, pr_delivery.boundary\n'
            'import pr_delivery.pr_text\n'
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name in ("subprocess", "pr_delivery.transport",'
            ' "pr_delivery.cli", "socket")\n'
            '    or name.split(".")[0] in ("herdr", "herdctl",'
            ' "target_runtime")\n'
            ')\n'
            'print("\\n".join(bad))\n'
            'sys.exit(1 if bad else 0)\n'
        ),
    ],
    cwd=str(R),
    capture_output=True,
    text=True,
)
assert pr_delivery_probe.returncode == 0, (
    'pr_delivery logic modules loaded a process or orchestration module',
    pr_delivery_probe.returncode, pr_delivery_probe.stdout,
    pr_delivery_probe.stderr,
)
for test_name in ('test_pr_delivery.py', 'test_pr_delivery_guards.py'):
    test_tree = ast.parse((R / 'tests' / test_name).read_text())
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split('.')[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = [(node.module or '').split('.')[0]]
        else:
            continue
        for root in roots:
            assert root not in {'subprocess', 'socket', 'urllib', 'http',
                                'requests', 'ssl'}, (
                test_name, root,
                'the delivery tests start no process and open no socket of'
                ' their own; git runs only through the production transport'
                ' and _hermetic_git, and gh never runs',
            )
    # The GitHub half is structurally replaced: a module either defines
    # or constructs a transport, and then must override `_gh`, or it
    # defines and constructs none and takes its transport from the
    # fixture of a module that does. The same condition applies to every
    # file; no file is exempted by name (round-01 N7).
    test_source = (R / 'tests' / test_name).read_text()
    defines_transport = any(
        isinstance(node, ast.ClassDef) and any(
            (isinstance(base, ast.Attribute)
             and base.attr == 'DeliveryTransport')
            or (isinstance(base, ast.Name) and base.id == 'DeliveryTransport')
            for base in node.bases
        )
        for node in ast.walk(test_tree)
    )
    constructs_transport = any(
        isinstance(node, ast.Call) and (
            getattr(node.func, 'id', getattr(node.func, 'attr', None))
            == 'DeliveryTransport'
        )
        for node in ast.walk(test_tree)
    )
    if defines_transport or constructs_transport:
        assert 'def _gh(' in test_source, (
            test_name, 'a test transport must override _gh so no gh'
            ' process can start',
        )
    else:
        assert 'DeliveryTransport' not in test_source, (
            test_name, 'a module without its own transport must not name'
            ' the real one',
        )

# (9) PR text hygiene: no provenance or co-author token anywhere in the
#     renderer's literals (decoded values, case-insensitive). The tuple
#     below is a set literal of plain words so the hermetic-git guard
#     does not read it as an argv, and each entry is a whole token, not a
#     substring an ordinary English word could produce.
PR_TEXT_FORBIDDEN_FRAGMENTS = frozenset({
    'co-authored-by', 'generated with', 'claude', 'codex', 'chatgpt',
    'anthropic', 'openai', 'grok', 'gpt-', 'signed-off-by',
})
pr_text_tree = ast.parse((R / PR_DELIVERY_TEXT_FILE).read_text())
pr_text_values = 0
for node in ast.walk(pr_text_tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        pr_text_values += 1
        lowered = node.value.lower()
        for fragment in PR_TEXT_FORBIDDEN_FRAGMENTS:
            assert fragment not in lowered, (
                PR_DELIVERY_TEXT_FILE, node.lineno, fragment,
            )
assert pr_text_values > 10, pr_text_values

# (10) CI shape: the package is compiled and whitespace is checked.
ci_text = (R / '.github' / 'workflows' / 'ci.yml').read_text()
assert 'pr_delivery/*.py' in ci_text, 'CI must compile pr_delivery'
assert 'git diff --check' in ci_text, 'CI must run git diff --check'

print('static tests: OK')
