# IBeam Fork

IBeam 是一个用于 [Interactive Brokers（盈透证券）Client Portal Web API Gateway][gateway] 的自动认证与会话维护工具。基于 [Voyz/ibeam](https://github.com/Voyz/ibeam) 改进。

> 感谢 [Voyz](https://github.com/Voyz) 创建的原始项目，本项目在其基础上进行了现代化改造。原项目采用 [Apache 2.0 协议](LICENSE)。

## 相对于原版的主要改动

| 改动项 | 原版 | 本版 |
|--------|------|------|
| 浏览器自动化 | Selenium + ChromeDriver + Xvfb | **Playwright**（内置浏览器，无需额外驱动和虚拟显示） |
| 配置方式 | 纯环境变量 | **YAML 配置文件**（支持多账户，环境变量仍可用且优先级更高） |
| 2FA 设备选择 | 不支持多设备 | **支持多 2FA 设备选择**（IB Key / Mobile Authenticator App 等） |
| 未开启 2FA | 必须配置 2FA handler | **兼容未开启 2FA 的账户** |
| Gateway 启动等待 | 固定 20 秒 | **智能等待**（最长 90 秒，就绪即登录） |
| Docker 镜像 | python:3.11 + chromium + xorg 等 20+ apt 包 | **python:3.12 + Playwright Chromium**，镜像更小 |
| CI/CD | 无 | **GitHub Actions 自动构建并推送 Docker Hub 镜像** |

## 核心功能

- **无人值守的 Gateway 认证** — 自动注入 IBKR 凭据完成登录
- **会话保活** — 每 60 秒执行 tickle + validate 维护循环，会话失效时自动重新登录
- **2FA 支持** — TOTP、Google Messages、通知推送、外部请求、自定义 Handler
- **健康检查** — 内置 HTTP 健康服务（端口 5001），提供 `/livez`、`/readyz`、`/activate`、`/deactivate` 端点
- **Docker 容器化** — 开箱即用

## 快速开始

### 1. 准备配置文件

复制示例并填入你的凭据：

```bash
cp config/service-0.yaml.example config/service-0.yaml
# 编辑 config/service-0.yaml，填入你的 IBKR 账户和密码
```

`config/service-0.yaml` 示例：

```yaml
ibAcct:
  username: 你的IBKR用户名
  password: 你的IBKR密码

twoFa:
  handler: TOTP
  totpSecret: 你的Base32密钥
  selectTarget: Mobile Authenticator App
```

> 也支持环境变量方式，环境变量优先级高于 YAML。
> 完整配置项见 `config/service-0.yaml.example.full`。

### 2. 构建并启动

```bash
docker compose up -d --build
```

### 3. 验证

```bash
curl -X GET "https://localhost:5000/v1/api/iserver/auth/status" -k
```

健康检查：

```bash
curl http://localhost:5001/readyz
```

## Docker Compose 配置

项目已包含 `docker-compose.yaml`：

```yaml
services:
  ibeam:
    build: .
    container_name: ibeam
    environment:
      - TRADER_INDEX=${TRADER_INDEX:-0}
    volumes:
      - ./config:/srv/config:ro
    ports:
      - 127.0.0.1:5000:5000
      - 127.0.0.1:5001:5001
    network_mode: bridge
    restart: 'no'
```

配置文件按 `TRADER_INDEX` 索引加载 `config/service-{TRADER_INDEX}.yaml`，默认为 `service-0.yaml`。多账户部署时创建 `service-1.yaml`、`service-2.yaml` 等即可。

## 配置项

支持 YAML 配置文件（推荐）和环境变量两种方式，环境变量优先级更高。

### YAML 配置（推荐）

| YAML 路径 | 对应环境变量 | 默认值 | 说明 |
|-----------|-------------|--------|------|
| `ibAcct.username` | `IBEAM_ACCOUNT` | — | IBKR 用户名（必填） |
| `ibAcct.password` | `IBEAM_PASSWORD` | — | IBKR 密码（必填） |
| `twoFa.handler` | `IBEAM_TWO_FA_HANDLER` | `None` | 2FA 处理器 |
| `twoFa.totpSecret` | `IBEAM_TOTP_SECRET` | `None` | TOTP Base32 密钥 |
| `twoFa.selectTarget` | `IBEAM_TWO_FA_SELECT_TARGET` | `Mobile Authenticator App` | 2FA 设备名称 |
| `gateway.baseUrl` | `IBEAM_GATEWAY_BASE_URL` | `https://localhost:5000` | Gateway 地址 |
| `gateway.startup` | `IBEAM_GATEWAY_STARTUP` | `90` | Gateway 启动等待（秒） |
| `auth.pageLoadTimeout` | `IBEAM_PAGE_LOAD_TIMEOUT` | `15` | 页面加载超时（秒） |
| `auth.errorScreenshots` | `IBEAM_ERROR_SCREENSHOTS` | `false` | 出错时截图 |
| `auth.maxFailedAuth` | `IBEAM_MAX_FAILED_AUTH` | `5` | 最大失败次数 |
| `service.maintenanceInterval` | `IBEAM_MAINTENANCE_INTERVAL` | `60` | 维护间隔（秒） |
| `service.healthServerPort` | `IBEAM_HEALTH_SERVER_PORT` | `5001` | 健康检查端口 |
| `logging.level` | `IBEAM_LOG_LEVEL` | `INFO` | 日志级别 |

完整配置项请参考 [`ibeam/src/var.py`](ibeam/src/var.py)。

## 工作原理

1. 启动 IB Gateway Java 进程
2. 通过 tickle 端点检查 Gateway 是否运行
3. 如果未认证，使用 Playwright 打开 Gateway 认证页面，自动填入凭据并提交
4. 处理 2FA（如已配置）
5. 启动定时维护循环（tickle + validate），保持会话活跃
6. 会话过期或竞争时自动重新认证

## 安全提示

- 凭据需以环境变量形式存储，存在安全风险
- `config/service-*.yaml` 已在 `.gitignore` 中，不会被提交
- 建议使用 Docker Swarm Secrets 或 GCP Secret Manager 等方案管理敏感信息
- 生产环境建议限制端口绑定到 `127.0.0.1`

## 致谢

本项目基于 [Voyz/ibeam](https://github.com/Voyz/ibeam)（Apache 2.0 协议）进行二次开发，感谢原作者 [Voy Zan](https://voyzan.com) 及所有贡献者的工作。

## 许可证

[Apache License 2.0](LICENSE)

## 免责声明

本项目非 Interactive Brokers 官方产品。使用风险自负。IBeam 需要存储您的私有凭据，这可能导致包括但不限于中断、资金损失和账户访问权丧失等风险。建议使用模拟账户凭据以降低潜在风险。

[gateway]: https://ibkrcampus.com/ibkr-api-page/webapi-doc/
