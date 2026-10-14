"""Cheap, fail-closed delivery gate; run before dependency setup and AI generation."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

from briefing import BEIJING_TZ, ClaimError, GitHubClaim, now_beijing


def decide(env, now, claim_factory=GitHubClaim):
    now = now.astimezone(BEIJING_TZ)
    today = now.date().isoformat()
    event = env.get("GITHUB_EVENT_NAME", "")
    result = {"action": "skip", "not_before": "", "today": today, "reason": "unsupported_event"}
    if event == "push":
        if env.get("RECOVER_DATE") != today:
            return dict(result, action="dry-run", reason="code_check")
    elif event == "workflow_dispatch":
        if env.get("MANUAL_DRY_RUN", "true") != "false":
            return dict(result, action="dry-run", reason="manual_preview")
    elif event == "schedule":
        minute = now.hour * 60 + now.minute
        if not 430 <= minute < 720:  # 07:10 <= Beijing time < 12:00
            return dict(result, reason="outside_morning_window")
        if minute < 450:
            result["not_before"] = "07:30"
    else:
        return result

    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        raise ClaimError("delivery gate requires GitHub credentials")
    sha, state = claim_factory(token, repo, today).read()
    if sha is not None:
        safe_state = state if state in ("sending", "sent", "uncertain") else "unrecognized_record"
        return dict(result, not_before="", reason="existing_" + safe_state)
    return dict(result, action="send", reason="missing_daily_record")


def main():
    try:
        result = decide(os.environ, now_beijing())
    except Exception:
        # Never print HTTP bodies, authentication material, or server exceptions.
        print(json.dumps({"action": "skip", "reason": "delivery_check_failed"}))
        return 1
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            for key in ("action", "not_before"):
                stream.write(f"{key}={result[key]}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
