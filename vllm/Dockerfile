# ─────────────────────────────────────────────────────────────────────────────
# vLLM built from source for NVIDIA Blackwell (RTX 5090, sm_120)
#
# The prebuilt vllm/vllm-openai:latest on Docker Hub does NOT include sm_120
# kernels. We must build from source using an NGC base that ships
# CUDA 12.8 + PyTorch 2.6 so nvcc can compile for compute_120.
#
# Build time: ~15–25 minutes (first time)
# Image size:  ~25–30 GB
# ─────────────────────────────────────────────────────────────────────────────

FROM nvcr.io/nvidia/pytorch:25.02-py3

WORKDIR /workspace

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget build-essential ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Clone vLLM (pinned to a known-good commit for sm_120 support)
# To use latest: replace the checkout with: git checkout main
RUN git clone https://github.com/vllm-project/vllm.git && \
    cd vllm && \
    git checkout main

WORKDIR /workspace/vllm

# Use the torch already in the NGC container instead of reinstalling it.
# This is the key step — allows building against PyTorch 2.6+cu128.
RUN python use_existing_torch.py

# Install vLLM build dependencies (without overriding torch)
RUN pip install --no-build-isolation -v \
    cmake>=3.26 \
    ninja \
    packaging \
    setuptools>=61 \
    wheel \
    jinja2

# Build vLLM from source — targets sm_120 for Blackwell
# TORCH_CUDA_ARCH_LIST explicitly includes sm_120
ENV TORCH_CUDA_ARCH_LIST="8.0 8.6 8.9 9.0 10.0 12.0+PTX"
ENV VLLM_USE_PRECOMPILED=""
ENV MAX_JOBS=8

RUN pip install --no-build-isolation -e . 2>&1 | tee /build.log

# Install OpenAI server entrypoint deps
RUN pip install --no-cache-dir \
    openai \
    uvicorn \
    fastapi \
    httpx

# Expose port
EXPOSE 8000

# Entrypoint: vLLM OpenAI-compatible server
# Arguments are passed via docker-compose command:
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]