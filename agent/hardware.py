"""Detect PC capacity (RAM / GPU VRAM) and recommend a local model that will actually run well."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess

import psutil

# (minimum memory budget in GB, ollama model tag, why)
# Qwen3 is used by default because its tool-calling is reliable at small sizes.
MODEL_TIERS = [
    (20.0, "qwen3:30b", "30B mixture-of-experts: near cloud quality, needs ~19 GB"),
    (10.5, "qwen3:14b", "14B: strong reasoning and tool use, needs ~9.5 GB"),
    (6.0, "qwen3:8b", "8B: best all-rounder for most PCs, needs ~5.5 GB"),
    (3.5, "qwen3:4b", "4B: good for 8 GB machines, needs ~2.8 GB"),
    (0.0, "qwen3:1.7b", "1.7B: very low-memory fallback, basic tasks only"),
]

# Good alternatives that also support tool calling in Ollama.
ALTERNATIVES = ["llama3.1:8b", "qwen2.5:7b", "mistral-nemo:12b", "granite3.3:8b"]

# Preference order when picking from models already installed.
PREFERRED_FAMILIES = ["qwen3", "qwen2.5", "llama3.1", "llama3.2", "mistral-nemo", "granite3", "mistral"]


def _nvidia_gpus() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        gpus = []
        for line in out.stdout.strip().splitlines():
            name, mem = line.rsplit(",", 1)
            gpus.append({"name": name.strip(), "vram_gb": round(int(mem.strip()) / 1024, 1)})
        return gpus
    except Exception:
        return []


def detect() -> dict:
    vm = psutil.virtual_memory()
    apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    return {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "cpu_cores": psutil.cpu_count(logical=False) or os.cpu_count(),
        "cpu_threads": psutil.cpu_count(logical=True),
        "ram_gb": round(vm.total / 1024**3, 1),
        "ram_free_gb": round(vm.available / 1024**3, 1),
        "gpus": _nvidia_gpus(),
        "apple_silicon": apple_silicon,
    }


def memory_budget(hw: dict) -> tuple[float, str]:
    """How many GB a model can comfortably use, and where it will run."""
    vram = max((g["vram_gb"] for g in hw.get("gpus", [])), default=0.0)
    if vram >= 6:
        return vram - 1.5, f"GPU ({vram} GB VRAM)"
    if hw.get("apple_silicon"):
        return hw["ram_gb"] * 0.6, f"Apple unified memory ({hw['ram_gb']} GB)"
    return hw["ram_gb"] * 0.5, f"CPU + RAM ({hw['ram_gb']} GB)"


def recommend(hw: dict | None = None) -> dict:
    hw = hw or detect()
    budget, where = memory_budget(hw)
    for min_gb, model, why in MODEL_TIERS:
        if budget >= min_gb:
            return {"model": model, "why": why, "runs_on": where, "budget_gb": round(budget, 1),
                    "alternatives": ALTERNATIVES}
    return {"model": MODEL_TIERS[-1][1], "why": MODEL_TIERS[-1][2], "runs_on": where,
            "budget_gb": round(budget, 1), "alternatives": ALTERNATIVES}


def pick_installed(installed: list[str]) -> str | None:
    """Pick the most capable-looking tool-calling model from what is already pulled."""
    for fam in PREFERRED_FAMILIES:
        matches = [m for m in installed if m.startswith(fam)]
        if matches:
            return sorted(matches)[0]
    return installed[0] if installed else None


if __name__ == "__main__":
    import sys
    h = detect()
    r = recommend(h)
    if "--model" in sys.argv:  # used by setup scripts
        print(r["model"])
        raise SystemExit(0)
    print(f"OS:       {h['os']} ({h['machine']})")
    print(f"CPU:      {h['cpu_cores']} cores / {h['cpu_threads']} threads")
    print(f"RAM:      {h['ram_gb']} GB ({h['ram_free_gb']} GB free)")
    for g in h["gpus"]:
        print(f"GPU:      {g['name']} - {g['vram_gb']} GB VRAM")
    print(f"Runs on:  {r['runs_on']}  (model budget ~{r['budget_gb']} GB)")
    print(f"RECOMMENDED MODEL: {r['model']}  -> {r['why']}")
    print(f"Alternatives:      {', '.join(r['alternatives'])}")
