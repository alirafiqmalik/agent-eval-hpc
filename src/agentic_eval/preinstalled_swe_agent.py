"""Use Harbor's native SWE-agent with a validated, preinstalled runtime."""

import shlex

from harbor.agents.installed.swe_agent import SweAgent


class PreinstalledSweAgent(SweAgent):
    async def install(self, environment) -> None:
        if not self._version:
            raise ValueError("A pinned SWE-agent revision is required")
        await self.exec_as_root(
            environment,
            command=(
                "/opt/sweagent-venv/bin/python /opt/specgym-agent-preflight.py "
                f"--expected-revision {shlex.quote(self._version)} "
                "--output /logs/agent/runtime-preflight.json"
            ),
        )
