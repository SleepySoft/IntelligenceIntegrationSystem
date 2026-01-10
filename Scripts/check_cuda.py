import torch
import onnxruntime as ort

print("------ PyTorch (用于 Sentence-Transformers) ------")
print(f"PyTorch 版本: {torch.__version__}")
print(f"CUDA 是否可用: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"当前显卡: {torch.cuda.get_device_name(0)}")

    # 查看 Torch 识别到的算力（1070 应该是 6.1）
    print(f"Device Capability: {torch.cuda.get_device_capability()}")
    print(f"Device Name: {torch.cuda.get_device_name(0)}")

    # 3. 核心步骤：测试一个简单的矩阵运算，看是否会报错
    try:
        x = torch.randn(1, 3).cuda()
        print("Success: GPU computation is working!")
    except Exception as e:
        print(f"Failed: {e}")
else:
    print("❌ PyTorch 正在使用 CPU")

print("\n------ ONNX Runtime (用于 ChromaDB 默认后端) ------")
print(f"可用的执行提供者: {ort.get_available_providers()}")

if 'CUDAExecutionProvider' in ort.get_available_providers():
    print("✅ ONNX 检测到了 GPU")
else:
    print("❌ ONNX 正在使用 CPU (你需要安装 onnxruntime-gpu)")
