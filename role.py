import json
import re
from typing import Dict, Any, List


def _parse_repo(text: str) -> str:
    m = re.search(r"(?:仓库|repo)\s+([\w\-]+)(?:[\s\.,;]|$)", text)
    return m.group(1) if m else None

def _parse_runid(text: str) -> str:
    m = re.search(r"(?:CI\s+)?runId\s+(\d+)(?:[\s\.,;]|$)", text)
    return m.group(1) if m else None

def _parse_pr_number(text: str) -> str:
    # Match "PR #<digits>" or "PR <digits>" or "#<digits>" — only at word boundary after PR/#
    m = re.search(r"(?:PR\s+#|PR\s+|#)(\d+)(?:[\s\.,;]|$)", text)
    if not m:
        m = re.search(r"PR\s+(\d+)(?:[\s\.,;]|$)", text)
    return m.group(1) if m else None

def _parse_jobid(text: str) -> str:
    # Exact DF-\d+ match, surrounded by whitespace or punctuation
    m = re.search(r"(?:任务|jobId)\s+(DF-\d+)(?:[\s\.,;]|$)", text)
    if not m:
        m = re.search(r"\b(DF-\d+)\b", text)
    return m.group(1) if m else None


async def execute(data: dict, ctx) -> dict:
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED"}

    repo = _parse_repo(text)
    runId = _parse_runid(text)
    number_str = _parse_pr_number(text)
    jobId = _parse_jobid(text)

    missing = []
    if not repo:
        missing.append("repo")
    if not runId:
        missing.append("runId")
    if not number_str:
        missing.append("PR number")
    if not jobId:
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
        compiled = prompt.compile(input=text, evidence=json.dumps(evidence_payload, ensure_ascii=False))
    except Exception:
        return {"decision": "BLOCKED"}

    # LLM call
    try:
        llm_response = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
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