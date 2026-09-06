#!/usr/bin/env python3
"""Rewrite stored continuity messages in the current continuity voice.

A finding keeps the words the model wrote when it was raised, so a change to
the continuity prompt leaves older rows reading the older way. This command
asks the selected model to restate them under the rule the live prompt
states, one call per finding, audited and budget-gated like any other.

Dry run by default: it prints what each message would become and writes
nothing. Add --apply to write.

    .venv/bin/python tools/restate_findings.py
    .venv/bin/python tools/restate_findings.py --apply
    .venv/bin/python tools/restate_findings.py --script "Hedda" --apply

Only open findings are considered, since a dismissed or resolved one is
closed history. By default only those written in the pre-v2 voice are
offered; --all takes every open finding. The original wording is kept on the
row it replaces, so a restatement is visible as one and a second pass reads
the original wording rather than the restated one. A reply that comes back
truncated or too short is refused and the stored message stands.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from ripple.config.secrets import SecretStore
from ripple.db.models import ChangeSet, ContinuityFinding, Script, ScriptUnit
from ripple.db.session import create_db_engine, session_factory
from ripple.extraction.continuity_judge import CONTINUITY_PROMPT_VERSION
from ripple.llm import ProviderError, get_provider
from ripple.services.settings import SettingsService
from ripple.services.spend import BudgetExceeded
from ripple.services.synthesizer import reads_as_argued, restate_message

REPO = Path(__file__).resolve().parent.parent
logger = logging.getLogger("restate")


def stale_findings(
    session, script: str | None = None, every: bool = False
) -> list[tuple[ContinuityFinding, Script]]:
    """The open findings this command would restate, with their scripts.

    `script` matches a title case-insensitively or an id exactly. `every`
    takes each open finding rather than only the ones reading as argued.
    """
    rows = session.execute(
        select(ContinuityFinding, Script)
        .join(ChangeSet, ChangeSet.id == ContinuityFinding.change_set_id)
        .join(Script, Script.id == ChangeSet.script_id)
        .where(ContinuityFinding.status == "open")
        .order_by(Script.title, ContinuityFinding.created_at)
    ).all()
    chosen = []
    for finding, owner in rows:
        if script and script.lower() not in owner.title.lower():
            if script != str(owner.id):
                continue
        # A row restated once already carries its original; the message on it
        # is the current voice unless a truncated reply left a fragment.
        payload = finding.payload_json or {}
        done = payload.get("restated_under") == CONTINUITY_PROMPT_VERSION
        if done and not every:
            continue
        source = payload.get("restated_from") or finding.message
        if every or reads_as_argued(source):
            chosen.append((finding, owner))
    return chosen


def cited_lines(session, finding: ContinuityFinding) -> list[str]:
    """The script lines the finding cites, in evidence order, each once."""
    lines: list[str] = []
    for evidence in finding.evidence:
        if evidence.script_unit_id is None:
            continue
        unit = session.get(ScriptUnit, evidence.script_unit_id)
        if unit is not None and unit.current_text not in lines:
            lines.append(unit.current_text)
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="restate_findings",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the restatements (default: dry run)"
    )
    parser.add_argument(
        "--all",
        dest="every",
        action="store_true",
        help="take every open finding, not only the ones reading as argued",
    )
    parser.add_argument(
        "--script", help="limit to one script, by title substring or id"
    )
    parser.add_argument(
        "--database",
        default="sqlite+pysqlite:///data/ripple.db",
        help="database URL (default: the local file the app uses)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # The provider's own request log is noise in a report of what changed.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    SecretStore().load()
    engine = create_db_engine(args.database)
    with session_factory(engine)() as session:
        provider_name, model_id = SettingsService().selected_model(session)
        if not provider_name or not model_id:
            logger.error("No model is selected. Choose one in Settings first.")
            return 1
        provider = get_provider(provider_name)

        targets = stale_findings(session, args.script, args.every)
        if not targets:
            logger.info("Nothing to restate.")
            return 0
        logger.info(
            "%d finding(s) to restate with %s%s",
            len(targets),
            model_id,
            "" if args.apply else " (dry run)",
        )

        written = kept = 0
        for finding, script in targets:
            payload = dict(finding.payload_json or {})
            original = payload.get("restated_from") or finding.message
            try:
                restated = restate_message(
                    original,
                    cited_lines(session, finding),
                    provider,
                    model_id,
                    session=session,
                    script_id=script.id,
                )
            except BudgetExceeded as error:
                logger.error("%s", error.message)
                session.rollback()
                return 1
            except ProviderError as error:
                logger.error("  %s: %s", script.title, error.message)
                kept += 1
                continue
            logger.info("\n  %s", script.title)
            logger.info("  was: %s", original)
            if restated is None:
                logger.info("  now: (nothing usable came back; the message stands)")
                kept += 1
                continue
            logger.info("  now: %s", restated)
            written += 1
            if args.apply:
                payload["restated_from"] = original
                payload["restated_under"] = CONTINUITY_PROMPT_VERSION
                finding.payload_json = payload
                finding.message = restated

        if args.apply:
            session.commit()
            logger.info("\n%d restated, %d left as written", written, kept)
        else:
            session.rollback()
            logger.info("\nDry run: nothing written. Add --apply to write.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
