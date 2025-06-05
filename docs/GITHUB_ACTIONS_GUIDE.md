# 🚀 GitHub Actions Docker 构建指南

## 📋 概述

本项目提供了两个 GitHub Actions 工作流来自动构建和推送 Docker 镜像：

1. **完整版** (`.github/workflows/docker-build-push.yml`) - 包含安全扫描、测试和通知
2. **简化版** (`.github/workflows/docker-build-simple.yml`) - 基础构建和推送功能

## ⚙️ 配置要求

### 必需的 Secrets

在 GitHub 仓库设置中添加以下 Secrets：

```
DOCKERHUB_USERNAME  # Docker Hub 用户名
DOCKERHUB_TOKEN     # Docker Hub 访问令牌
```

### 获取 Docker Hub 访问令牌

1. 登录 [Docker Hub](https://hub.docker.com/)
2. 点击右上角头像 → Account Settings
3. 选择 Security → New Access Token
4. 创建令牌并复制保存

## 🔧 工作流功能

### 完整版功能

#### 🛡️ 安全扫描
- **代码扫描**: 使用 Trivy 扫描源代码漏洞
- **镜像扫描**: 扫描构建后的 Docker 镜像
- **SARIF 报告**: 上传扫描结果到 GitHub Security

#### 🏗️ 构建优化
- **多平台构建**: 支持 `linux/amd64` 和 `linux/arm64`
- **缓存优化**: 使用 GitHub Actions 缓存
- **元数据提取**: 自动生成标签和标签

#### 🧪 测试验证
- **健康检查**: 验证镜像能否正常启动
- **功能测试**: 运行基础功能测试
- **部署测试**: 模拟实际部署场景

#### 📢 通知系统
- **成功通知**: 构建成功时的详细信息
- **失败通知**: 构建失败时的错误提示

### 简化版功能

- ✅ 基础构建和推送
- ✅ 多平台支持
- ✅ 缓存优化
- ✅ 基础测试

## 🏷️ 标签策略

工作流会自动生成以下标签：

| 触发条件 | 生成标签 | 示例 |
|----------|----------|------|
| 推送到主分支 | `latest` | `cxsz16888/sehuatang:latest` |
| 推送到分支 | 分支名 | `cxsz16888/sehuatang:v2` |
| 创建标签 | 版本号 | `cxsz16888/sehuatang:v1.0.0` |
| Pull Request | PR号 | `cxsz16888/sehuatang:pr-123` |

## 🚀 触发方式

### 自动触发

```yaml
# 推送到主分支时触发
git push origin main

# 创建标签时触发
git tag v1.0.0
git push origin v1.0.0

# 创建 Pull Request 时触发（仅构建，不推送）
```

### 手动触发

1. 进入 GitHub 仓库
2. 点击 Actions 标签
3. 选择工作流
4. 点击 "Run workflow"
5. 可选择分支和自定义标签

## 📊 构建状态

### 查看构建状态

在仓库主页可以看到构建状态徽章：

```markdown
![Docker Build](https://github.com/username/sehuatang/workflows/构建并推送Docker镜像/badge.svg)
```

### 构建日志

1. 进入 Actions 标签
2. 选择具体的工作流运行
3. 查看详细的构建日志

## 🔍 故障排除

### 常见问题

#### 1. 认证失败
```
Error: Cannot perform an interactive login from a non TTY device
```

**解决方案**:
- 检查 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_TOKEN` 是否正确设置
- 确保 Token 有推送权限

#### 2. 构建超时
```
Error: The operation was canceled.
```

**解决方案**:
- 检查 Dockerfile 是否有耗时操作
- 考虑使用简化版工作流
- 优化依赖安装过程

#### 3. 平台构建失败
```
Error: failed to solve: failed to build for platform linux/arm64
```

**解决方案**:
- 检查依赖是否支持 ARM64
- 可以临时移除 ARM64 平台支持

#### 4. 缓存问题
```
Warning: Failed to restore cache
```

**解决方案**:
- 这通常不影响构建，只是会稍慢
- 可以清理 Actions 缓存重新开始

### 调试技巧

#### 1. 启用调试日志
在工作流中添加：
```yaml
env:
  ACTIONS_STEP_DEBUG: true
  ACTIONS_RUNNER_DEBUG: true
```

#### 2. 本地测试
```bash
# 本地构建测试
docker build -t test-image .

# 测试健康检查
docker run --rm test-image python run.py --mode health
```

#### 3. 分步调试
注释掉工作流中的某些步骤，逐步排查问题。

## 📈 优化建议

### 构建速度优化

1. **优化 Dockerfile**:
   ```dockerfile
   # 将不常变化的层放在前面
   COPY requirements.txt .
   RUN pip install -r requirements.txt
   # 将经常变化的代码放在后面
   COPY . .
   ```

2. **使用 .dockerignore**:
   ```
   .git
   .github
   logs/
   *.md
   ```

3. **缓存策略**:
   ```yaml
   cache-from: type=gha
   cache-to: type=gha,mode=max
   ```

### 安全性优化

1. **最小权限原则**:
   - 使用专用的 Docker Hub Token
   - 限制 Token 权限范围

2. **镜像安全**:
   - 定期更新基础镜像
   - 使用非 root 用户运行

3. **密钥管理**:
   - 使用 GitHub Secrets 存储敏感信息
   - 定期轮换访问令牌

## 📚 参考资料

- [GitHub Actions 文档](https://docs.github.com/en/actions)
- [Docker Build Push Action](https://github.com/docker/build-push-action)
- [Docker Hub 文档](https://docs.docker.com/docker-hub/)
- [Trivy 安全扫描](https://github.com/aquasecurity/trivy-action)

## 🤝 贡献

如果您发现工作流有问题或有改进建议，欢迎：

1. 提交 Issue 描述问题
2. 创建 Pull Request 提供修复
3. 在 Discussions 中分享经验
