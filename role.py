import json
import re
from typing import Dict, Any, List


async def execute(data: dict, ctx) -> dict:
    # Parse input
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED"}
    
    # Extract required identifiers using strict regex
    repo_match = re.search(r"仓库\s+([\w\-]+)", text)
    runId_match = re.search(r"CI\s+runId\s+(\d+)", text)
    pr_match = re.search(r"PR\s+#(\d+)", text)
    jobId_match = re.search(r"任务\s+(DF-\d+)", text)
    
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
    evidence = {}
    
    # Call devflow.job.read
    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=jobId)
        if not isinstance(job_result, list):
            raise TypeError("devflow.job.read must return list")
        evidence["devflow.job.read"] = job_result
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Call ci.log.fetch
    try:
        ci_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(ci_result, dict):
            raise TypeError("ci.log.fetch must return dict")
        # Check for missing or invalid log evidence per contract
        if not isinstance(ci_result.get("log"), str) or len(ci_result.get("log", "")) == 0 or ci_result.get("logsUnavailable") is True:
            return {"decision": "BLOCKED"}
        evidence["ci.log.fetch"] = ci_result
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Call git.pr.diff
    try:
        pr_result = await ctx.tools.call("git.pr.diff", repo=repo, number=number)
        if not isinstance(pr_result, dict):
            raise TypeError("git.pr.diff must return dict")
        # Check for missing or invalid diff evidence per contract
        if not isinstance(pr_result.get("diff"), str) or len(pr_result.get("diff", "")) == 0:
            return {"decision": "BLOCKED"}
        evidence["git.pr.diff"] = pr_result
    except Exception:
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
            # Block if sub-agent returns BLOCKED decision (per H1 & sandbox)
            if isinstance(result, dict) and result.get("decision") == "BLOCKED":
                return {"decision": "BLOCKED"}
            # Also block if result is empty string or falsy non-dict
            if isinstance(result, str) and not result.strip():
                return {"decision": "BLOCKED"}
            if result is None:
                return {"decision": "BLOCKED"}
            delegate_results.append({"agent": name, "result": result})
        except Exception:
            return {"decision": "BLOCKED"}
    
    evidence["delegates"] = delegate_results
    
    # Load and compile prompt
    try:
        prompt = await ctx.prompt("role")
        compiled = prompt.compile(input=text, evidence=json.dumps(evidence, ensure_ascii=False))
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Query LLM with prompt=prompt
    try:
        llm_response = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
    except Exception:
        return {"decision": "BLOCKED"}
    
    # Parse decision field — exact JSON key match only
    try:
        parsed = json.loads(llm_response.strip())
        if isinstance(parsed, dict) and "decision" in parsed:
            decision = parsed["decision"]
            if decision == "PASS" or decision == "BLOCKED":
                return {"decision": decision}
    except json.JSONDecodeError:
        pass
    
    # Fallback: strict regex on top-level "decision": "..."
    decision_match = re.search(r'"decision"\s*:\s*"(PASS|BLOCKED)"', llm_response)
    if decision_match:
        decision = decision_match.group(1)
        return {"decision": decision}
    
    return {"decision": "BLOCKED"}