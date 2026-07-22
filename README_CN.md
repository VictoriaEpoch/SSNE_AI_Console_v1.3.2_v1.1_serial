# SSNE AI Console v1.3.2

本上位机已按 `ssne_ai_demo (5).zip` 中的 `UART_COMMANDS.md` 接入板端协议，覆盖串口连接、紧凑状态解析、推理模式、人脸录入、久坐告警、跌倒告警、人员/宠物危险区和性能测试。

## 启动

推荐双击：

```text
install_and_run.bat
```

脚本会安装危险区截图所需的 Pillow，然后启动程序。也可以手动执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 串口连接

1. 插入设备并关闭会占用端口的串口助手。
2. 在“串口连接”页点击“刷新串口”。
3. 选择 COM 口和波特率（默认 `115200`）。
4. 点击“连接”。

通信参数为 `115200 8-N-1`，命令按参考客户端使用 `CRLF` 结尾。程序优先使用 pyserial；电脑没有 pyserial 时，会直接调用 Windows 原生 COM 接口。

连接成功后每秒发送一次 `status`。启用久坐监测时，每秒还会发送一次不改变周期输出开关的 `debug`，读取板端 `sv/sc` 姿态字段。

兼容两套状态格式：

- 参考包当前格式：`[SERIAL][S] f=... fs=... p=...`、`[SERIAL][D] sv=... sc=...`
- 旧版格式：`[SERIAL][STATUS] frame=... fall_status=...`

## 久坐告警

在“安全告警”页：

1. 勾选“启用久坐告警”。
2. 设置阈值，默认 30 分钟。
3. 点击“应用”。

板端姿态调试字段 `sc=1` 代表 `SITTING`。上位机在检测到有人且姿态有效时连续计时；站立、躺卧或人员离开会清零，短时姿态丢失保留 5 秒缓冲。达到阈值后，顶部告警条、日志和系统提示音会同时触发。

## 危险区域：截屏并绘制多边形

危险区坐标必须和板端摄像头完整画面对应。使用流程：

1. 先在电脑上显示板端的完整视频画面。
2. 打开“安全告警”，点击“截取屏幕区域”。
3. 在桌面上拖动框选完整视频画面，不要只框危险区。
4. 在弹出的编辑器中，左键依次添加多边形顶点。
5. 拖动白色顶点可以调整；右键顶点删除；右键空白或 `Backspace` 撤销。
6. 点击“下发并启用”。

也可点击“导入截图”，再绘制区域。多边形至少 3 点、最多 12 点。上位机会把顶点换算为归一化坐标，并依次发送：

```text
zone set <x1> <y1> ... <xn> <yn>
zone on
zone list
```

板端使用检测框底边中心（人员/宠物的落脚点）判断是否进入多边形。状态字段：

- `dz/dzn`：危险区是否开启、顶点数量
- `dzt/dzh/dza`：人员目标数、命中数、是否告警
- `pzt/pzh/pza`：宠物目标数、命中数、是否告警

本地会保存久坐阈值和归一化多边形顶点；截图本身不会落盘。

## 设备控制

“设备控制”页提供：

- `base / hand / face / pose / all` 推理模式
- 静态背景和姿态关键点开关
- 立即扫脸、状态与调试查询
- `reg <id> <name> <frames>` 多帧人脸录入
- 状态打印周期
- `test base|face|pose|all|each` 性能测试

“日志终端”仍支持手动发送任意命令，可发送 `help` 或 `pro` 查看板端帮助。

## 告警说明

运行总览会统一显示：人员状态、姿态/久坐计时、跌倒状态、危险区状态和板端帧率。以下状态会触发告警条和提示音：

- 已确认跌倒：`fs=FL`
- 人员进入危险区：`dza=1`
- 猫或狗进入危险区：`pza=1` 或 `[SERIAL][PET_DANGER][ALERT]`
- 连续坐姿达到本地久坐阈值

## 构建 EXE

双击 `build_exe.bat`。脚本会安装 requirements 和 PyInstaller，并生成：

```text
dist\SSNE_AI_Console_v1.3.2.exe
```

## 测试

```powershell
$env:PYTHONPATH=(Get-Location).Path
python tests/test_protocol.py
python tests/test_connection.py
python tests/test_safety.py
```
