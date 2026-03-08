# ClawPool (Enterprise OpenClaw Multi-Instance)

本项目用于在一台主机上为企业员工按人分配并运行独立 OpenClaw 实例，统一入口登录后自动路由到“该员工固定实例”。

## 主要功能

- Keycloak OIDC + oauth2-proxy 统一认证入口
- 员工首次登录自动创建实例（JIT Provision），后续登录固定命中同一容器
- 每用户独立数据目录，实例与数据隔离，避免跨用户访问
- 空闲自动停机（idle-controller），降低资源占用
- 资源动态档位调节（resource-controller）
- 登录后自动唤醒等待页（避免首次冷启动直接报错）
- `trusted-proxy` 鉴权模式，兼容 OpenClaw Gateway 与本地回环调用
- Web 控制台 `/console`（xterm），支持命令行交互和窗口自适应

## 架构概览

流量链路：

`Browser -> Traefik -> oauth2-proxy -> instance-manager -> openclaw-<user>`

核心组件：

- `infra/traefik`: 统一入口转发
- `infra/oauth2-proxy`: OIDC 登录与会话
- `services/instance-manager`: 按用户解析、实例创建/启动、反向代理、控制台代理
- `services/idle-controller`: 空闲实例自动停止
- `services/resource-controller`: 活跃实例资源调节

## 目录结构

- `infra/`: 部署与配置（compose、env 模板、traefik、脚本）
- `services/instance-manager/`: 按用户实例路由与生命周期控制
- `services/idle-controller/`: 空闲关停
- `services/resource-controller/`: 资源调度
- `infra/tests/`: 部署后验证脚本
- `docs/`: 方案、报告、运行文档

## 部署前准备

1. 安装 Docker / Docker Compose（v2）。
2. 准备域名并指向部署主机（例如 `claw.example.com`）。
3. 在 Keycloak 创建 OIDC Client（用于 oauth2-proxy）。
4. 预创建宿主机持久化目录（默认）：
   - `/srv/openclaw/users`
5. 准备 OpenAI Endpoint / API Key 与允许模型列表。

## 部署步骤

1. 准备环境变量文件：

```bash
cp infra/.env.deploy.template infra/.env
```

2. 编辑 `infra/.env`，至少填写以下关键项：

- `KEYCLOAK_ISSUER_URL`
- `KEYCLOAK_CLIENT_ID`
- `KEYCLOAK_CLIENT_SECRET`
- `OPENCLAW_OAUTH2_COOKIE_SECRET`
- `OPENCLAW_HOST`
- `OPENCLAW_IMAGE` / `OPENCLAW_IMAGE_TAG`
- `OPENCLAW_DEFAULT_OPENAI_KEY`
- `OPENCLAW_DEFAULT_OPENAI_ENDPOINT`
- `OPENCLAW_ALLOWED_MODELS`
- `OPENCLAW_DEFAULT_OPENAI_MODEL`
- `OPENCLAW_USERS_ROOT`（默认 `/srv/openclaw/users`）

3. 校验 Compose 配置：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.base.yml config
```

4. 启动基础服务：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.base.yml up -d --build
```

5. 查看服务状态：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.base.yml ps
```

## 切换 OpenClaw 镜像

- 仅更新默认镜像（供后续新建用户容器使用，并刷新 `instance-manager`）：

```bash
ops/set-openclaw-image.sh --image yx-openclaw --tag 20260308
```

- 更新默认镜像，并将现有 `openclaw-*` 容器迁移到新镜像：

```bash
ops/set-openclaw-image.sh --image yx-openclaw --tag 20260308 --recreate-existing
```

## 自定义镜像能力

- 当前自定义镜像默认内置 OCR、Office/PDF 转换、图片处理、音视频处理、网页抓取、压缩归档、源码分析工具。
- 关键命令包括：`tesseract`、`soffice`、`pandoc`、`pdftotext`、`qpdf`、`mutool`、`magick`、`ffmpeg`、`mediainfo`、`lynx`、`html2text`、`7z`、`zip`、`rg`、`ag`、`ctags`。
- 本地构建示例：`docker build -t yx-openclaw:20260306 infra/docker-build`
- 若后续要瘦身，可通过 build args 关闭某些工具组，例如：`--build-arg INSTALL_LIBREOFFICE=0 --build-arg INSTALL_OCR=0`。

## 使用方式

1. 用户访问 `https://<OPENCLAW_HOST>`。
2. 完成 Keycloak 登录后，系统按身份路由到该用户专属实例。
3. 若实例未运行，会先显示“初始化/唤醒”页面，准备好后自动跳转。
4. Web 终端入口：`https://<OPENCLAW_HOST>/console`（同样按当前登录用户连接对应容器）。

## 部署后验证

推荐运行健康检查：

```bash
infra/tests/gateway-health-check.sh <user_email> <host>
```

示例：

```bash
infra/tests/gateway-health-check.sh alice@company.com claw.example.com
```

40 并发用户压测预检：

```bash
make perf-40-dry
```

实际执行：

```bash
make perf-40
```

## 常用运维命令

- 查看 infra 日志：

```bash
docker logs --tail 200 infra-instance-manager-1
docker logs --tail 200 infra-oauth2-proxy-1
```

- 清理某用户实例与数据（用于“首次登录”重测）：

```bash
infra/scripts/cleanup-user-data.sh <user_email>
```

- 重启 instance-manager：

```bash
docker compose --env-file infra/.env -f infra/docker-compose.base.yml up -d --build instance-manager
```

## 安全与配置建议

- 不要提交真实密钥到 Git；仅在 `infra/.env` 填写敏感信息
- `OPENCLAW_GATEWAY_AUTH_MODE` 建议保持 `trusted-proxy`
- `OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER` 建议使用 `host`（兼容容器内本地 gateway 调用）
- 若启用 JIT，建议同时配置 `OPENCLAW_ALLOWED_EMAIL_DOMAINS` 或 `OPENCLAW_ALLOWED_GROUPS`

## 相关文档

- 基础部署说明：[infra/README.md](infra/README.md)
- 环境变量模板：`infra/.env.deploy.template`
