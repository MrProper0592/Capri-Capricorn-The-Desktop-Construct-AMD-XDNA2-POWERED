"""
capri.ai — AMD Ryzen AI XDNA2 NPU Engine and Inference Client.
"""

from capri.ai.model_client import OllamaClient, XDNA2Client, clean_reply_text
from capri.ai.xdna2_engine import (
    ModelfileConfig,
    XDNA2Engine,
    check_memlock_limit,
    get_engine,
    get_npu_telemetry,
    parse_modelfile,
)

__all__ = [
    "ModelfileConfig",
    "OllamaClient",
    "XDNA2Client",
    "XDNA2Engine",
    "check_memlock_limit",
    "clean_reply_text",
    "get_engine",
    "get_npu_telemetry",
    "parse_modelfile",
]
