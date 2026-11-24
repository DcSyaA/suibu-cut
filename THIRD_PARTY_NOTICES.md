# 第三方组件与许可

## FFmpeg

本程序在 `bin/ffmpeg.exe` 中随附 FFmpeg，用于音频解码、试听片段生成和混剪导出。

- 版本：`n4.3.2-160-gfbb9368226`（Windows x64 static build）
- 项目主页：<https://ffmpeg.org/>
- 许可：GNU Lesser General Public License v3.0 or later（LGPL-3.0-or-later）
- 许可全文：`licenses/LGPL-3.0.txt` 与 `licenses/GPL-3.0.txt`
- 该构建启用的组件与配置可通过运行 `bin\ffmpeg.exe -L` 查看。

FFmpeg 是按原样随附的独立可执行文件，程序通过命令行调用它，没有修改其源码。若需要重新编译或获取对应源代码，请访问 <https://ffmpeg.org/download.html>，并按上述版本号与构建配置获取上游源码。

## Python

便携版使用 Python 3.13（64-bit）作为运行时，位于 `runtime/`。

- 项目主页：<https://www.python.org/>
- 许可：Python Software Foundation License Version 2
- 许可全文：`runtime/LICENSE.txt`

## Tcl/Tk

图形界面基于 Tcl/Tk 8.6，随 Python 运行时一同分发，位于 `runtime/tcl/`。Tcl/Tk 使用 BSD 风格许可，详情请参阅 Python 安装包及 Tcl/Tk 项目页面：<https://www.tcl.tk/>。

## Microsoft .NET Framework

`随舞剪辑台.exe` 是一个很小的 .NET Framework 4.x 启动器，用于无控制台窗口地启动便携 Python 运行时。它不修改系统，也不安装任何组件。
