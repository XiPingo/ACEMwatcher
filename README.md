# ACEMwatcher

定时检查上海交通大学安泰经济与管理学院博士项目公告页的变化，并在有新增、删除或修改时发送邮件到 `xiangxi@smail.nju.edu.cn`。

监控页面：<https://acem.sjtu.edu.cn/acem2021/phd/notices.html>

## Local run

```powershell
python -m pip install -r requirements.txt
python acem.py --dry-run
```

`--dry-run` 只打印变化，不发送邮件，也不会更新快照。

正常发送邮件时需要设置以下环境变量：

- `SMTP_USER`: SMTP 登录账号
- `SMTP_PASS`: SMTP 授权码或密码
- `EMAIL_FROM`: 发件人地址，默认使用 `SMTP_USER`
- `SMTP_HOST`: SMTP 主机，默认 `smtp.qq.com`
- `SMTP_PORT`: 默认 `465`（SSL/TLS）；设为 `587` 时使用 STARTTLS，其他端口使用 SSL/TLS

首次运行会把当前公告作为新增内容发送初始通知，然后保存快照；之后发现公告变化才发送通知。发信失败时不会更新快照。快照文件为 `acem_snapshot.json`。

## GitHub Actions

工作流每 30 分钟运行一次，也支持手动触发。请在仓库 Secrets 中配置 `SMTP_USER`、`SMTP_PASS` 和 `EMAIL_FROM`。

QQ 邮箱需开启 SMTP 服务，`SMTP_USER` 填完整 QQ 邮箱地址，`SMTP_PASS` 填邮箱生成的 SMTP 授权码，`EMAIL_FROM` 建议与 `SMTP_USER` 一致。账号和授权码的首尾空白会自动去除。

可在仓库 Actions Variables 中设置 `SMTP_HOST` 和 `SMTP_PORT`，默认是 `smtp.qq.com:465`。

日志会显示 SMTP 的 `connect`、`greeting`、`login`、`send` 阶段。提交邮件前发生临时断线或超时，会最多尝试 3 次；认证失败直接报错。提交邮件时断线可能无法确认是否已被服务器接受，因此当次不自动重发、不更新快照，后续定时运行仍会尝试通知。服务器已确认接收后，退出连接失败不会导致重复通知。

本地回归测试：`python -m unittest discover -s tests -v`（不连接邮箱、不发送真实邮件）。
