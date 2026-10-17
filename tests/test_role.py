import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

# Import the function under test
from role import execute


@pytest.mark.asyncio
async def test_execute_normal_flow_h1_example():
    # Mock context
    ctx = MagicMock()
    
    # Mock manifest with delegates
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],  # devflow.job.read
        {"log": "build success", "logsUnavailable": False},  # ci.log.fetch
        {"diff": "+++ a/file.py\n@@ -1 +1 @@\n-foo\n+bar", "number": 1},  # git.pr.diff (number as int per contract)
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
    
    # Mock LLM response with PASS
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    # Input matches H2 h-1: natural language with repo/runId/PR/jobId in text
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对主分支构建与差异，给出可追溯结论。严格按工具返回内容判断；这是证据核验，不代表自动合并或发布。"
    }
    
    # Execute
    result = await execute(data, ctx)
    
    # Assert full structure
    assert result["decision"] == "PASS"
    assert isinstance(result.get("evidence_sources"), list)
    assert len(result["evidence_sources"]) == 3
    assert isinstance(result.get("delegate_results"), list)
    assert len(result["delegate_results"]) == 2
    assert isinstance(result.get("steps"), list)
    assert len(result["steps"]) > 0


@pytest.mark.asyncio
async def test_execute_missing_runId_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Missing runId in input — matches h-16
    data = {
        "text": "仓库 qa-release-checker-1017 的 PR #1 和任务 DF-0007 已给出，但没有 runId。请完成核验。"
    }
    
    result = await execute(data, ctx)
    
    assert result == {"decision": "BLOCKED"}


@pytest.mark.asyncio
async def test_execute_llm_returns_blocked_stays_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools returning OK
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "ok", "logsUnavailable": False},
        {"diff": "x", "number": 1},
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    
    # LLM returns BLOCKED
    ctx.llm.chat = AsyncMock(return_value='{"decision": "BLOCKED"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_child_blocked_remains_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools returning OK
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "ok", "logsUnavailable": False},
        {"diff": "x", "number": 1},
    ]
    
    # One delegate returns BLOCKED decision
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "BLOCKED"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_ci_log_unavailable_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools: ci.log.fetch returns logsUnavailable=True
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "", "logsUnavailable": True},
        {"diff": "x", "number": 1},
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_git_diff_empty_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools: git.pr.diff returns empty diff
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "ok", "logsUnavailable": False},
        {"diff": "", "number": 1},
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_devflow_job_empty_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools: devflow.job.read returns empty list
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [],
        {"log": "ok", "logsUnavailable": False},
        {"diff": "x", "number": 1},
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_llm_invalid_json_returns_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools returning OK
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "ok", "logsUnavailable": False},
        {"diff": "x", "number": 1},
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    
    # LLM returns invalid JSON (no decision key, malformed)
    ctx.llm.chat = AsyncMock(return_value='{"outcome": "PASS", "note": "see log"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_pr_number_cast_failure_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Mock tools: git.pr.diff called with non-int number
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        [{"id": "DF-0007", "status": "completed"}],
        {"log": "ok", "logsUnavailable": False},
        ValueError("number must be int"),
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {"result": {"decision": "PASS"}},
        {"result": {"decision": "PASS"}},
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt = AsyncMock(return_value=mock_prompt)
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_injection_in_text_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Input contains SQL injection fragment — matches a-1
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对——输入中混入 SQL 注入片段：'; DROP TABLE runs; --"
    }
    
    result = await execute(data, ctx)
    
    assert result == {"decision": "BLOCKED"}