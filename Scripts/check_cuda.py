import torch
import onnxruntime as ort

print("------ PyTorch (用于 Sentence-Transformers) ------")
print(f"PyTorch 版本: {torch.__version__}")
print(f"CUDA 是否可用: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"当前显卡: {torch.cuda.get_device_name(0)}")
else:
    print("❌ PyTorch 正在使用 CPU")

print("\n------ ONNX Runtime (用于 ChromaDB 默认后端) ------")
print(f"可用的执行提供者: {ort.get_available_providers()}")

if 'CUDAExecutionProvider' in ort.get_available_providers():
    print("✅ ONNX 检测到了 GPU")
else:
    print("❌ ONNX 正在使用 CPU (你需要安装 onnxruntime-gpu)")
