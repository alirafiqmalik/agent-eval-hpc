"""Exercise the real inference client against a local stub before spending GPU time."""

import argparse
import importlib.metadata
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def check_client() -> dict:
    import litellm

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            response = {
                "id": "preflight",
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
                                    "id": "call_preflight",
                                    "type": "function",
                                    "function": {
                                        "name": "bash",
                                        "arguments": '{"command":"true"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = litellm.completion(
                model="hosted_vllm/specgym",
                api_base=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="local",
                messages=[{"role": "user", "content": "Run true."}],
                tools=[
                    {
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
                ],
                max_tokens=32,
                timeout=10,
                num_retries=0,
            )
            assert len(requests) == 1 and requests[0]["tools"]
            call = response.choices[0].message.tool_calls[0]
            assert call.function.name == "bash"
            assert json.loads(call.function.arguments) == {"command": "true"}
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return {name: importlib.metadata.version(name) for name in ("litellm", "mcp", "swe-rex")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-revision")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.expected_revision:
        revision = Path("/opt/specgym-agent-revision").read_text().strip()
        if revision != args.expected_revision:
            raise RuntimeError("Image SWE-agent revision differs from the experiment")
        # Import the complete native path, not just LiteLLM's lazy public facade.
        from sweagent.agent.agents import DefaultAgent  # noqa: F401

    packages = check_client()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"client_check_passed": True, "revision": args.expected_revision, "packages": packages},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
