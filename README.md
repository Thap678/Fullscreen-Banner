# 全屏提示横幅（OLED 防烧屏版）

托盘常驻的小工具：按一下托盘图标，所有显示器铺一条「请勿触动」全屏横幅，屏蔽键盘（Esc 关闭），
长时间无人操作自动进入纯黑暗屏守护，避免 OLED 烧屏；屏幕分辨率不匹配时会按当前分辨率自动出图。
v1.4 起支持导入自己的图片（任意格式/尺寸），按铺满裁切 / 完整显示 / 拉伸三种方式适配各屏。

- 版本：1.4
- 运行环境：Windows 10/11 x64
- 依赖：Pillow（脚本版）；安装版已内置 Python 3.15 运行时，无需另装

> 本项目基于 [Hannyueh/Fullscreen-Banner](https://github.com/Hannyueh/Fullscreen-Banner)（MIT）修改并重写：
> v1.2 起重写为多分辨率自动出图与 OLED 防烧屏，v1.3 增加安装包，v1.4 增加自定义图片与三种适配方式。
> 原始版权声明见 [LICENSE](LICENSE)。

## 日常使用

| 操作 | 效果 |
|---|---|
| 桌面 / 开始菜单「全屏提示」 | 启动程序（托盘出现金色锁头图标） |
| 左键单击托盘图标 | 显示 / 关闭横幅 |
| 横幅显示时按 `Esc` | 关闭横幅 |
| 横幅显示时按 `Ctrl+Alt+Shift+Esc` | 强制解除键盘屏蔽并关闭横幅（防锁死出口） |
| 右键托盘图标 | 显示/关闭、强制解除、选择自定义图片、适配方式、恢复默认出图、屏幕信息、重新出图、开机启动、退出 |
| 再次双击启动 | 不产生第二个实例，改为切换已运行实例的横幅 |

### 横幅显示时的行为

- 键盘被屏蔽（鼠标不屏蔽），对应任务不会被误触
- 任务栏、输入法状态条被遮挡/临时隐藏；通知浮层无法完全压住（系统限制）
- 无操作 90 秒 → 切到纯黑 + 一行暗字（暗屏守护）；动鼠标、点击或按键立即恢复
- 横幅每 30 秒微移几个像素、整体亮度 85%，进一步降低烧屏风险

## 自定义图片（v1.4 新增）

- **导入**：托盘右键 →「选择自定义图片…」，任意尺寸/格式（PNG、JPG、WebP、BMP、GIF 首帧、AVIF、TIFF 等，
  以 Pillow 能否打开为准）。选完立即生效；横幅正在显示时会先收起，选完自动恢复。
- **免菜单导入**：把图片命名为 `banner.png`（或 `banner.jpg` / `banner.webp` …）放进
  `%LOCALAPPDATA%\Banner\`，下次显示自动使用；`banner_custom.*` 优先级更高。
- **适配方式**（图与屏幕比例不一致时，托盘菜单三项带勾选）：

  | 方式 | 效果 |
  |---|---|
  | 铺满裁切 cover（默认） | 等比放大到盖住整屏后居中裁切，无黑边 |
  | 完整显示 contain | 等比缩小到完整可见，左右或上下留黑 |
  | 拉伸铺满 fill | 直接拉到屏幕尺寸，会变形 |

- 所有显示器共用同一张图，但每块屏按自己的分辨率各自适配；21:9 与 16:10 混编不会拉伸。
- 首次使用会按屏幕分辨率生成缓存（`%LOCALAPPDATA%\Banner\cache\custom_*.png`），之后显示秒开；
  换图、换适配方式或源图改动都会自动失效重算。
- 「恢复默认出图」清除配置里的自定义图，回到按分辨率自动生成横幅。
- 自定义图同样叠加亮度 0.85、30 秒微移与暗屏守护；cover 渲染四周多留 6px，微移时不露黑边；
  透明 PNG 会合成到黑底上。

## 分辨率与自动出图

有自定义图片时，下面这套自动出图逻辑会被跳过（不会再弹「重新出图」确认条）。

1. 优先使用与屏幕**像素尺寸完全相同**的 `banner_{宽}x{高}.png`（1:1 显示，零缩放）
2. 没有精确图时弹出非模态确认条：`[重新出图] [本次铺满] [☐ 不再询问]`
   - 重新出图：后台约 2–3 秒生成，完成后原地替换，不闪屏
   - 本次铺满：按宽度铺满，上下可能被裁
   - 勾选「不再询问」：记住本次选择，以后不再询问
3. 生成的图存在 `%LOCALAPPDATA%\Banner\cache\banner_{宽}x{高}.png`
4. 都找不到时回退到 `banner.png`（铺满）

## 配置文件（`%LOCALAPPDATA%\Banner\banner.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `oled_protect` | true | 防烧屏总开关（false = 静止常亮） |
| `idle_to_guard` | 90 | 空闲多少秒进入暗屏守护 |
| `shift_interval` | 30 | 横幅微移间隔（秒） |
| `shift_radius` | 6 | 微移半径（像素） |
| `brightness` | 0.85 | 横幅亮度系数（1.0 = 原图） |
| `guard_text` | 任务运行中 · 请勿触动 | 暗屏小字 |
| `guard_color` | #4f5c56 | 暗屏小字颜色 |
| `guard_font` | ["Microsoft YaHei UI", 14] | 暗屏小字字体 |
| `guard_move_interval` | 12 | 暗屏小字换位间隔（秒） |
| `hide_ime_bar` | true | 横幅显示期间隐藏输入法状态条 |
| `auto_generate` | true | 缺精确图时是否自动出图 |
| `ask_before_generate` | true | 出图前是否弹确认条 |
| `banner_status` | // SYSTEM STATUS : BUSY | 出图用：状态行 |
| `banner_title` | ["此计算机", "正在运行任务"] | 出图用：大标题两行 |
| `banner_plate` | 请 勿 触 动 | 出图用：警示牌文字 |
| `banner_sub` | 两行说明 | 出图用：说明文字 |
| `banner_image` | "" | 自定义图片绝对路径；空 = 不用（v1.4） |
| `banner_fit` | cover | 自定义图适配：cover / contain / fill（v1.4） |

改完文案后在托盘菜单点「重新出图（当前分辨率）」即可生效。

## 安装包

- 最新产物：`dist\installer\全屏提示_Setup_1.4.exe`（15.9 MB，当前用户安装，**不需要管理员权限**）
- 旧版：`dist\installer\全屏提示_Setup_1.3.exe`（保留用于回滚）
- 发布说明：`RELEASE_NOTES.md`（合并 v1.3 / v1.4 变更）
- 默认安装到 `%LOCALAPPDATA%\Programs\Banner`
- 向导内可勾选：桌面快捷方式、开机启动（默认勾选）、安装完成后立即启动
- 卸载：删除程序文件与快捷方式、清除开机启动项；**保留** `%LOCALAPPDATA%\Banner` 里的配置、日志、缓存图
- 卸载入口：开始菜单「卸载 全屏提示」，或「设置 → 应用」里的「全屏提示」

## 从源码运行 / 构建

```powershell
# 1) 直接运行（需要带 tkinter 的 Python 3.8+，先装依赖）
pip install -r requirements.txt
pythonw banner.pyw

# 2) 构建 exe + 安装包（推荐 pwsh 7；5.1 也能跑，脚本是 ASCII-only）
#    脚本默认使用 Python 3.15：%LOCALAPPDATA%\Python\pythoncore-3.15-64
pip install pyinstaller
pwsh -NoProfile -File .\build.ps1
# 只重打安装包（复用已有 dist\Banner）
pwsh -NoProfile -File .\build.ps1 -SkipExe

# 3) 自定义图片功能自检（隔离临时数据目录，不显示横幅）
python tests\custom_image_checks.py
```

构建依赖：

- Python 3.8+（脚本版只需 Pillow）；打包用 Python 3.15 + `pip install pyinstaller`
- NSIS 便携版解压到 `tools\nsis\nsis-3.10`（`makensis.exe`）

产物：

- `dist\Banner\Banner.exe` + `_internal\`（目录版，约 41.7 MB）
- `dist\installer\全屏提示_Setup_1.4.exe`（约 15.9 MB）

## 目录结构

仓库内：

```
banner.pyw           主程序（单文件，双击即用；打包入口）
make_banner.py       横幅出图（库 + CLI）：python make_banner.py out.png 2560x1440
Banner.spec          PyInstaller 配置（缺 banner_2560x1440.png 也能打包）
version_info.txt     Banner.exe 的文件版本信息（1.4.0.0）
RELEASE_NOTES.md     发布说明（合并 v1.3 / v1.4）
installer.nsi        NSIS 安装脚本（UTF-8 with BOM）
build.ps1            一键构建（exe + 安装包）
banner.png           兜底横幅图（自动生成的 PCB 风格）
tray.ico             托盘图标 / 安装包图标
tests/               无界面自检（custom_image_checks.py：自定义图片/适配/缓存）
LICENSE              MIT（含上游版权声明）
```

构建时在本地生成、不入库：

```
build\               PyInstaller 中间产物
dist\                Banner.exe 目录版 + installer\ 安装包
tools\nsis\          NSIS 便携版（makensis.exe）
banner_{宽}x{高}.png 本机分辨率精确图（缺失时程序按屏幕分辨率自动生成）
```

## 常见问题

- **按 Esc 没反应/找不到托盘图标**：按 `Ctrl+Alt+Shift+Esc` 强制解除；托盘图标可能被 Windows 折叠进「隐藏的图标」
- **开机自启失效**：程序启动时会自动校验并修正注册表里的路径；也可在托盘菜单重新勾选「开机启动」
- **改分辨率后横幅被裁**：会弹「重新出图」确认条，或托盘菜单「重新出图（当前分辨率）」
- **导入的图显示不出来**：确认格式 Pillow 能打开、文件没被移走；横幅显示中导入会先收起再恢复
- **导入的图边缘被裁**：cover 的正常行为；想看到全图请切到「适配：完整显示」
- **想换回默认横幅**：托盘菜单「恢复默认出图」；若自定义图仍然生效，检查
  `%LOCALAPPDATA%\Banner\` 里是否还有 `banner.*` 投图文件
- **想看程序日志**：`%LOCALAPPDATA%\Banner\banner.log`（1 MB 轮转）
- **其它应用的通知会盖住横幅**：系统通知浮层层级高于普通置顶窗口，无法可靠压住；需要时请开「专注助手」
