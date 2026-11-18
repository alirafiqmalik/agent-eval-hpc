"""Single-response code generation through the allocation's model server."""

import hashlib
import json
import time
from datetime import UTC, datetime

from harbor.agents.base import BaseAgent
from openai import AsyncOpenAI


class VeriContestOneShot(BaseAgent):
    @staticmethod
    def name():
        return "vericontest-one-shot"

    def version(self):
        return "0.1.0"

    async def setup(self, environment):
        result = await environment.exec("mkdir -p /app")
        if result.return_code != 0:
            raise RuntimeError("Could not prepare /app in the sandbox.")

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        base_url = self._get_env("OPENAI_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "OPENAI_BASE_URL is missing. Run through the configured "
                "cluster launcher so the model endpoint is supplied."
            )

        # These markers will delimit the benchmark prompt in task files.
        start_marker = "<VERICONTEST_PROMPT>"
        end_marker = "</VERICONTEST_PROMPT>"

        if start_marker not in instruction or end_marker not in instruction:
            raise ValueError("Task instruction is missing benchmark prompt markers.")

        prompt = instruction.split(start_marker, 1)[1].split(end_marker, 1)[0].strip()
        if not prompt:
            raise ValueError("Benchmark prompt is empty.")

        settings = {
            "model": self.model_name,
            "max_tokens": 2048,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 20261026,
        }
        # Keep the Colab pilot request: one user message and no system message.
        # Harbor appends agent/prompts/system.md outside the markers, so the
        # split above drops it.
        messages = [{"role": "user", "content": prompt}]

        def save_json(filename, data):
            (self.logs_dir / filename).write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        (self.logs_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        save_json(
            "request.json",
            {"messages": messages, **settings, "system_prompt_sent": False},
        )

        metadata = {
            "started_at": datetime.now(UTC).isoformat(),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "settings": settings,
            "status": "started",
        }
        save_json("generation.json", metadata)

        started = time.perf_counter()

        try:
            async with AsyncOpenAI(
                base_url=base_url,
                api_key=self._get_env("OPENAI_API_KEY") or "local",
                timeout=600.0,
                max_retries=0,
            ) as client:
                response = await client.chat.completions.create(
                    messages=messages,
                    **settings,
                )
        except Exception as exc:
            metadata.update(
                status="request_failed",
                elapsed_seconds=time.perf_counter() - started,
                error_type=type(exc).__name__,
            )
            save_json("generation.json", metadata)
            raise

        save_json("response.json", response.model_dump(mode="json"))

        if not response.choices:
            metadata.update(
                status="empty_choices",
                elapsed_seconds=time.perf_counter() - started,
            )
            save_json("generation.json", metadata)
            raise RuntimeError("Model server returned no choices.")

        choice = response.choices[0]
        raw = choice.message.content or ""
        raw_path = self.logs_dir / "raw.txt"
        raw_path.write_text(raw, encoding="utf-8")

        usage = response.usage
        if usage is not None:
            context.n_input_tokens = usage.prompt_tokens
            context.n_output_tokens = usage.completion_tokens

        metadata.update(
            status="generated",
            elapsed_seconds=time.perf_counter() - started,
            finish_reason=choice.finish_reason,
            usage=usage.model_dump(mode="json") if usage else None,
            response_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            returned_model=response.model,
        )
        save_json("generation.json", metadata)
        context.metadata = metadata

        await environment.upload_file(
            source_path=raw_path,
            target_path="/app/response.txt",
        )
