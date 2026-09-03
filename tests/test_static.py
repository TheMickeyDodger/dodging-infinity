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
# inside the pin the moment it exists.
DURABLE_EXECUTION_ALLOWED_IMPORT_ROOTS = {'abc', 'durable_execution'}
DURABLE_EXECUTION_FORBIDDEN_IMPORT_ROOTS = {
    'target_runtime', 'workflow_authority', 'telegram_operator',
    'codex_gateway', 'operator_session', 'human_interaction',
    'herdr', 'herdctl', 'git_transport',
}
durable_execution_files = sorted(
    p for p in product_files
    if p.relative_to(R).parts[0] == 'durable_execution'
)
assert durable_execution_files, 'durable_execution sources not found'
for path in durable_execution_files:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                path, node.level,
                'durable_execution may not use a relative import',
            )
            imported = [node.module or '']
        else:
            continue
        for name in imported:
            root = name.split('.')[0]
            assert root not in DURABLE_EXECUTION_FORBIDDEN_IMPORT_ROOTS, (
                path, name,
                'durable_execution must not import the substrate, the'
                ' control chain, the orchestration engine, or the'
                ' transport seam',
            )
            assert root in DURABLE_EXECUTION_ALLOWED_IMPORT_ROOTS, (
                path, name,
                'durable_execution imports only abc and its own package',
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


def _neutral_vocabulary_violations(token_text):
    lowered = token_text.lower()
    words = set(re.findall(r'[a-z]+', lowered))
    normalized = re.sub(r'[^a-z0-9]', '', lowered)
    return sorted(words & DURABLE_EXECUTION_FORBIDDEN_WORDS) + [
        phrase for phrase in DURABLE_EXECUTION_FORBIDDEN_PHRASES
        if phrase in normalized
    ]


neutral_scan_files = durable_execution_files
neutral_scan_names = {p.relative_to(R).as_posix() for p in neutral_scan_files}
assert neutral_scan_names, 'neutral vocabulary scan set is empty'
for required_file in (
    'durable_execution/contract.py', 'durable_execution/__init__.py',
):
    assert required_file in neutral_scan_names, (
        'neutral vocabulary scan lost a package file', required_file
    )
neutral_tokens_scanned = 0
neutral_values_scanned = 0
for path in neutral_scan_files:
    source = path.read_text()
    tree = ast.parse(source)
    joined = [node for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]
    assert not joined, (
        path, [(node.lineno, node.col_offset) for node in joined],
        'durable_execution holds plain string literals only: no f-string,'
        ' so no forbidden term can be split across formatted parts',
    )
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        neutral_tokens_scanned += 1
        violations = _neutral_vocabulary_violations(token.string)
        assert not violations, (
            path, token.start, token.string, violations,
            'neutral seam vocabulary: no product, provider, sibling-seam,'
            ' orchestration, or delivery term anywhere in'
            ' durable_execution, docstrings and comments included',
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            neutral_values_scanned += 1
            violations = _neutral_vocabulary_violations(node.value)
            assert not violations, (
                path, (node.lineno, node.col_offset), node.value, violations,
                'neutral seam vocabulary (decoded string value): no product,'
                ' provider, sibling-seam, orchestration, or delivery term'
                ' may be assembled by implicit concatenation or an escape'
                ' sequence',
            )
assert neutral_tokens_scanned > 0, 'neutral vocabulary scan saw no tokens'
assert neutral_values_scanned > 0, 'neutral vocabulary scan saw no string values'

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
            'bad = sorted(\n'
            '    name for name in sys.modules\n'
            '    if name == "herdctl" or name == "herdr" or name.startswith("herdr.")\n'
            '    or name == "target_runtime" or name.startswith("target_runtime.")\n'
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

print('static tests: OK')
