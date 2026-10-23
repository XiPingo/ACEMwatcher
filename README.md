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
- `SMTP_PORT`: SMTP SSL 端口，默认 `465`

首次运行会把当前公告作为新增内容发送初始通知，然后保存快照；之后发现公告变化才发送通知。发信失败时不会更新快照。快照文件为 `acem_snapshot.json`。

## GitHub Actions

工作流每 30 分钟运行一次，也支持手动触发。请在仓库 Secrets 中配置 `SMTP_USER`、`SMTP_PASS` 和 `EMAIL_FROM`。
