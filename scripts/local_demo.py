"""Starts a local CPU-only API with synthetic silence and explicit mock indicators."""

import json

import uvicorn

from tts_inference.gateway import create_gateway
from tts_inference.mock import MockDispatcher

if __name__ == "__main__":
    print("MOCK MODE — no model weights, no GPU, synthetic 0.5s silence only")
    print("Try token: local-demo-key-of-32-plus-characters")
    print("Example POST /v1/audio/jobs: " + json.dumps({"model": "voxcpm2", "input": "Hello", "model_config": {"gender": "female", "age": "young"}}))
    uvicorn.run(create_gateway(MockDispatcher(), client_keys={"local": "local-demo-key-of-32-plus-characters"}), host="127.0.0.1", port=8765)
