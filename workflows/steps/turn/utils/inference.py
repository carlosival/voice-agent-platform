import numpy as np
import onnxruntime as ort
import os
from transformers import WhisperFeatureExtractor

from .audio_utils import truncate_audio_to_last_n_seconds

from pathlib import Path

# workflows/steps/turn/utils/inference.py -> workflows/steps/turn/
TURN_STEP_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = TURN_STEP_DIR / "model"

ONNX_MODEL_FILENAME = "smart-turn-v3.2-cpu.onnx"
ONNX_MODEL_PATH = MODEL_DIR / ONNX_MODEL_FILENAME

if not ONNX_MODEL_PATH.is_file():
    raise FileNotFoundError(
        f"Smart-turn ONNX model not found at {ONNX_MODEL_PATH}. "
        f"Did you download it into {MODEL_DIR}?"
    )



def build_session(onnx_path):
    so = ort.SessionOptions()
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.intra_op_num_threads = os.cpu_count()
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(onnx_path, sess_options=so)

feature_extractor = WhisperFeatureExtractor(chunk_length=8)
session = build_session(str(ONNX_MODEL_PATH))


def predict_endpoint(audio_array):
    """
    Predict whether an audio segment is complete (turn ended) or incomplete.

    Args:
        audio_array: Numpy array containing audio samples at 16kHz, float32 [-1,1], mono

    Returns:
        Dictionary containing prediction results:
        - prediction: 1 for complete, 0 for incomplete
        - probability: Probability of completion (sigmoid output)
    """

    # Truncate to 8 seconds (keeping the end) or pad to 8 seconds
    audio_array = truncate_audio_to_last_n_seconds(audio_array, n_seconds=8)

    # Process audio using Whisper's feature extractor
    inputs = feature_extractor(
        audio_array,
        sampling_rate=16000,
        return_tensors="np",
        padding="max_length",
        max_length=8 * 16000,
        truncation=True,
        do_normalize=True,
    )

    # Extract features and ensure correct shape for ONNX
    input_features = inputs.input_features.squeeze(0).astype(np.float32)
    input_features = np.expand_dims(input_features, axis=0)  # Add batch dimension

    # Run ONNX inference
    outputs = session.run(None, {"input_features": input_features})

    # Extract probability (ONNX model returns sigmoid probabilities)
    probability = outputs[0][0].item()

    # Make prediction (1 for Complete, 0 for Incomplete)
    prediction = 1 if probability > 0.5 else 0

    return {
        "prediction": prediction,
        "probability": probability,
    }

# docker exec -it worker-1 python3 -m workflows.steps.turn.inference

# Example usage
if __name__ == "__main__":
    # Create a dummy audio array for testing (1 second of random audio)
    dummy_audio = np.random.randn(16000).astype(np.float32)

    result = predict_endpoint(dummy_audio)
    print(f"Prediction: {result['prediction']}")
    print(f"Probability: {result['probability']:.4f}")