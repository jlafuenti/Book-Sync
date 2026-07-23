"""
Pytest bootstrap for the jetson test suite.

CI installs only the light deps (fastapi, uvicorn, pytest) — not the
Jetson-only stack: faster-whisper needs a GPU/CTranslate2 build that only
exists on the device, and nltk's sentence-tokenizer data (punkt_tab) is
fetched over the network on first use. Stub both modules in sys.modules
before any test imports `server`, so the import succeeds without either.
"""
import sys
import types

if "nltk" not in sys.modules:
    nltk_stub = types.ModuleType("nltk")
    nltk_data_stub = types.ModuleType("nltk.data")
    nltk_data_stub.find = lambda *args, **kwargs: None
    nltk_stub.data = nltk_data_stub
    nltk_stub.download = lambda *args, **kwargs: None
    nltk_stub.sent_tokenize = lambda text: [text]
    sys.modules["nltk"] = nltk_stub
    sys.modules["nltk.data"] = nltk_data_stub

if "faster_whisper" not in sys.modules:
    faster_whisper_stub = types.ModuleType("faster_whisper")
    faster_whisper_stub.WhisperModel = object
    sys.modules["faster_whisper"] = faster_whisper_stub
