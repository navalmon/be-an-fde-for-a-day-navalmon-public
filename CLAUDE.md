# Claude Code Instructions

This file contains instructions and configuration for Claude Code in this repository.

# CodeCycle: Autonomous Development Workflow

**MANDATORY**: When implementing features, fixing bugs, or making code changes,
you MUST automatically invoke the CodeCycle workflow:

Skill(bic-common-developer-workflow:codecycle)

## CodeCycle Configuration

MAIN_BRANCH: main
PREREQ: cd py; uv sync --all-packages
PREREQ2: npm install -g pnpm@10.27.0
PREREQ3: cd ts; pnpm install --frozen-lockfile
BUILD: uvx pre-commit run --all-files
BUILD2: cd py; uv run pyright apps/sample common/libs/models common/libs/fastapi
BUILD3: cd ts; pnpm build-all
BUILD4: cd ts; pnpm run-eslint-all

TEST: cd py\common\libs\models; uv run pytest
TEST2: cd py\common\libs\fastapi; uv run pytest
TEST3: cd ts; pnpm test-all
TEST4: cd py\apps\sample; uv run pytest

## Workflow Overview

CodeCycle will autonomously:
1. Plan and implement changes
2. Run prerequisites if configured (optional PREREQ, PREREQ2)
3. Build until successful (BUILD commands)
4. Test until successful (TEST commands)
5. Create branch: codecycle/{feature-name}
6. Commit and push changes
7. Run code review
8. Create pull request
9. Monitor PR continuously (every 30 seconds, up to 7 days)
10. Respond to comments and fix CI failures
11. Continue until PR is merged

**ONLY STOP** when:
- PR is merged successfully
- 3 consecutive build/test failures with no progress
- Critical blocker requires user decision
- User explicitly requests stop (Ctrl+C)

**RESUME**: If interrupted, just ask Claude to "continue monitoring"
