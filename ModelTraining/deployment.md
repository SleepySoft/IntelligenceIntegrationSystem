# AMD MI50 (ROCm) + DeepSpeed ZeRO-2 部署指南

在 AMD MI50 (ROCm) 平台上部署 DeepSpeed 环境比 NVIDIA 稍微复杂一些，因为需要处理 ROCm 的兼容性。

以下是**一步步的实战部署指南**，我们将构建一个基于 `rocm/pytorch` 的 Docker 容器，并确保 LLaMA-Factory 和 DeepSpeed 能在 MI50 上顺利运行。

---

## 第一阶段：宿主机（Ubuntu Host）准备

在运行 Docker 之前，你的 Ubuntu 物理机必须安装好 AMD 驱动和 Docker 运行时支持。

### 1. 安装 AMD GPU 驱动 (Host)
确保你的宿主机已经安装了 ROCm 驱动。

```bash
# 检查驱动是否正常
rocm-smi
```
*如果能看到两张 MI50 卡的信息，说明驱动正常。*

### 2. 安装 Docker 及 ROCm 容器运行时
为了让 Docker 能使用 AMD 显卡，必须安装 `rocm-container-runtime` (类似于 NVIDIA 的 nvidia-container-toolkit)。

```bash
# 1. 安装 Docker (如果已安装可跳过)
sudo apt-get update
sudo apt-get install -y docker.io

# 2. 添加 AMD 仓库并安装 rocm-container-runtime
# 注意：以下是通用步骤，请根据你的 ROCm 版本调整
sudo apt-get update
sudo apt-get install -y rocm-container-runtime

# 3. 配置 Docker Daemon (关键步骤)
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<EOF
{
  "runtimes": {
    "rocm": {
      "path": "/opt/rocm/bin/rocm-container-runtime",
      "runtimeArgs": []
    }
  }
}
EOF

# 4. 重启 Docker
sudo systemctl restart docker
```

---

## 第二阶段：构建 Docker 镜像

直接使用官方的 PyTorch 镜像，然后在其基础上安装 DeepSpeed 和 LLaMA-Factory。

### 1. 创建 `Dockerfile`
新建一个文件夹，并在其中创建一个名为 `Dockerfile` 的文件：

```dockerfile
# 1. 选择基础镜像
# MI50 (Vega20) 在 ROCm 5.7/6.0+ 支持良好。
# 这里选用 ROCm 6.1 + PyTorch 2.3 + Python 3.10 的官方镜像
FROM rocm/pytorch:rocm6.1_ubuntu22.04_py3.10_pytorch_2.3.0

# 2. 设置工作目录
WORKDIR /workspace

# 3. 安装系统级依赖
# ninja: 加速 DeepSpeed 编译
# libopenmpi-dev: DeepSpeed 多卡通信需要 MPI
# git: 拉取代码
RUN apt-get update && apt-get install -y \
    git \
    ninja-build \
    libopenmpi-dev \
    libaio-dev \
    && rm -rf /var/lib/apt/lists/*

# 4. 安装/升级 pip
RUN pip install --upgrade pip

# 5. 安装 LLaMA-Factory
# 我们直接拉取源码安装，方便后续修改
RUN git clone [https://github.com/hiyouga/LLaMA-Factory.git](https://github.com/hiyouga/LLaMA-Factory.git)
WORKDIR /workspace/LLaMA-Factory
# 安装依赖，同时安装 metrics (评估) 和 deepspeed 支持
# 注意：在 ROCm 镜像中，PyTorch 已经装好了，这里 pip install 会自动识别
RUN pip install -e ".[metrics]"

# 6. 安装 DeepSpeed (针对 AMD 优化)
# 官方 rocm/pytorch 镜像通常预装了 DeepSpeed，如果没有或版本不对，才执行下面这行。
# 我们可以强制重装兼容版本，或者相信镜像自带的。
# 建议：先尝试直接安装 LLaMA-Factory 的依赖，它会自动拉取 DeepSpeed。
# 如果需要手动编译 AMD 版 DeepSpeed，解除下面注释：
# RUN pip uninstall -y deepspeed && \
#     pip install deepspeed

# 7. 解决 bitsandbytes 兼容性 (AMD 显卡通常用不到这个，但为了防报错)
# AMD 暂时不推荐用 bitsandbytes 做量化，推荐用 BF16
# 如果必须要用，需要找 ROCm 专用的 bitsandbytes fork，这里暂时跳过

# 8. 设置环境变量
# 针对 MI50 (gfx906) 的优化
ENV HSA_OVERRIDE_GFX_VERSION=9.0.6
# 开启 DeepSpeed 的一些构建标志
ENV DS_BUILD_OPS=1

CMD ["/bin/bash"]
```

