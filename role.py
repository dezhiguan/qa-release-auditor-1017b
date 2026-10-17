import json
import re
from typing import Dict, Any, List


async def execute(data: dict, ctx) -> dict:
    # Parse input text for required identifiers — H2 cases all use data.text
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED"}
    
    # Extract repo: match "仓库 <slug>" or fallback to "repo <slug>" (per H2 examples)
    repo_match = re.search(r"(?:仓库|repo)\s+([\w\-]+)", text)
    # Extract runId: "CI runId <digits>" or "runId <digits>"
    runId_match = re.search(r"(?:CI\s+)?runId\s+(\d+)", text)
    # Extract PR number: "PR #<digits>" or "PR <digits>" or "#<digits>"
    pr_match = re.search(r"(?:PR\s+)?#?(\d+)\b", text)
    # Extract jobId: "任务 <DF-\d+>" or "jobId <DF-\d+>" or "DF-\d+"
    jobId_match = re.search(r"(?:任务|jobId|DF-)\s*(DF-\d+)", text)
    
    repo = repo_match.group(1) if repo_match else None
    runId = runId_match.group(1) if runId_match else None
    number = pr_match.group(1) if pr_match else None
    jobId = jobId_match.group(1) if jobId_match else None
    
    # Block if any required identifier is missing
    missing = []
    if not repo:
        missing.append("repo")
    if not runId:
        missing.append("runId")
    if not number:
        missing.append("PR number")
    if not jobId:
        missing.append("jobId")
    if missing:
        return {"decision": "BLOCKED"}
    
    # Collect evidence from tools
    evidence_sources = []
    steps = []
    
    # Call devflow.job.read
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
    
    # Call ci.log.fetch
    try:
        ci_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(ci_result, dict):
            raise TypeError("ci.log.fetch must return dict")
        # Per contract: log must be non-empty string; logsUnavailable=True blocks
        if not isinstance(ci_result.get("log"), str) or len(ci_result.get("log", "")) == 0 or ci_result.get("logsUnavailable") is True:
            return {"decision": "BLOCKED"}
        evidence_sources.append({"tool": "ci.log.fetch", "result": ci_result})
        steps.append(f"ci.log.fetch(repo='{repo}', runId='{runId}') → log length {len(ci_result['log'])}")
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Call git.pr.diff — number must be int per contract
    try:
        pr_number_int = int(number)
        pr_result = await ctx.tools.call("git.pr.diff", repo=repo, number=pr_number_int)
        if not isinstance(pr_result, dict):
            raise TypeError("git.pr.diff must return dict")
        if not isinstance(pr_result.get("diff"), str) or len(pr_result.get("diff", "")) == 0:
            return {"decision": "BLOCKED"}
        evidence_sources.append({"tool": "git.pr.diff", "result": pr_result})
        steps.append(f"git.pr.diff(repo='{repo}', number='{pr_number_int}') → diff length {len(pr_result['diff'])}")
    except (ValueError, Exception):
        return {"decision": "BLOCKED"}
    
    # Delegate to two authorized sub-agents
    allowed_delegates = list(ctx.manifest.spec.delegates or [])
    if len(allowed_delegates) < 2:
        return {"decision": "BLOCKED"}
    
    delegate_results = []
    for name in allowed_delegates[:2]:
        try:
            delegate_resp = await ctx.delegate(name, text)
            if not isinstance(delegate_resp, dict) or "result" not in delegate_resp:
                raise ValueError(f"invalid delegate response from {name}")
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
    
    # Load prompt and compile with raw evidence
    try:
        prompt = await ctx.prompt("role")
        evidence_payload = {
            "evidence_sources": evidence_sources,
            "delegate_results": delegate_results
        }
        compiled = prompt.compile(input=text, evidence=json.dumps(evidence_payload, ensure_ascii=False))
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Query LLM
    try:
        llm_response = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Strict JSON parse + decision validation
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