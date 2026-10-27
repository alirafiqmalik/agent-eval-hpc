"""Harbor Singularity adapter for commands governed by longer trial budgets."""

from harbor.environments.singularity import SingularityEnvironment


class UnitySingularityEnvironment(SingularityEnvironment):
    """Avoid Harbor 0.24's implicit 600-second HTTP timeout for agent commands.

    Harbor still owns each phase's time budget and cancellation. The explicit
    command ceiling must exceed those budgets; shorter caller timeouts survive.
    """

    def __init__(self, *args, default_exec_timeout_sec: int, **kwargs):
        if default_exec_timeout_sec <= 0:
            raise ValueError("default_exec_timeout_sec must be positive")
        self.default_exec_timeout_sec = default_exec_timeout_sec
        super().__init__(*args, **kwargs)

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        return await super().exec(
            command=command,
            cwd=cwd,
            env=env,
            timeout_sec=(self.default_exec_timeout_sec if timeout_sec is None else timeout_sec),
            user=user,
        )
