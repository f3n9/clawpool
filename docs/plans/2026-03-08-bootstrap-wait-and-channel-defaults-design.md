# Bootstrap Wait Page And Channel Defaults Design

**Context**

当前有两个用户可见回归：

1. 用户首次登录、实例尚未启动时，没有立即展示等待页，而是停留在登录页或空白页。
2. 新用户进入后，runtime config 仍显式写入 `telegram` 和 `wecom`，没有满足“内置 Channels 依赖产品默认；额外安装插件才显式启用”的目标。

**Goals**

- 修复浏览器首次进入时的等待页体验，让用户在容器启动期间稳定看到 bootstrap wait page。
- 调整默认 Channels / 插件策略：
  - 内置 Channels 若 OpenClaw 本身默认启用，则不强制写入 `openclaw.json`。
  - 额外安装且默认未启用的插件（当前是 `wecom`，未来可扩展）应显式写入 `enabled: true`。
- 两个问题分开修复、分开提交，便于回滚和验证。

## Problem 1: Bootstrap Wait Page

**Root Cause**

`/resolve` 是登录后首个关键入口之一，但 `_should_use_bootstrap_wait_page()` 当前把 `/resolve` 排除掉了。结果是：

- 未认证或刚完成认证时，若请求命中 `/resolve`，启动中状态返回的是 JSON 错误；
- 浏览器不会被切换到等待页，因此用户看到的就会是登录页原地不动或空白页。

**Design**

- 允许 `/resolve` 在浏览器导航场景下使用 bootstrap wait page。
- 保持 `/health`、`/__openclaw__/bootstrap-status`、websocket 等非页面/轮询入口继续返回机器可读响应，不进入等待页。
- 不改动已有 wait page 轮询机制，只修正入口判定。

**Validation**

- 新增针对 `_should_use_bootstrap_wait_page()` 的单元测试，确认 `/resolve` 在浏览器导航请求下返回 `True`。
- 新增针对非 HTML 请求/状态轮询路径的回归测试，避免误伤 API 行为。

## Problem 2: Default Channel / Plugin Persistence

**Root Cause**

当前的默认启用逻辑把“内置 channels”和“额外安装插件”混在一起处理：

- host-side runtime config repair 会把默认插件直接写进 `openclaw.json`；
- startup-time reconciliation 也会扫描 `/app/extensions` 与 `/opt/openclaw/extensions` 并写入启用状态。

这会导致：

- 内置 Channels（例如 `telegram`）即使本身是产品默认启用，也被显式写入配置；
- `wecom` 和内置 channel 没有被分层处理。

**Design**

- 将“需要显式持久化的默认插件”限定为 **额外安装的 bundled plugins**，来源为 `/opt/openclaw/extensions`。
- 不再把 `/app/extensions`（内置扩展根）作为“需要显式写入 config 的默认启用来源”。
- 保留对 `OPENCLAW_DEFAULT_CHANNEL_PLUGINS` 的支持，但默认值改为只覆盖额外插件集合，而不是内置 + 额外混合。
- 对已有用户显式配置继续保持尊重：若某插件已经是 `enabled: false`，不覆盖。

**Validation**

- 新增/调整单元测试，验证：
  - `telegram` 不再被默认写入 runtime config；
  - `wecom` 仍会被显式写入 `enabled: true`；
  - 非法插件名仍被忽略；
  - 用户显式 `enabled: false` 仍被保留。

## Delivery Plan

1. 先修 Problem 1，单独提交。
2. 再修 Problem 2，单独提交。
3. 每个问题都走失败测试 -> 最小实现 -> 验证通过 -> 提交 的顺序。
