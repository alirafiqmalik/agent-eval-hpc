"""Check native SWE-agent -> LiteLLM -> HTTPX timeouts inside the experiment image."""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace


def probe() -> dict:
    import httpx
    import litellm
    from sweagent.agent.models import GenericAPIModelConfig, LiteLLMModel

    calls = []
    transport = []
    delay = 0.0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            time.sleep(delay)
            response = {
                "id": "timeout-probe",
                "object": "chat.completion",
                "created": 0,
                "model": "specgym",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_probe",
                                    "type": "function",
                                    "function": {"name": "bash", "arguments": '{"command":"true"}'},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            body = json.dumps(response).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected from the deliberately short negative control.

    original_send = httpx.Client.send

    def observed_send(client, request, **kwargs):
        if request.url.host == "127.0.0.1" and request.url.path.endswith("/chat/completions"):
            transport.append(dict(request.extensions["timeout"]))
        return original_send(client, request, **kwargs)

    tool = {
        "type": "function",
        "function": {
            "name": "bash",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def query(timeout):
            completion = {"max_tokens": 32768, "num_retries": 0}
            if timeout is not None:
                completion["timeout"] = timeout
            model = LiteLLMModel(
                GenericAPIModelConfig(
                    name="hosted_vllm/specgym",
                    api_key="local",
                    api_base=f"http://127.0.0.1:{server.server_port}/v1",
                    max_input_tokens=98304,
                    temperature=0.6,
                    top_p=0.95,
                    per_instance_cost_limit=0,
                    per_instance_call_limit=400,
                    completion_kwargs=completion,
                ),
                SimpleNamespace(use_function_calling=True, tools=[tool]),
            )
            return model._single_query([{"role": "user", "content": "Run true."}], n=1)

        httpx.Client.send = observed_send
        try:
            query(None)
            default_timeout = transport[-1]["read"]
            assert default_timeout == 600.0
            delay = 0.2
            try:
                query(0.05)
            except litellm.Timeout:
                pass
            else:
                raise AssertionError("Negative control did not time out")
            assert transport[-1]["read"] == 0.05
            response = query(7200)
            assert response[0]["tool_calls"][0]["function"]["name"] == "bash"
            assert transport[-1]["read"] == 7200.0
            assert calls[-1]["max_tokens"] == 32768 and calls[-1]["tools"]
            assert "timeout" not in calls[-1]  # Client setting, not a sampling parameter.
            return {
                "passed": True,
                "default_read_timeout": default_timeout,
                "configured_read_timeout": transport[-1]["read"],
                "negative_control_timeout": 0.05,
                "delayed_response_seconds": delay,
                "native_model_path": "LiteLLMModel._single_query",
                "http_calls": len(calls),
            }
        finally:
            httpx.Client.send = original_send
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
