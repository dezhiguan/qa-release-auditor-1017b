import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

# Import the function under test
from role import execute


@pytest.mark.asyncio
async def test_execute_normal_flow():
    # Mock context
    ctx = MagicMock()
    
    # Mock manifest with delegates
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],  # devflow.job.read
        {"log": "build success", "logsUnavailable": False},  # ci.log.fetch
        {"diff": "+++ a/file.py\n@@ -1 +1 @@\n-foo\n+bar", "number": "1"},  # git.pr.diff
    ]
    
    # Mock delegates
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    # Mock prompt
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="compiled prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    
    # Mock LLM response with PASS — now passing prompt=prompt
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    # Input
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对主分支构建与差异，给出可追溯结论。"
    }
    
    # Execute
    result = await execute(data, ctx)
    
    # Assert
    assert result == {"decision": "PASS"}


@pytest.mark.asyncio
async def test_execute_missing_repo_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Missing repo in input
    data = {
        "text": "CI runId 39010072606、PR #1、任务 DF-0007 请核验。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "repo" in result["reason"]


@pytest.mark.asyncio
async def test_execute_llm_returns_blocked_stays_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools returning OK
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "ok", "logsUnavailable": False},
        {"diff": "x", "number": "1"},
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    
    # LLM returns BLOCKED (even with 'PASS' in explanation)
    ctx.llm.chat = AsyncMock(return_value='{"decision": "BLOCKED"} Explanation contains PASS but decision is BLOCKED.')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result == {"decision": "BLOCKED", "reason": "LLM explicitly decided BLOCKED"}


@pytest.mark.asyncio
async def test_execute_tool_failure_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # First tool fails
    ctx.tools.call = AsyncMock(side_effect=Exception("network error"))
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "devflow.job.read failed" in result["reason"]