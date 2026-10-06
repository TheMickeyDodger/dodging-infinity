"""Test helper (not a test module): ONE Runtime delivery maintenance pass in
a FRESH process over existing stores (Task 8 S-VI, the S6-3 crash-ownership
regressions in ``test_mission_delivery``).

It composes exactly what production composes — the Mission gate
(``mission_control.gate.production_gate``) and the Mission delivery driver
(``mission_control.delivery.production_delivery``: the REAL delivery
transport and machine through ``pr_delivery.cli.build_machine``) — and runs
``TargetBroker.maintain(workflow_id, MAINTAIN_DELIVERY)`` over the real
Runtime git transport, at the FIXED clock the calling test passes.

Optional fault ``die_after_intent``: the process dies (``os._exit(137)``)
right after the owned-child ledger's fsynced INTENT row for that preparation
effect is written — an owner death between the intent and the spawn.

Usage: python3 -B tests/_delivery_pass.py '<json config>'; prints one JSON
line with the Broker outcome.
"""

import json
import os
import sys


def main(config):
    sys.path[:0] = [config["root"], os.path.join(config["root"], "tests")]
    from mission_control import delivery as delivery_module
    from mission_control import gate as gate_module
    from pr_delivery import transport as transport_module
    from target_runtime import broker as broker_module
    from target_runtime.git_transport import GitTransport

    def clock():
        return config["now"]
    fault = config.get("die_after_intent")
    if fault:
        real = transport_module.DeliveryTransport._ledger_append

        def append(self, key, row):
            real(self, key, row)
            if isinstance(row.get("intent"), str) and row.get("effect") == fault:
                os._exit(137)
        transport_module.DeliveryTransport._ledger_append = append
    gate = gate_module.production_gate(config["mission_dir"], config["principal"],
                                       clock=clock)
    delivery = delivery_module.production_delivery(
        gate, config["store_dir"], config["delivery_dir"], clock=clock)
    broker = broker_module.TargetBroker(
        store_directory=config["store_dir"],
        control_repository_realpath=config["control"],
        transport=GitTransport(),
        workspaces_root=config["workspaces"],
        role_turn_fn=None,
        claude_config_path=config["claude_config"],
        clock=clock,
        mission_gate=gate,
        mission_delivery=delivery,
        delivery_store_directory=config["delivery_dir"],
    )
    outcome = broker.maintain(config["workflow_id"], broker_module.MAINTAIN_DELIVERY)
    sys.stdout.write(json.dumps({"ok": outcome.ok, "outcome": outcome.outcome,
                                 "problem": outcome.problem,
                                 "detail": outcome.detail}) + "\n")


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
