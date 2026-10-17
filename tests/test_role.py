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
    
    # Mock LLM response with PASS
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    # Input
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对主分支构建与差异，给出可追溯结论。"
    }
    
    # Execute
    result = await execute(data, ctx)
    
    # Assert full structure
    assert result["decision"] == "PASS"
    assert isinstance(result.get("evidence_sources"), list)
    assert len(result["evidence_sources"]) >= 2
    assert isinstance(result.get("delegate_results"), list)
    assert len(result["delegate_results"]) == 2
    assert isinstance(result.get("steps"), list)
    assert len(result["steps"]) > 0


@pytest.mark.asyncio
async def test_execute_missing_repo_blocks():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]
    
    # Missing repo in input
    data = {
        "text": "CI runId 39010072606、PR #1、任务 DF-0007 请核验。"
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
        {"diff": "x", "number": "1"},
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
        {"diff": "", "number": "1"},
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
    
    # LLM returns invalid JSON (no decision key, malformed)
    ctx.llm.chat = AsyncMock(return_value='{"outcome": "PASS", "note": "see log"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"


@pytest.mark.asyncio
async def test_execute_llm_malformed_json_with_injection_returns_blocked():
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
    
    # LLM returns injection-containing malformed JSON (e.g., quoted string containing decision)
    ctx.llm.chat = AsyncMock(return_value='{"log": "ERROR: \"decision\": \"PASS\" in stderr"}')
    
    data = {
        "text": "请对仓库 qa-release-checker-1017 的 CI runId 39010072606、PR #1、任务 DF-0007 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"