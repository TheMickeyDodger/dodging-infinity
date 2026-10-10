"""Grok Bot transport adapter (Task 8): a thin vendor adapter over the
current DI contracts. It holds no authority and decides nothing.

A plain-text request from a Grok Bot conversation goes to the Codex
Outer Operator through the operator session seam (source ``grok_bot``);
the Operator authors the Mission proposal; ``local_request.surface``
records it, presents it, applies the relayed approval, reports status
and drives the run, unchanged. ``grok_bot.adapter`` maps one tool per
surface operation; ``grok_bot.framing`` is the Operator turn's outbound
text and its one inbound envelope; ``grok_bot.index`` keeps a repeated
request from proposing a second Mission; ``grok_bot.cli`` is the JSON
command line behind ``grokbot.py``; ``grok_bot.mcp`` maps MCP's tools
messages onto the adapter; and ``grok_bot.server`` is the Streamable HTTP
endpoint, listening on 127.0.0.1 alone (``grokbot.py serve``).
``grok_bot.delivery`` relays the SEPARATE delivery ceremony, pr_delivery's
own ``present-dots`` and ``attest-dots``, unchanged; it is the only module
here that imports ``pr_delivery``, and the adapter loads it lazily, for its
three delivery tools alone.

Approval needs a LOCAL ARMING first (``grok_bot.arming`` says exactly what
carries it and what it depends on): the human runs the
exact arming command ``present`` hands over in Grok Bot's per-command,
user-approved local shell on the DI machine, and the relayed approval must
carry the one-time code it printed. Possession of the MCP bearer token alone
approves nothing.

Limitation, stated and not engineered around: approval through Grok Bot
is an OPERATOR-ATTESTED relay of a separate plain-text "approved" reply.
It is not cryptographically authenticated human identity. Grok Bot gives
DI no signed sender attribution, so DI does not establish who sent the
reply; the surface records that, with its residual risk, as
``operator_attestation_only_not_sender_evidence``, and every adapter
result carries it.
The Grok connection is a transport only: it grants no Mission or
delivery authority, and ``delivery_authority`` is "none" on every result.
A PR Delivery Authorization exists only when pr_delivery's own ceremony
records one, for exactly the displayed delivery proposal, after the
human's separate reply to THAT display (operator-attested in the same
way). An engineering approval never produces one, and nothing here
performs a delivery step, merges, releases or deploys.

Why this adapter does not implement
``human_interaction.contract.HumanInteractionAdapter``: that seam is
built for a polling transport (``receive(cursor)`` waits for inbound
events), while a Grok Bot tool call arrives as a call into DI. Its
``InteractionEvent`` also promises an authenticated ``principal_id`` and
an ``allowed`` verdict that Grok Bot cannot honestly populate. Rather
than fill those fields with values DI cannot stand behind, or weaken
that contract, this adapter bypasses the seam and stays correspondingly
thin.

Second limitation, the Operator turn's launch posture: the ``request``
tool runs ONE FRESH Codex turn through
``operator_session.RestrictedCodexOperatorSession`` ->
``codex_gateway.gateway.submit_restricted`` ->
``codex_gateway.role_turn.run_operator_turn``, whose argv is the role-turn
restrictive posture (``codex exec --json -C <repository realpath>
--sandbox read-only --ignore-user-config --ignore-rules --strict-config -c
approval_policy=never -``), verified on the exact argv before the process
starts; a posture that cannot be verified is refused, never run under
ambient policy. It continues NO session: every ``operator_session_id`` is
refused (``grok_bot_operator_session_refused``) before any provider call,
because that posture is fresh-only by construction
(``verify_restrictive_posture`` refuses any argv carrying ``resume`` or
``fork``), so no continued session could run under it. What the posture
confines, and what it does not: ``--sandbox read-only`` confines WRITES by
the Operator's shell commands; it does not confine READS. Request text can
induce the Operator to read, under its own permissions, any file the
serving user can read, and the Operator's reply is returned to the caller
(``operator_message``). That is a material, disclosed residual: no
exfiltration has been demonstrated here, and nothing here prevents it.
Read-only is not secret isolation, and nothing here scopes readable paths.
"Propose only: do not approve, dispatch, run,
commit or push" remains an INSTRUCTION in the turn's preamble, as does the
neutralization of forged envelopes in the human's text: mitigations, not
boundaries. What DI enforces is on its own side: the adapter records
nothing from that turn but one proposal, through the surface's closed
schema, and approval and dispatch are separate surface calls.

Network: the adapter's own code opens no outbound connection and reads no
environment variable. Its ``present_delivery`` and ``approve_delivery``
tools, however, run pr_delivery's ceremony (``present-dots``,
``attest-dots``; ``delivery_status`` runs neither), which runs the installed git
against the repository: ``git ls-remote`` against the configured remote
and, when the remote base differs from HEAD, ``git fetch`` of the base
branch. With a real remote those can reach an external host, and the fetch
writes local repository data (``docs/grok-bot.md``, ceremony
prerequisites). Only ``grok_bot.server`` binds a socket, on 127.0.0.1
alone, and never without its bearer token: it is REQUIRED (the
endpoint fails closed without one), it is transport access control only,
it is read from an owner-only file the human provisions, it is never
installed by this code, and it approves nothing. Its loopback tests
prove protocol shape only; live Grok Bot compatibility is unverified until
the live acceptance exercise (``grok_bot.server.PUBLIC_REACHABILITY``).
"""
