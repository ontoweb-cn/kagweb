# KAGWeb on Kubernetes

Component-split stack from a single image: `KAGWEB_COMPONENT`
(`backend` / `frontend`) selects which supervisord program a container runs,
so each container carries exactly one concern while sharing one entrypoint
(settings loading, extras install, env re-export). `apply -k` brings up:

| Container | 端口 | 数据卷 | 说明 |
|---|---|---|---|
| `backend` | 8001 | `/app/data`(PVC,RW) | FastAPI;探针 `/health/ready`、`/health/live` |
| `frontend` | 3782 | `user/settings`(只读) | Next.js;入口导出 auth/API-base 与 backend 同源;代理回环 `127.0.0.1:8001`(同 Pod) |

## 使用

```bash
# 沙箱 runner 镜像不在 GHCR,先本地构建(入 kind/minikube 需 load):
kubectl apply -k deploy/k8s
```

首次启动会话数据初始化由 `init-data` initContainer 完成;模型密钥等运行时
设置通过 Web Settings 写入 PVC(`data/user/settings/*.json`),预置可用
Secret 挂载替换该子目录(不要提交到仓库)。

## 设计约定(与评审结论对应)

- `replicas: 1` + `strategy: Recreate`:`/app/data` 是 SQLite 树,RWO 卷
  不允许滚动双挂载;进程内扩容用 `backend_workers`(多 worker 时需另部署
  redis 并把 `redis_url` 写入 settings)。
- redis **不是**单 worker 的依赖:无 redis 时使用进程内协调器,
  `/health/ready` 正常返回 200。
- 全 Pod 强制 `runAsNonRoot`(UID/GID 1000,与镜像内 `kagweb` 用户
  一致)+ `seccompProfile: RuntimeDefault`。
- 生产环境请在 `kustomization.yaml` 中把 `:latest` pin 到具体版本 tag。

## 子目录部署(如 `ai.wust.edu.cn/kagweb`)

应用支持挂在域名子目录下,前缀在**构建期**烧入:

```bash
docker build --build-arg NEXT_PUBLIC_BASE_PATH=/kagweb \
  -t kagweb:subpath .
```

要点:
- Ingress 只做前缀路由(`path: /kagweb`),**不要**配置 strip-prefix
  rewrite——Next basePath 自己处理前缀,改写会造成双前缀/资源 404;
- WebSocket 需要 Ingress 的 upgrade 注解(nginx:`nginx.ingress.kubernetes.io/proxy-http-version: "1.1"` 等);
- 后端配套:`system.json` 的 `cors_origins` 加入前端完整 origin;若启用
  Codex OAuth,redirect_uri 注册为 `https://<host>/kagweb/api/auth/openai-codex/callback`。

