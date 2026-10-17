import json
import re
from typing import Dict, Any, List


async def execute(data: dict, ctx) -> dict:
    # Parse input
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED", "reason": "input.text must be a string"}
    
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
        return {
            "decision": "BLOCKED",
            "reason": f"missing required identifiers: {', '.join(missing)}"
        }
    
    # Collect evidence from tools
    evidence = {}
    
    # Call devflow.job.read
    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=jobId)
        if not isinstance(job_result, list):
            raise TypeError("devflow.job.read must return list")
        evidence["devflow.job.read"] = job_result
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "reason": f"devflow.job.read failed for jobId={jobId}: {str(e)}"
        }
    
    # Call ci.log.fetch
    try:
        ci_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(ci_result, dict):
            raise TypeError("ci.log.fetch must return dict")
        evidence["ci.log.fetch"] = ci_result
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "reason": f"ci.log.fetch failed for repo={repo}, runId={runId}: {str(e)}"
        }
    
    # Call git.pr.diff
    try:
        pr_result = await ctx.tools.call("git.pr.diff", repo=repo, number=number)
        if not isinstance(pr_result, dict):
            raise TypeError("git.pr.diff must return dict")
        evidence["git.pr.diff"] = pr_result
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "reason": f"git.pr.diff failed for repo={repo}, number={number}: {str(e)}"
        }
    
    # Delegate to two authorized sub-agents
    allowed_delegates = list(ctx.manifest.spec.delegates or [])
    if len(allowed_delegates) < 2:
        return {
            "decision": "BLOCKED",
            "reason": f"insufficient delegates in manifest: {allowed_delegates}"
        }
    
    delegate_results = []
    for name in allowed_delegates[:2]:  # Use first two only
        try:
            delegate_resp = await ctx.delegate(name, text)
            if not isinstance(delegate_resp, dict) or "result" not in delegate_resp:
                raise ValueError(f"invalid delegate response from {name}")
            delegate_results.append({"agent": name, "result": delegate_resp["result"]})
        except Exception as e:
            return {
                "decision": "BLOCKED",
                "reason": f"delegate {name} failed: {str(e)}"
            }
    
    evidence["delegates"] = delegate_results
    
    # Load and compile prompt
    try:
        prompt = await ctx.prompt("role")
        compiled = prompt.compile(input=text, evidence=json.dumps(evidence, ensure_ascii=False))
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "reason": f"prompt compilation failed: {str(e)}"
        }
    
    # Query LLM with prompt=prompt (required for versioning)
    try:
        llm_response = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "reason": f"LLM call failed: {str(e)}"
        }
    
    # Parse decision field from LLM output
    # Match exact decision: "PASS" or "BLOCKED" (case-insensitive, whitespace-trimmed)
    decision_match = re.search(r'"decision"\s*:\s*"(PASS|BLOCKED)"', llm_response)
    if not decision_match:
        return {
            "decision": "BLOCKED",
            "reason": "LLM response missing valid decision field"
        }
    
    decision = decision_match.group(1)
    
    # Always respect explicit BLOCKED — even if explanation contains 'PASS'
    if decision == "BLOCKED":
        return {"decision": "BLOCKED", "reason": "LLM explicitly decided BLOCKED"}
    
    # Only PASS if all evidence present, delegates succeeded, and LLM says PASS
    return {"decision": "PASS"}