### 2. 构建镜像
在 `Dockerfile` 所在目录运行：
```bash
docker build -t qwen-rocm-trainer .
```

---

## 第三阶段：启动容器

使用以下命令启动容器。我们需要将物理机的 GPU 设备映射进容器，并挂载你的代码和数据目录。

```bash
# 假设你的项目代码在 /home/user/project
# 假设你的模型文件在 /home/user/models/Qwen2.5-7B-Instruct

docker run -it -d --name trainer \
    --network=host \
    --ipc=host \
    --group-add video \
    --cap-add=SYS_PTRACE \
    --security-opt seccomp=unconfined \
    --device=/dev/kfd \
    --device=/dev/dri \
    -v /home/user/project:/workspace/project \
    -v /home/user/models:/workspace/models \
    qwen-rocm-trainer
```

**参数解释：**
* `--device=/dev/kfd` 和 `--device=/dev/dri`: **核心参数**，将 AMD GPU 设备直通给容器。
* `--ipc=host`: DeepSpeed 多卡通信需要共享内存。
* `--network=host`: 方便访问外网下载数据，也方便 DeepSpeed 节点间通信。
* `--group-add video`: 赋予容器访问显卡的权限。

---

## 第四阶段：验证 DeepSpeed 与 ROCm

进入容器并进行验证：
```bash
docker exec -it trainer bash
```

### 1. 验证 GPU 可见性
```bash
rocm-smi
# 应该能看到 2 张 MI50
```

### 2. 验证 PyTorch ROCm
```bash
python -c "import torch; print(torch.cuda.is_available()); print(torch.version.hip)"
# 输出: True 和 ROCm 版本号 (如 6.1.x)
```

### 3. 验证 DeepSpeed (最关键)
创建一个测试文件 `check_ds.py`：
```python
import torch
import deepspeed
print(f"DeepSpeed Version: {deepspeed.__version__}")
print(f"CUDA (ROCm) Available: {torch.cuda.is_available()}")
print(f"Device Count: {torch.cuda.device_count()}")
# 检查是否支持 bf16
print(f"BF16 Supported: {torch.cuda.is_bf16_supported()}")
```
运行 `python check_ds.py`。
*注意：MI50 (Vega20) 理论上支持 BF16，但在早期的 PyTorch/ROCm 版本中可能需要开启模拟。如果输出 False，可以在训练脚本中改用 fp16，或者强制尝试 bf16。*

---

## 第五阶段：开始训练

现在你已经在 Docker 里了。

1.  **准备数据**：将你之前生成的 `alpaca_train.json` 等文件放入挂载的目录（例如 `/workspace/project/data`）。
2.  **配置 LLaMA-Factory**：按照指南，修改 `dataset_info.json`。
3.  **准备启动脚本**：将生成的 `train_qwen.sh` 和 `ds_z2_config.json` 放入容器内（或挂载目录）。

### 修改 `ds_z2_config.json` (针对 ROCm 的特别调整)
ROCm 对 `nccl`（NVIDIA 通信库）的替代品是 `rccl`。DeepSpeed 通常能自动处理，但有时需要显式禁用一些不兼容的优化。

如果遇到报错，尝试在 `ds_z2_config.json` 中添加/修改：
```json
{
  "communication_data_type": "bf16",  // 如果用 BF16 训练
  // ... 其他配置保持不变 ...
}
```

### 运行训练
```bash
cd /workspace/LLaMA-Factory
# 确保你的脚本路径和模型路径在容器内是正确的
bash /workspace/project/train_qwen.sh
```

---

## 常见报错与排查 (AMD MI50 专属)

1.  **`RuntimeError: No HIP GPUs are available`**:
    * 检查 `docker run` 时是否漏了 `--device` 参数。
    * 检查宿主机 `rocm-smi` 是否正常。

2.  **`bfloat16` 报错**:
    * MI50 对 BF16 的支持有时依赖于 PyTorch 版本。如果报错，请在 `train_qwen.sh` 中将 `--bf16 True` 改为 `--fp16 True`，并将 DeepSpeed 配置里的 `"bf16": {"enabled": true}` 改为 `"fp16": {"enabled": true}`。虽然 BF16 更好，但 FP16 在 MI50 上更成熟。

3.  **DeepSpeed JIT 编译错误**:
    * DeepSpeed 启动时会尝试编译一些 C++ 算子。如果报错 `ninja: build stopped: subcommand failed`，通常是因为缺少头文件。
    * 解决方法：在 `Dockerfile` 里确保安装了 `libaio-dev`。

4.  **`HSA_OVERRIDE_GFX_VERSION`**:
    * 如果 PyTorch 抱怨显卡架构不匹配，确保在 Docker 环境变量中设置了 `export HSA_OVERRIDE_GFX_VERSION=9.0.6` (对应 MI50)。