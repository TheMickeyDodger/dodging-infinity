# Contributing to Dodging Infinity

Thanks for helping improve Dodging Infinity.

Dodging Infinity is built around one core idea: turn unbounded objectives into bounded, isolated, verifiable units of work.

Contributions should preserve that principle even as the system gains new runtimes, models, roles, orchestration strategies, and problem domains.

## Architectural principles

- **Roles are not models.** Supervisor, Lead, Executor, and Reviewer are logical contracts. Do not hard-code a role to a particular provider or model family.
- **Models are replaceable execution engines.** Any Herdr-supported model/runtime should be able to occupy any compatible role.
- **Scope stays bounded.** A Herdr owns its repository, task, rule set, runtime state, and completion criteria.
- **Cross-repository work is delegated.** A parent Herdr should not silently expand its authority into another repository.
- **Completion requires evidence.** Required dependencies, review decisions, tests, and completion gates should fail closed when state is unresolved.
- **Safety boundaries are explicit.** Repository isolation, review constraints, and Git authorization boundaries should remain deterministic where possible.

## Repository structure

Key components include:

- `herdr/control_plane.py` — programmatic orchestration interface
- `herdr/orchestrator.py` — structured child-Herdr orchestration
- `herdr/dependencies.py` — parent/child dependency tracking and completion gates
- `herdr/policy.py` — layered policy and rule resolution
- `herdr/runtime.py` — runtime interaction and prompt settlement
- `herdr/lifecycle.py` — role bootstrap and lifecycle behavior
- `herdr/guards.py` — Git and runtime safety boundaries
- `herdctl.py` — human-facing compatibility and administration CLI
- `roles/` — logical role contracts
- `tests/` — regression coverage

## Local setup

Clone the repository and install the local CLI wrapper:

```bash
git clone https://github.com/TheMickeyDodger/dodging-infinity.git
cd dodging-infinity
./scripts/install.sh
```

## Running the test suite

Run all unit tests:

```bash
status=0
for test_file in tests/test_*.py; do
  if [ "$test_file" = "tests/test_static.py" ]; then
    continue
  fi
  PYTHONPATH="$PWD" python3 "$test_file" || status=$?
done
test "$status" -eq 0
```

Run the static/integration suite:

```bash
PYTHONPATH="$PWD" python3 tests/test_static.py
```

Compile-check the Python sources:

```bash
python3 -m py_compile herdctl.py herdr/*.py tests/*.py
```

## Model and runtime contributions

New models and runtimes are welcome.

Provider-specific behavior should live at the execution boundary rather than leaking into the orchestration contract. A new integration should preserve the ability to replace that model/runtime without rewriting the role or Control Plane architecture.

When adding model/runtime support:

- keep role semantics provider-independent
- isolate provider-specific invocation or settlement behavior
- preserve repository and permission boundaries
- add regression coverage for the new behavior
- document any capabilities or limitations that affect orchestration

## Pull requests

Keep pull requests bounded and explain what problem is being solved, what changed, and how the result was verified.

Before opening a pull request:

- run the unit test suite
- run `tests/test_static.py`
- run the compile check
- run `git diff --check`
- add or update tests for behavior changes
- update documentation when the public interface changes
- update `CHANGELOG.md` for meaningful user-facing changes
- verify that no secrets, credentials, local machine paths, or generated runtime state are included

CI runs on Ubuntu and macOS across supported Python versions for every pull request and push to `main`.

## Compatibility

Where practical, preserve compatibility with existing `herdctl` workflows and initialized repositories. If a breaking change is necessary, include an explicit migration path.

## Licensing

By contributing to Dodging Infinity, you agree that your contributions will be licensed under the Apache License 2.0.
