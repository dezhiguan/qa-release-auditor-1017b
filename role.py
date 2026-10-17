import json
import re
from typing import Dict, Any, List


def _parse_repo(text: str) -> str:
    # Match "仓库 <slug>" or "repo <slug>" — allow Chinese/ASCII delimiters: 、，。；
    # Also support bare slug after colon or equal sign, e.g. "repo=qa-release-checker-1017"
    # Reject jobId-like (DF-\d+) or pure digits or empty
    patterns = [
        r"(?:仓库|repo)[\s：:＝=]+([a-zA-Z0-9][a-zA-Z0-9\-]{0,38}/[a-zA-Z0-9][a-zA-Z0-9\-]{0,38}|[a-zA-Z0-9][a-zA-Z0-9\-]{1,78})[\s\.,;：：；、，。\)]?",
        r"(?:repo|仓库)=([a-zA-Z0-9][a-zA-Z0-9\-]{0,38}/[a-zA-Z0-9][a-zA-Z0-9\-]{0,38}|[a-zA-Z0-9][a-zA-Z0-9\-]{1,78})",
        r"(?:repo|仓库):([a-zA-Z0-9][a-zA-Z0-9\-]{0,38}/[a-zA-Z0-9][a-zA-Z0-9\-]{0,38}|[a-zA-Z0-9][a-zA-Z0-9\-]{1,78})"
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            candidate = m.group(1).strip()
            if re.fullmatch(r"DF-\d+", candidate) or candidate.isdigit() or not candidate:
                continue
            return candidate
    return None


def _parse_runid(text: str) -> str:
    # Match 'runId <digits>', 'CI runId <digits>', 'runId=<digits>', 'runId：<digits>'
    # Allow Chinese/ASCII delimiters: 、，。；：：＝=
    # Exclude matches inside PR context (e.g., '#123456')
    patterns = [
        r"(?i)(?:CI\s+)?runId[\s：:＝=]+(\d+)(?:[\s\.,;：；、，。\)]?)",
        r"(?i)(?:CI\s+)?runId=(\d+)",
        r"(?i)(?:CI\s+)?runId：(\d+)"
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            # Guard against misreading PR number (e.g., 'PR #123456' → '123456' as runId)
            start, end = m.span(1)
            pre_context = text[max(0, start-10):start]
            if '#' in pre_context or re.search(r'#\s*\d+$', pre_context):
                continue
            return m.group(1)
    return None


def _parse_pr_number(text: str) -> str:
    # Match 'PR #<digits>', 'PR <digits>', '#<digits>', 'PR：<digits>', 'PR=<digits>'
    # Allow Chinese/ASCII delimiters: 、，。；：：＝=
    # Avoid overlapping with runId by requiring clear PR context or # prefix
    patterns = [
        r"(?i)(?:PR[\s：:＝=]+#|PR[\s：:＝=]+|#)(\d+)(?:[\s\.,;：；、，。\)]?)",
        r"(?i)PR[\s：:＝=]+(\d+)(?:[\s\.,;：；、，。\)]?)",
        r"(?i)PR=(\d+)",
        r"(?i)PR：(\d+)"
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _parse_jobid(text: str) -> str:
    # Match '任务 DF-\d+', 'jobId DF-\d+', 'DF-\d+', 'jobId=<DF-\d+>', '任务：<DF-\d+>'
    # Allow Chinese/ASCII delimiters: 、，。；：：＝=
    patterns = [
        r"(?:任务|jobId)[\s：:＝=]+(DF-\d+)(?:[\s\.,;：；、，。\)]?)",
        r"(?:任务|jobId)=(DF-\d+)",
        r"(?:任务|jobId)：(DF-\d+)",
        r"\b(DF-\d+)\b"
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            candidate = m.group(1) if len(m.groups()) >= 1 else m.group(0)
            if re.fullmatch(r"DF-\d+", candidate):
                return candidate
    return None


async def execute(data: dict, ctx) -> dict:
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED"}

    repo = _parse_repo(text)
    runId = _parse_runid(text)
    number_str = _parse_pr_number(text)
    jobId = _parse_jobid(text)

    # Validate all four identifiers are present and non-empty
    missing = []
    if not isinstance(repo, str) or not repo.strip():
        missing.append("repo")
    if not isinstance(runId, str) or not runId.strip():
        missing.append("runId")
    if not isinstance(number_str, str) or not number_str.strip():
        missing.append("PR number")
    if not isinstance(jobId, str) or not jobId.strip():
        missing.append("jobId")
    if missing:
        return {"decision": "BLOCKED"}

    evidence_sources = []
    steps = []
    delegate_results = []

    # devflow.job.read
    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=jobId)
        if not isinstance(job_result, list):
            raise TypeError("devflow.job.read must return list")
        evidence_sources.append({"tool": "devflow.job.read", "result": job_result})
        steps.append(f"devflow.job.read(jobId='{jobId}') → {len(job_result)} record(s)")
        if len(job_result) == 0:
            return {"decision": "BLOCKED"}
    except Exception:
        return {"decision": "BLOCKED"}

    # ci.log.fetch
    try:
        ci_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(ci_result, dict):
            raise TypeError("ci.log.fetch must return dict")
        log_val = ci_result.get("log")
        if not isinstance(log_val, str) or len(log_val.strip()) == 0 or ci_result.get("logsUnavailable") is True:
            return {"decision": "BLOCKED"}
        evidence_sources.append({"tool": "ci.log.fetch", "result": ci_result})
        steps.append(f"ci.log.fetch(repo='{repo}', runId='{runId}') → log length {len(log_val)}")
    except Exception:
        return {"decision": "BLOCKED"}

    # git.pr.diff — cast number to int per contract
    try:
        pr_number = int(number_str)
        pr_result = await ctx.tools.call("git.pr.diff", repo=repo, number=pr_number)
        if not isinstance(pr_result, dict):
            raise TypeError("git.pr.diff must return dict")
        diff_val = pr_result.get("diff")
        if not isinstance(diff_val, str) or len(diff_val.strip()) == 0:
            return {"decision": "BLOCKED"}
        evidence_sources.append({"tool": "git.pr.diff", "result": pr_result})
        steps.append(f"git.pr.diff(repo='{repo}', number='{pr_number}') → diff length {len(diff_val)}")
    except (ValueError, Exception):
        return {"decision": "BLOCKED"}

    # Delegate to exactly two authorized agents
    allowed_delegates = list(ctx.manifest.spec.delegates or [])
    if len(allowed_delegates) < 2:
        return {"decision": "BLOCKED"}

    for name in allowed_delegates[:2]:
        try:
            delegate_resp = await ctx.delegate(name, text)
            if not isinstance(delegate_resp, dict) or "result" not in delegate_resp:
                return {"decision": "BLOCKED"}
            result = delegate_resp["result"]
            # Block on any sub-agent returning decision: BLOCKED, or empty/None result
            if isinstance(result, dict) and result.get("decision") == "BLOCKED":
                return {"decision": "BLOCKED"}
            if isinstance(result, str) and not result.strip():
                return {"decision": "BLOCKED"}
            if result is None:
                return {"decision": "BLOCKED"}
            delegate_results.append({"agent": name, "result": result})
            steps.append(f"delegate('{name}') → result type {type(result).__name__}")
        except Exception:
            return {"decision": "BLOCKED"}

    # Compile prompt with evidence
    try:
        prompt = await ctx.prompt("role")
        evidence_payload = {
            "evidence_sources": evidence_sources,
            "delegate_results": delegate_results
        }
        compiled = prompt.compile(input=text, evidence="{{evidence}}")
        system_msg = [{"role": "system", "content": compiled}]
        user_msg = [{"role": "user", "content": json.dumps({"input": text, "evidence": evidence_payload}, ensure_ascii=False)}]
    except Exception:
        return {"decision": "BLOCKED"}

    # LLM call
    try:
        llm_response = await ctx.llm.chat(system_msg + user_msg, prompt=prompt)
    except Exception:
        return {"decision": "BLOCKED"}

    # Parse and validate decision
    try:
        parsed = json.loads(llm_response.strip())
        if isinstance(parsed, dict) and "decision" in parsed:
            decision = parsed["decision"]
            if decision == "PASS" or decision == "BLOCKED":
                return {
                    "decision": decision,
                    "evidence_sources": evidence_sources,
                    "delegate_results": delegate_results,
                    "steps": steps
                }
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return {
        "decision": "BLOCKED",
        "evidence_sources": evidence_sources,
        "delegate_results": delegate_results,
        "steps": steps
    }