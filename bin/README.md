# bin/

本目录用于放置随附的 FFmpeg 可执行文件，**不纳入版本库**（约 52 MB）。

想从源码运行，二选一：

1. 下载 `ffmpeg.exe` 放到本目录：`bin\ffmpeg.exe`；
2. 或者把 `ffmpeg` 加入系统 `PATH`，程序会自动查找。

获取 FFmpeg：<https://ffmpeg.org/download.html>（Windows 构建可选用
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 或 [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases)）。
建议使用 GPL 版本，并把 `ffmpeg.exe` 单独复制到这里。

普通用户不需要这些：直接下载 Releases 中的
`suibu-cut_v1.1.0_portable.zip`，解压后就包含 `bin\ffmpeg.exe` 和精简 Python 运行时。
