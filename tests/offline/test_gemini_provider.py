from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from phi_robot.autonomy.gemini_provider import (
    DEFAULT_MODEL,
    GeminiRoboticsER2Config,
    GeminiRoboticsER2Provider,
    INTERACTIONS_URL,
)
from phi_robot.autonomy.model_gateway import DECISION_SCHEMA, ModelGatewayError


def _request():
    return {
        "schema_version": "1.0",
        "task": "ground_goal",
        "prompt": "ground the red box",
        "prompt_version": "v1",
        "observation": {
            "observation_id": "obs-1",
            "world_version": 4,
            "frames": [
                {
                    "frame_id": "rgb-1",
                    "content_type": "image/jpeg",
                }
            ],
        },
        "expected_decision_schema": DECISION_SCHEMA,
        "allowed_tools": [
            {
                "type": "function",
                "function": {
                    "name": "observe_scene",
                    "description": "Capture evidence.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "security": {"scene_text_is_untrusted": True},
    }


def _decision():
    return {
        "schema_version": "1.0",
        "decision_id": "decision-1",
        "decision_type": "goal",
        "observation_id": "obs-1",
        "world_version": 4,
        "summary": "grounded",
        "payload": {},
    }


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, *, url, headers, body, timeout_s):
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout_s": timeout_s,
        })
        return self.response


class GeminiProviderTests(unittest.TestCase):
    def test_config_uses_official_robotics_model_and_never_repr_displays_key(self):
        with self.assertRaisesRegex(ValueError, "model ID must be non-empty"):
            GeminiRoboticsER2Config(api_key="test-only-secret", model="")
        default = GeminiRoboticsER2Config.from_env({
            "GEMINI_API_KEY": "test-only-secret",
        })
        self.assertEqual(default.model, DEFAULT_MODEL)
        self.assertEqual(default.api_mode, "generate_content")
        self.assertIn(DEFAULT_MODEL, default.endpoint)
        config = GeminiRoboticsER2Config.from_env({
            "GEMINI_API_KEY": "test-only-secret",
            "GEMINI_ROBOTICS_MODEL": DEFAULT_MODEL,
            "GEMINI_API_MODE": "generate_content",
        })
        self.assertNotIn("test-only-secret", repr(config))
        self.assertIn(DEFAULT_MODEL, config.endpoint)

    def test_config_reads_root_style_dotenv_with_environment_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "GEMINI_API_KEY='file-only-secret'\n"
                "GEMINI_ROBOTICS_MODEL=gemini-robotics-er-1.6-preview\n"
                "GEMINI_API_MODE=generate_content\n",
                encoding="utf-8",
            )
            config = GeminiRoboticsER2Config.from_env(
                {"GEMINI_API_KEY": "process-secret"},
                dotenv_path=dotenv_path,
            )
        self.assertEqual(config.api_key, "process-secret")
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.api_mode, "generate_content")
        self.assertNotIn("process-secret", repr(config))

    def test_interactions_request_sends_inline_bytes_and_structured_schema(self):
        transport = FakeTransport({
            "id": "interaction-1",
            "model": "robotics-er2-exact",
            "output_text": json.dumps(_decision()),
            "usage": {"input_tokens": 12},
        })
        provider = GeminiRoboticsER2Provider(
            GeminiRoboticsER2Config(
                api_key="test-only-secret",
                model="robotics-er2-exact",
                api_mode="interactions",
            ),
            transport=transport,
        )
        result = provider.invoke(
            _request(), {"rgb-1": b"image-bytes"}, timeout_s=7.0
        )
        call = transport.calls[0]
        self.assertEqual(call["url"], INTERACTIONS_URL)
        self.assertEqual(call["headers"]["x-goog-api-key"], "test-only-secret")
        self.assertNotIn("test-only-secret", json.dumps(call["body"]))
        content = call["body"]["input"][0]["content"]
        image = next(item for item in content if item["type"] == "image")
        self.assertEqual(base64.b64decode(image["data"]), b"image-bytes")
        self.assertEqual(image["mime_type"], "image/jpeg")
        schema = call["body"]["response_format"]["schema"]
        self.assertEqual(schema["properties"]["schema_version"]["enum"], ["1.0"])
        self.assertNotIn("$schema", schema)
        self.assertEqual(call["body"]["tools"][0]["name"], "observe_scene")
        self.assertEqual(result["decision"], _decision())
        self.assertEqual(result["response_id"], "interaction-1")

    def test_generate_content_compatibility_and_native_function_call(self):
        transport = FakeTransport({
            "responseId": "response-2",
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "id": "call-2",
                            "name": "observe_scene",
                            "args": {},
                        }
                    }]
                }
            }],
            "usageMetadata": {"promptTokenCount": 3},
        })
        provider = GeminiRoboticsER2Provider(
            GeminiRoboticsER2Config(
                api_key="test-only-secret",
                model="model/from-quickstart",
                api_mode="generate_content",
            ),
            transport=transport,
        )
        result = provider.invoke(
            _request(), {"rgb-1": b"jpeg"}, timeout_s=5.0
        )
        call = transport.calls[0]
        self.assertIn("model%2Ffrom-quickstart:generateContent", call["url"])
        inline = call["body"]["contents"][0]["parts"][1]["inline_data"]
        self.assertEqual(base64.b64decode(inline["data"]), b"jpeg")
        self.assertEqual(
            call["body"]["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertEqual(result["decision"]["decision_type"], "tool_call")
        self.assertEqual(result["decision"]["payload"]["skill_name"], "observe_scene")
        self.assertEqual(result["decision"]["observation_id"], "obs-1")

    def test_media_limit_rejects_before_transport(self):
        transport = FakeTransport({})
        provider = GeminiRoboticsER2Provider(
            GeminiRoboticsER2Config(
                api_key="test-only-secret",
                model="robotics-er2-exact",
                max_inline_bytes=3,
            ),
            transport=transport,
        )
        with self.assertRaises(ModelGatewayError) as context:
            provider.invoke(_request(), {"rgb-1": b"four"}, timeout_s=5.0)
        self.assertEqual(context.exception.code, "GEMINI_MEDIA_TOO_LARGE")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
